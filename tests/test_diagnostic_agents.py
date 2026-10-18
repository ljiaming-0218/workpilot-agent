"""Exercise the intelligent handoff and final-answer filtering without a model server."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import httpx
from openai import APITimeoutError
from sqlalchemy.orm import Session

from backend.agent.diagnostic_agents import DiagnosticAgents, review_claim
from backend.agent.planner import Planner
from backend.agent.risk import RiskChecker
from backend.agent.router import IntentRouter
from backend.config import Settings
from backend.schemas.agent import (AgentTaskResult, DiagnosticClaim, InvestigatorDecision,
                                   SemanticReview, VerifiedClaim, VerifierReport)
from backend.services import multi_agent_service as multi
from backend.services.llm_service import LLMRequestTimeoutError, LLMService, LLMServiceError
from backend.services.multi_agent_runtime import ToolBudget
from backend.tools import create_default_registry
from backend.utils.trace_context import emit_llm_trace


def test_independent_model_requests_do_not_wait_on_a_global_lock():
    barrier = Barrier(2)

    class ConcurrentModel:
        def generate_structured(self, *args):
            barrier.wait(timeout=3)
            return InvestigatorDecision(summary="no evidence", claims=[], gaps=[],
                                        next_tool="stop", reason="no evidence")

    agents = DiagnosticAgents(ConcurrentModel())
    def decide(index):
        task = AgentTaskResult(task_id=f"t{index}", parent_task_id="supervisor",
                               agent_id=f"a{index}", service=f"s{index}-service",
                               status="failed", stop_reason="not_started")
        return agents.decide("query", task, [], 0, [], ToolBudget(1), [])

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(item.next_tool == "stop" for item in pool.map(decide, [1, 2]))


class SourceClient:
    def call_tool(self, name, arguments):
        if name == "search_knowledge":
            return {"items": [], "total": 0}
        assert name == "query_error_logs"
        service = arguments["service_name"]
        return {"items": [{"id": 1 if service == "order-service" else 2,
                           "service_name": service, "level": "ERROR", "error_type": "TIMEOUT",
                           "message": "queue_capacity=0; restoring queue_capacity=32 recovered the service.",
                           "stack_trace": None, "request_id": "shared-request",
                           "created_at": "2026-10-16T00:00:00Z"}], "total": 1}


class ScriptedModel:
    def __init__(self):
        self.inputs = []

    def generate_structured(self, system_prompt, user_prompt, response_model):
        payload = json.loads(user_prompt)
        self.inputs.append((response_model.__name__, payload))
        emit_llm_trace(model="scripted", response_schema=response_model.__name__,
                       latency_ms=1, status="success", error=None, tokens={"total": 10})
        if response_model is SemanticReview:
            return SemanticReview(claims=[{
                "claim_id": claim["claim_id"],
                "support": "unsupported" if "内存泄漏" in claim["text"] else "supported",
                "reason": "检查引用记录中的配置和恢复描述。",
            } for claim in payload["claims"]])
        service = payload["service"]
        reference = payload["observations"][0]["evidence_id"]
        more = service == "order-service" and len(payload["observations"]) == 1
        return InvestigatorDecision(
            summary="这段未核验摘要不能直接显示。",
            claims=[
                DiagnosticClaim(claim_type="fact", text="配置 queue_capacity=0 阻止请求入队。", evidence_ids=[reference]),
                DiagnosticClaim(claim_type="recommendation", text="恢复 queue_capacity=32 并验证请求链路。", evidence_ids=[reference]),
                DiagnosticClaim(claim_type="hypothesis", text="内存泄漏导致超时。", evidence_ids=[reference]),
            ], gaps=[], next_tool="search_knowledge" if more else "stop",
            reason="核对手册中的配置建议。" if more else "已有具体配置与恢复证据，停止检索。",
        )


@pytest.mark.parametrize("review_error", [None, LLMRequestTimeoutError, LLMServiceError])
def test_two_services_make_independent_decisions_and_review_controls_the_answer(monkeypatch, review_error):
    events = []

    class Trace:
        def __init__(self, *args): pass
        def record_multi_event(self, **event): events.append(event)
        def persist(self, session): pass

    monkeypatch.setattr(multi, "TraceRecorder", Trace)
    parent = MagicMock(spec=Session)
    parent.get.return_value = SimpleNamespace()

    def factory():
        session = MagicMock(spec=Session)
        session.__enter__.return_value = session
        return session

    query = "分析 order-service 和 inventory-service 的超时故障"
    routing, planned = multi.prepare_multi_scope(query, IntentRouter(None), Planner(None))
    model = ScriptedModel()
    original_generate = model.generate_structured
    def generate(system, user, schema):
        if schema is SemanticReview and review_error:
            raise review_error("controlled review failure")
        return original_generate(system, user, schema)
    model.generate_structured = generate
    result = multi.execute_multi_agent_run(
        parent, create_default_registry(mcp_client=SourceClient()), RiskChecker(),
        41, "request-41", query, routing, planned, session_factory=factory,
        agents=DiagnosticAgents(model),
    )
    assert result.analysis_source == "llm"
    if review_error:
        assert result.verifier.status == ("timeout" if review_error is LLMRequestTimeoutError else "failed")
        assert len(result.evidence) == 3 and len(result.agent_tasks) == 2
        assert result.verifier.claims == [] and result.analysis.evidence_steps == []
        assert "本次不输出已核验事实" in result.final_answer
        assert "配置 queue_capacity=0 阻止请求入队" not in result.final_answer
        assert "已检索的原始记录（未完成语义核验）" in result.final_answer
        assert "queue_capacity=32" in result.final_answer
        assert "建议的后续检查" in result.final_answer
        return
    assert result.steps_executed == 3  # A chose knowledge, B stopped; neither queried tickets.
    assert result.model_budget == {"used": 4, "limit": 7}
    assert all(task.model_source == "llm" for task in result.agent_tasks)
    assert "queue_capacity=0" in result.final_answer and "queue_capacity=32" in result.final_answer
    assert "内存泄漏" not in result.final_answer
    assert "这段未核验摘要" not in result.final_answer
    decisions = [payload for name, payload in model.inputs if name == "InvestigatorDecision"]
    assert all(item["service"] == payload["service"] for payload in decisions for item in payload["observations"])
    assert all(payload["available_evidence_ids"] == [item["evidence_id"] for item in payload["observations"]]
               for payload in decisions)
    assert sum(event["event_type"] == "llm_call" for event in events) == 4
    assert result.verifier.claims and any(claim.support == "unsupported" for claim in result.verifier.claims)
    review_input = next(payload for name, payload in model.inputs if name == "SemanticReview")
    ledger = {item["evidence_id"]: item for item in review_input["evidence_ledger"]}
    assert len(ledger) == len(review_input["evidence_ledger"])
    for claim in review_input["claims"]:
        assert set(claim["evidence_ids"]) <= ledger.keys()
        assert "cited_evidence" not in claim


def test_answer_separates_supported_causes_from_unverified_hypotheses():
    claims = [VerifiedClaim(claim_id=str(index), claim_type="hypothesis", text=text,
                            evidence_ids=["e1"], support=support, reason="record comparison")
              for index, (text, support) in enumerate([
                  ("supported cause", "supported"),
                  ("unproven possibility", "unverified"),
                  ("rejected cause", "unsupported"),
              ])]
    answer = multi._answer([], "summary", VerifierReport(status="completed", claims=claims), semantic=True)
    supported_section, unverified_section = answer.split("待验证假设：")
    assert "证据支持的原因分析：" in supported_section
    assert "supported cause" in supported_section and "unproven possibility" not in supported_section
    assert "unproven possibility" in unverified_section and "supported cause" not in unverified_section
    assert "rejected cause" not in answer
    assert all(claim.claim_type == "hypothesis" for claim in claims)


def test_ticket_identity_is_compared_without_overriding_semantic_review():
    class Model:
        payload = None

        def generate_structured(self, system, user, schema):
            self.payload = json.loads(user)
            return SemanticReview(claims=[{"claim_id": "c1", "support": "unverified",
                                          "reason": "Current incident is not established."}])

    title = "Ticket for edge-service"
    claim = DiagnosticClaim(claim_id="c1", claim_type="fact",
                            text=f"{title} records a failure of stock-service.", evidence_ids=["t1"])
    task = AgentTaskResult(task_id="t", parent_task_id="supervisor", agent_id="a",
                           service="edge-service", status="completed", stop_reason="done", findings=[claim])
    model = Model()
    report = DiagnosticAgents(model).verify([task], [{"evidence_id": "t1", "output": {
        "items": [{"id": 7, "title": title, "content": "stock-service failed"}]}}], ToolBudget(1), [])
    checks = model.payload["claims"][0]["record_identity_checks"]
    assert checks == [{"evidence_id": "t1", "record_id": 7, "title": title,
                       "exact_record_title_present_in_claim": True}]
    assert report.claims[0].support == "unverified"


def test_investigator_cannot_cite_another_tasks_evidence():
    class InvalidModel:
        def generate_structured(self, *args):
            return InvestigatorDecision(summary="summary", claims=[DiagnosticClaim(
                claim_type="fact", text="a cause", evidence_ids=["another-task"]
            )], next_tool="stop", reason="done")

    task = AgentTaskResult(task_id="service-1", parent_task_id="supervisor", agent_id="investigator-1",
                           service="order-service", status="completed", stop_reason="done")
    with pytest.raises(LLMServiceError, match="outside its task"):
        DiagnosticAgents(InvalidModel()).decide("query", task, [], 0, [], ToolBudget(2), [])


def test_one_successful_working_value_does_not_prove_a_minimum():
    claim = DiagnosticClaim(claim_type="recommendation", text="确保容量不低于 32。", evidence_ids=["log-1"])
    ledger = {"log-1": {"output": {"message": "Restoring queue_capacity=32 recovered the service."}}}
    verdict = SimpleNamespace(support="supported", reason="恢复有效。")
    assert review_claim(claim, verdict, ledger).support == "unsupported"
    claim.text = "核对配置是否为 32，并验证恢复情况。"
    assert review_claim(claim, verdict, ledger).support == "supported"


@pytest.mark.parametrize("text", ["恢复容量至32或更高。", "Restore to 32 or higher.", "调整到32及以上。", "调整为32或更低。"])
def test_suffix_bounds_cannot_be_invented(text):
    claim = DiagnosticClaim(claim_type="recommendation", text=text, evidence_ids=["e1"])
    ledger = {"e1": {"output": {"message": "Restoring capacity=32 recovered the service."}}}
    assert review_claim(claim, SimpleNamespace(support="supported", reason="ok"), ledger).support == "unsupported"


def test_uncited_recommendation_is_rejected_with_the_actual_validation_location(caplog):
    payload = {"summary": "Needs more evidence", "claims": [
        {"claim_type": "recommendation", "text": "Check request traces", "evidence_ids": []}
    ], "gaps": [], "next_tool": "stop", "reason": "done"}
    completion = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=json.dumps(payload), refusal=None))], usage=None)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: completion)))
    settings = Settings(mysql_user="test", mysql_password="test", llm_api_key="test", llm_model="test")
    with pytest.raises(LLMServiceError, match="schema validation"):
        LLMService(settings, client=client).generate_structured("prompt", "query", InvestigatorDecision)
    assert "claims.0.evidence_ids" in caplog.text and "too_short" in caplog.text


def test_provider_timeout_remains_identifiable_by_the_verifier():
    client = MagicMock()
    client.chat.completions.create.side_effect = APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))
    settings = Settings(mysql_user="test", mysql_password="test", llm_api_key="test", llm_model="test")
    with pytest.raises(LLMRequestTimeoutError) as error:
        LLMService(settings, client=client).generate_structured("prompt", "query", SemanticReview)
    assert isinstance(error.value.__cause__, APITimeoutError)
