"""Deterministic W1/W2 checks without MySQL or an online model."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from dataclasses import replace
from threading import Barrier, Lock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.agent.planner import Planner
from backend.agent.evidence_verifier import EvidenceVerifier
from backend.agent.risk import RiskChecker
from backend.agent.router import IntentRouter
from backend.schemas.agent import AgentRequest, IncidentAnalysis
from backend.dependencies import get_db
from backend.main import create_app
from backend.models.enums import AgentRunStatus, RiskLevel, ToolCallStatus
from backend.mcp.client import MCPRequestTimeoutError, MCPTransientConnectionError
from backend.services.agent_service import AgentExecutionError
from backend.services import multi_agent_service as multi
from backend.tools import create_default_registry
from backend.services.run_event_service import RunEventBroker
from backend.services.multi_agent_runtime import ToolBudget
from backend.services.run_history_service import get_agent_run_detail


QUERY = "分析 order-service 和 inventory-service 最近的超时故障"


@pytest.fixture(autouse=True)
def investigation_scope(monkeypatch):
    # These tests isolate scheduler behavior using mocked sessions and tools.
    monkeypatch.setattr("backend.services.access_service.resolve_run_scope",
                        lambda session, run_id: (1, {"order-service", "inventory-service"}))


class FixtureClient:
    def __init__(self, fail_ticket: bool = False) -> None:
        self.fail_ticket = fail_ticket
        self.calls: list[tuple[str, dict]] = []

    def call_tool(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, arguments))
        service = arguments.get("service_name")
        if name == "query_error_logs":
            return {"items": [], "total": 0}
        if name == "search_tickets":
            if self.fail_ticket and service == "order-service":
                raise RuntimeError("controlled failure")
            return {"items": [], "total": 0}
        if name == "search_knowledge":
            return {"items": [], "total": 0}
        raise AssertionError(name)


class MemoryTrace:
    events: list[dict]

    def __init__(self, session, run_id, progress_sink=None):
        self.events = []

    def record_multi_event(self, **event):
        self.events.append(event)

    def persist(self, session):
        pass


def _execute(monkeypatch, *, fail_ticket=False, fixture_client=None):
    monkeypatch.setattr(multi, "TraceRecorder", MemoryTrace)
    routing, planned = multi.prepare_multi_scope(QUERY, IntentRouter(None), Planner(None))
    run = SimpleNamespace()
    session = MagicMock(spec=Session)
    session.get.return_value = run
    branch_sessions = []

    def session_factory():
        branch = MagicMock(spec=Session)
        branch.__enter__.return_value = branch
        branch_sessions.append(branch)
        return branch

    session.branch_sessions = branch_sessions
    client = fixture_client or FixtureClient(fail_ticket)
    result = multi.execute_multi_agent_run(
        session, create_default_registry(mcp_client=client), RiskChecker(),
        42, "request-42", QUERY, routing, planned,
        session_factory=session_factory,
    )
    return result, run, client, session


def test_service_tasks_have_independent_decisions_and_global_evidence(monkeypatch):
    result, run, client, session = _execute(monkeypatch)
    assert result.mode == "multi"
    assert result.status == AgentRunStatus.COMPLETED
    assert result.investigation_status == "completed"
    assert [task.service for task in result.agent_tasks] == [
        "order-service", "inventory-service",
    ]
    assert all(len(task.decisions) == 3 for task in result.agent_tasks)
    assert result.steps_executed == 6
    assert len({item["evidence_id"] for item in result.evidence}) == 6
    assert [item["step"] for item in result.evidence] == list(range(1, 7))
    assert all(item["result_status"] == "empty_success" for item in result.evidence)
    assert run.status == AgentRunStatus.COMPLETED
    assert session.commit.call_count >= 2  # Dispatch/progress persist before final completion.
    assert len(session.branch_sessions) == 2
    assert all(branch.commit.call_count >= 3 for branch in session.branch_sessions)
    assert len(client.calls) == 6


def test_one_service_tool_failure_remains_visible_and_other_service_continues(monkeypatch):
    result, run, client, session = _execute(monkeypatch, fail_ticket=True)
    first, second = result.agent_tasks
    assert first.status == "partial"
    assert first.errors == ["TOOL_EXECUTION_ERROR"]
    assert second.status == "completed"
    assert any(item["service"] == "inventory-service" for item in result.evidence)
    assert not any(
        item["tool"] == "search_tickets" and item["service"] == "order-service"
        for item in result.evidence
    )
    assert result.status == AgentRunStatus.COMPLETED
    assert result.investigation_status == "partial"
    assert any(branch.rollback.called for branch in session.branch_sessions)


def test_multi_scope_accepts_one_service_and_rejects_operation():
    routing, planned = multi.prepare_multi_scope(
        "分析 order-service 的故障", IntentRouter(None), Planner(None),
    )
    assert routing.intent == "incident_analysis"
    assert [item.service for item in planned.investigations] == ["order-service"]
    with pytest.raises(multi.MultiAgentScopeError):
        multi.prepare_multi_scope(
            "模拟重启 order-service 和 inventory-service",
            IntentRouter(None), Planner(None),
        )


def test_non_low_tool_is_rejected_before_any_call(monkeypatch):
    monkeypatch.setattr(multi, "TraceRecorder", MemoryTrace)
    routing, planned = multi.prepare_multi_scope(QUERY, IntentRouter(None), Planner(None))
    session = MagicMock(spec=Session)
    session.get.return_value = SimpleNamespace()
    client = FixtureClient()
    registry = create_default_registry(mcp_client=client)
    definition = registry.get_tool("query_error_logs")
    registry._tools["query_error_logs"] = replace(definition, risk_level=RiskLevel.HIGH)
    with pytest.raises(AgentExecutionError):
        multi.execute_multi_agent_run(
            session, registry, RiskChecker(), 42, "request-42", QUERY,
            routing, planned, session_factory=lambda: MagicMock(spec=Session),
        )
    assert client.calls == []
    assert session.get.return_value.status == AgentRunStatus.FAILED


def test_multi_http_sync_and_async_dispatch(monkeypatch):
    result, _, _, _ = _execute(monkeypatch)
    assert AgentRequest(query=QUERY).model_dump() == {"query": QUERY}
    app = create_app()
    app.dependency_overrides[get_db] = lambda: MagicMock(spec=Session)
    from backend.services.access_service import current_team
    app.dependency_overrides[current_team] = lambda: SimpleNamespace(team_id=7, user_id=9, role="member")
    monkeypatch.setattr("backend.routers.agent_router.require_requested_services", lambda *args: None)
    app.state.intent_router = IntentRouter(None)
    app.state.planner = Planner(None)
    app.state.tool_registry = MagicMock()
    app.state.agent_graph = MagicMock()
    app.state.incident_analyzer = MagicMock()
    app.state.risk_checker = RiskChecker()
    app.state.run_event_broker = RunEventBroker()
    app.state.session_factory = MagicMock()
    prepared = []
    def prepare(session, query, **kwargs):
        prepared.append(kwargs)
        return 42, "request-42"
    monkeypatch.setattr("backend.routers.agent_router.prepare_agent_run", prepare)
    executed = []
    monkeypatch.setattr(
        "backend.routers.agent_router.execute_multi_agent_run",
        lambda *args, **kwargs: executed.append(args) or result,
    )
    background_calls = []
    monkeypatch.setattr(
        "backend.routers.agent_router.execute_agent_run_background",
        lambda *args: background_calls.append(args),
    )
    specialist_calls = []
    monkeypatch.setattr(
        "backend.routers.agent_router.execute_specialist_run",
        lambda *args: specialist_calls.append(args) or result.model_copy(update={
            "mode": "multi", "agent_tasks": [result.agent_tasks[0]],
        }),
    )
    client = TestClient(app, raise_server_exceptions=True)
    response = client.post("/agent/runs", json={"query": QUERY})
    assert response.status_code == 200
    assert response.json()["data"]["mode"] == "multi"
    assert executed and executed[0][3] == 42
    accepted = client.post("/agent/runs/async", json={"query": QUERY})
    assert accepted.status_code == 202
    assert accepted.json()["data"]["run_id"] == 42
    assert prepared[-1] == {"creator_user_id": 9, "team_id": 7}
    assert background_calls[-1][11] == "multi"
    delegated = client.post(
        "/agent/runs", json={"query": "模拟重启 order-service"},
    )
    assert delegated.status_code == 200
    assert delegated.json()["data"]["mode"] == "multi"
    assert specialist_calls
    accepted_specialist = client.post(
        "/agent/runs/async", json={"query": "模拟重启 order-service"},
    )
    assert accepted_specialist.status_code == 202
    assert background_calls[-1][11] == "specialist"
    assert client.post("/agent/runs", json={"query": QUERY, "mode": "single"}).status_code == 422
    baseline_calls = []
    monkeypatch.setattr(
        "backend.routers.agent_router.run_agent",
        lambda *args, **kwargs: baseline_calls.append((args, kwargs)) or result.model_copy(update={
            "mode": "single", "agent_tasks": [],
        }),
    )
    baseline = client.post(
        "/agent/runs", json={"query": QUERY},
        headers={"X-WorkPilot-Comparison-Mode": "single"},
    )
    assert baseline.status_code == 200
    assert baseline.json()["data"]["mode"] == "single"
    assert baseline_calls


def test_workflow_specialist_keeps_approval_handoff(monkeypatch):
    from backend.services import specialist_agent_service as specialist

    events = []

    class Trace:
        def __init__(self, session, run_id, progress_sink=None):
            pass

        def record_multi_event(self, **event):
            events.append(event)

        def persist(self, session):
            pass

    monkeypatch.setattr(specialist, "TraceRecorder", Trace)
    waiting = multi.AgentResult(
        run_id=42, request_id="request-42", intent="operation",
        routing_source="rule", routing_confidence=0.99, plan=[],
        planner_source="rule", steps_executed=0, selected_tool=None,
        final_answer="waiting", tool_results=[], evidence=[], analysis=None,
        analysis_source=None, risk_level=RiskLevel.HIGH,
        requires_approval=True, risk_reasons=[],
        status=AgentRunStatus.WAITING_APPROVAL,
        approval_status="pending", error=None,
    )
    monkeypatch.setattr(specialist, "execute_agent_run", lambda *args: waiting)
    resumed = waiting.model_copy(update={
        "status": AgentRunStatus.COMPLETED, "requires_approval": False,
        "approval_status": "approved", "final_answer": "done",
    })
    monkeypatch.setattr(specialist, "resume_agent", lambda *args: resumed)
    session = MagicMock(spec=Session)
    dependencies = [session, MagicMock(), MagicMock(), MagicMock(), MagicMock(),
                    MagicMock(), MagicMock()]
    first = specialist.execute_specialist_run(*dependencies, 42, "request-42", "simulate")
    assert first.mode == "multi"
    assert first.status == AgentRunStatus.WAITING_APPROVAL
    assert first.agent_tasks[0].stop_reason == "waiting_approval"
    second = specialist.resume_specialist_run(*dependencies, 42, "approve")
    assert second.mode == "multi"
    assert second.agent_tasks[0].status == "completed"
    assert [event["event_type"] for event in events] == [
        "specialist_dispatched", "specialist_result", "specialist_result",
    ]


def test_specialist_history_preserves_mode_and_completed_tool_evidence():
    now = datetime.now(timezone.utc)
    run = SimpleNamespace(
        id=42, request_id="request-42", user_query="find ticket",
        intent="ticket_search", status=AgentRunStatus.COMPLETED,
        started_at=now, finished_at=now, latency_ms=1,
        final_answer="found",
    )
    task = {
        "task_id": "task-42-1", "parent_task_id": "run-42",
        "agent_id": "workflow_specialist", "service": "ticket_search",
        "status": "completed", "stop_reason": "completed",
    }
    events = [SimpleNamespace(
        node="supervisor", event_type="specialist_result",
        output_json={"mode": "multi", "task": task}, input_json=None,
    )]
    calls = [SimpleNamespace(
        tool_name="search_tickets", status=ToolCallStatus.COMPLETED,
        input_json={"service_name": "order-service"},
        output_json={"total": 1, "items": [{"id": 7}]},
    )]
    session = MagicMock(spec=Session)
    session.get.return_value = run
    session.scalars.side_effect = [
        SimpleNamespace(all=lambda: events), SimpleNamespace(all=lambda: calls),
    ]
    detail = get_agent_run_detail(session, 42)
    assert detail.mode == "multi"
    assert detail.agent_tasks[0]["agent_id"] == "workflow_specialist"
    assert detail.evidence == [{"tool": "search_tickets", "output": calls[0].output_json}]
    session.scalars.side_effect = [
        SimpleNamespace(all=lambda: [SimpleNamespace(
            node="supervisor", event_type="specialist_dispatched",
            output_json={"task_id": "task-42-1"}, input_json=None,
        )]),
        SimpleNamespace(all=lambda: []),
    ]
    pending = get_agent_run_detail(session, 42)
    assert pending.mode == "multi"
    assert pending.agent_tasks == []


def test_global_budget_claim_is_atomic():
    budget = ToolBudget(9)
    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = list(pool.map(lambda _: budget.claim(), range(40)))
    assert sum(claimed) == budget.used == 9


def test_identified_connection_failure_retries_same_step_once(monkeypatch):
    class TransientOnceClient(FixtureClient):
        def __init__(self):
            super().__init__()
            self.failed = False

        def call_tool(self, name, arguments):
            if name == "query_error_logs" and arguments["service_name"] == "order-service" and not self.failed:
                self.failed = True
                self.calls.append((name, arguments))
                raise MCPTransientConnectionError("injected connection reset")
            return super().call_tool(name, arguments)

    result, _, client, _ = _execute(monkeypatch, fixture_client=TransientOnceClient())
    first = result.agent_tasks[0]
    assert first.attempts[0]["status"] == "failed"
    assert first.attempts[1]["status"] == "completed"
    assert first.attempts[0]["step_id"] == first.attempts[1]["step_id"]
    assert first.attempts_used == 3
    assert result.tool_budget == {"used": 6, "limit": 9}
    assert result.service_investigations[0].status == "partial"
    assert len([call for call in client.calls if call[0] == "query_error_logs" and call[1]["service_name"] == "order-service"]) == 2


def test_outcome_unknown_timeout_is_not_retried(monkeypatch):
    class TimeoutClient(FixtureClient):
        def call_tool(self, name, arguments):
            if name == "query_error_logs" and arguments["service_name"] == "order-service":
                self.calls.append((name, arguments))
                raise MCPRequestTimeoutError("outcome unknown")
            return super().call_tool(name, arguments)

    result, _, client, _ = _execute(monkeypatch, fixture_client=TimeoutClient())
    first = result.agent_tasks[0]
    assert first.errors == ["MCP_OUTCOME_UNKNOWN"]
    assert len([call for call in client.calls if call[0] == "query_error_logs" and call[1]["service_name"] == "order-service"]) == 1


def test_supervisor_dispatches_one_nonduplicate_followup(monkeypatch):
    class FollowupClient(FixtureClient):
        def call_tool(self, name, arguments):
            if name == "search_tickets":
                service = arguments["service_name"]
                self.calls.append((name, arguments))
                return {"total": 1, "items": [{
                    "id": 1, "title": "Incident", "content": "Ticket",
                    "category": "incident", "priority": "high", "status": "open",
                    "service_name": service, "resolution": None,
                    "created_at": "2026-10-14T00:00:00Z",
                    "updated_at": "2026-10-14T00:00:00Z",
                }]}
            if name == "query_error_logs" and arguments["minutes"] == 1_440:
                self.calls.append((name, arguments))
                return {"total": 1, "items": [{
                    "id": 2, "service_name": arguments["service_name"],
                    "level": "ERROR", "error_type": "TIMEOUT",
                    "message": "Wider-window signal", "stack_trace": None,
                    "request_id": "fixed", "created_at": "2026-10-14T00:00:00Z",
                }]}
            return super().call_tool(name, arguments)

    result, _, client, session = _execute(monkeypatch, fixture_client=FollowupClient())
    assert len(result.agent_tasks) == 3
    assert result.agent_tasks[-1].task_id == "followup-1"
    assert result.agent_tasks[-1].service == "order-service"
    assert result.tool_budget == {"used": 5, "limit": 9}
    assert len([call for call in client.calls if call[0] == "query_error_logs" and call[1]["minutes"] == 1_440]) == 1
    assert len(session.branch_sessions) == 3
    assert any(
        claim.support == "supported" and "日志 ID 2" in claim.text
        for claim in result.verifier.claims
    )


def test_investigators_can_enter_tools_concurrently_with_isolated_sessions(monkeypatch):
    class ConcurrentClient(FixtureClient):
        def __init__(self):
            super().__init__()
            self.barrier = Barrier(2)
            self.lock = Lock()
            self.active = 0
            self.max_active = 0

        def call_tool(self, name, arguments):
            if name == "query_error_logs":
                with self.lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                self.barrier.wait(timeout=3)
                with self.lock:
                    self.active -= 1
            return super().call_tool(name, arguments)

    client = ConcurrentClient()
    result, _, _, session = _execute(monkeypatch, fixture_client=client)
    assert client.max_active == 2
    assert len(session.branch_sessions) == 2
    assert session.branch_sessions[0] is not session.branch_sessions[1]
    assert result.tool_budget["used"] <= result.tool_budget["limit"]


def test_saved_history_matches_concurrent_evidence_by_tool_and_input():
    now = datetime.now(timezone.utc)
    run = SimpleNamespace(
        id=42, request_id="request-42", user_query=QUERY,
        intent="incident_analysis", status=AgentRunStatus.COMPLETED,
        started_at=now, finished_at=now, latency_ms=1,
        final_answer="fixture answer",
    )
    def event(node, event_type, *, input_json=None, output_json=None):
        return SimpleNamespace(
            node=node, event_type=event_type,
            input_json=input_json, output_json=output_json,
        )
    events = [
        event("supervisor", "results_merged", output_json={
            "evidence_ids": ["42:service-1:step-1:1", "42:service-2:step-1:1"],
            "tasks": [], "investigation_status": "completed",
            "tool_budget": {"used": 2, "limit": 9},
            "verifier": {"status": "completed", "claims": [], "error": None},
        }),
        event("service_investigator", "tool_completed",
              input_json={"agent_id": "investigator-1", "task_id": "service-1",
                          "tool": "query_error_logs",
                          "inputs": {"service_name": "order-service", "minutes": 60, "limit": 20}},
              output_json={"evidence_id": "42:service-1:step-1:1", "result_status": "success_with_data"}),
        event("service_investigator", "tool_completed",
              input_json={"agent_id": "investigator-2", "task_id": "service-2",
                          "tool": "query_error_logs",
                          "inputs": {"service_name": "inventory-service", "minutes": 60, "limit": 20}},
              output_json={"evidence_id": "42:service-2:step-1:1", "result_status": "success_with_data"}),
    ]
    calls = [
        SimpleNamespace(tool_name="query_error_logs", status=ToolCallStatus.COMPLETED,
                        input_json={"service_name": service, "minutes": 60, "limit": 20,
                                    "error_type": None, "keyword": None},
                        output_json={"total": 1, "items": [{"id": record_id}]})
        for service, record_id in [("inventory-service", 22), ("order-service", 11)]
    ]
    session = MagicMock(spec=Session)
    session.get.return_value = run
    session.scalars.side_effect = [SimpleNamespace(all=lambda: events),
                                   SimpleNamespace(all=lambda: calls)]
    detail = get_agent_run_detail(session, 42)
    assert [(item["service"], item["source_record_ids"]) for item in detail.evidence] == [
        ("order-service", [11]), ("inventory-service", [22]),
    ]
    assert detail.tool_budget == {"used": 2, "limit": 9}
    assert detail.verifier.status == "completed"


def test_verifier_rejects_overreach_but_keeps_hypothesis():
    analysis = IncidentAnalysis(
        summary="证据不足", confirmed_facts=[
            "步骤 1 的 query_error_logs 返回 1 条匹配记录。",
            "超时日志证明服务发生了内存泄漏。",
        ], hypotheses=["内存泄漏可能是原因，需检查内存指标。"],
        recommended_actions=["检查内存指标。"], evidence_steps=[1],
    )
    evidence = ({
        "step": 1, "evidence_id": "42:service-1:service-1-1:1",
        "tool": "query_error_logs", "service": "order-service",
        "result_status": "success_with_data",
        "output": {"total": 1, "items": [{"id": 7, "error_type": "TIMEOUT",
                                            "service_name": "order-service"}]},
    },)
    report = EvidenceVerifier().verify(analysis, evidence)
    assert [claim.support for claim in report.claims] == [
        "supported", "unsupported", "unverified",
    ]
    assert report.claims[0].evidence_ids == ["42:service-1:service-1-1:1"]
    assert report.claims[1].evidence_ids == []


def test_verifier_checks_record_fields_not_just_evidence_id():
    analysis = IncidentAnalysis(
        summary="待核验", confirmed_facts=[
            "步骤 1 的日志 ID 7 记录错误类型 TIMEOUT。",
            "步骤 1 的日志 ID 7 记录错误类型 MEMORY_LEAK。",
        ], hypotheses=[], recommended_actions=["核对日志。"], evidence_steps=[1],
    )
    report = EvidenceVerifier().verify(analysis, ({
        "step": 1, "evidence_id": "log-7", "tool": "query_error_logs",
        "service": "order-service",
        "result_status": "success_with_data",
        "output": {"total": 1, "items": [{"id": 7, "error_type": "TIMEOUT",
                                            "service_name": "order-service"}]},
    },))
    assert [claim.support for claim in report.claims] == ["supported", "unsupported"]


def test_verifier_does_not_treat_failed_record_as_evidence():
    analysis = IncidentAnalysis(
        summary="待核验", confirmed_facts=["步骤 1 的 search_tickets 返回 0 条匹配记录。"],
        hypotheses=[], recommended_actions=["核对工单。"], evidence_steps=[1],
    )
    report = EvidenceVerifier().verify(analysis, ({
        "step": 1, "evidence_id": "failed-call", "tool": "search_tickets",
        "result_status": "failed", "output": {"total": 0},
    },))
    assert report.claims[0].support == "unsupported"


def test_verifier_timeout_is_explicit_and_does_not_erase_tool_evidence(monkeypatch):
    def timeout(*args, **kwargs):
        raise TimeoutError("injected")
    monkeypatch.setattr(EvidenceVerifier, "verify", timeout)
    result, _, _, _ = _execute(monkeypatch)
    assert result.verifier.status == "timeout"
    assert result.verifier.error == "VERIFIER_TIMEOUT"
    assert len(result.evidence) == 6
    assert "本次不输出已核验事实" in result.final_answer
    assert result.analysis.evidence_steps == []
    assert "没有通过逐条核验" in result.analysis.confirmed_facts[0]


def test_verifier_failure_is_explicit_and_does_not_certify_facts(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("injected")
    monkeypatch.setattr(EvidenceVerifier, "verify", fail)
    result, _, _, _ = _execute(monkeypatch)
    assert result.verifier.status == "failed"
    assert result.verifier.error == "VERIFIER_FAILED"
    assert result.verifier.claims == []
    assert "本次不输出已核验事实" in result.final_answer
    assert result.analysis.evidence_steps == []


def test_conflict_case_routes_to_incident_without_broadening_document_search():
    query = "调查固定窗口的冲突证据：service: order-service 出现 ORDER_TIMEOUT；核对日志、工单和手册"
    assert IntentRouter(None).route(query).intent == "incident_analysis"
    assert IntentRouter(None).route("搜索知识库中的冲突证据文档").intent == "knowledge_search"


def test_wrong_service_record_is_not_accepted_as_evidence(monkeypatch):
    class WrongServiceClient(FixtureClient):
        def call_tool(self, name, arguments):
            if name == "query_error_logs" and arguments["service_name"] == "order-service":
                self.calls.append((name, arguments))
                return {"total": 1, "items": [{
                    "id": 91, "service_name": "inventory-service",
                    "level": "ERROR", "error_type": "TIMEOUT",
                    "message": "misattributed", "stack_trace": None,
                    "request_id": "scope-test", "created_at": "2026-10-14T00:00:00Z",
                }]}
            return super().call_tool(name, arguments)

    result, _, _, _ = _execute(monkeypatch, fixture_client=WrongServiceClient())
    assert "SERVICE_SCOPE_MISMATCH" in result.agent_tasks[0].errors
    assert not any(
        item["tool"] == "query_error_logs" and item["service"] == "order-service"
        for item in result.evidence
    )
    assert result.service_investigations[0].status == "partial"
