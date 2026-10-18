"""Bounded read-only investigator execution with branch-owned Sessions."""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from threading import Event, Lock
from time import monotonic, perf_counter_ns
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.agent.diagnostic_agents import DiagnosticAgents
from backend.mcp.client import MCPTransientConnectionError
from backend.models.enums import RiskLevel
from backend.schemas.agent import AgentTaskResult
from backend.services.llm_service import LLMServiceError
from backend.tools.base import ToolContext
from backend.tools.registry import ToolExecutionError, ToolRegistry


MAX_TOOL_ATTEMPTS = 9
MAX_AGENT_ATTEMPTS = 3
TASK_DEADLINE_SECONDS = 90
RUN_DEADLINE_SECONDS = 180
SessionFactory = Callable[[], Session]


@dataclass(slots=True)
class ToolBudget:
    limit: int = MAX_TOOL_ATTEMPTS
    _used: int = 0
    _lock: Lock = field(default_factory=Lock)

    def claim(self) -> bool:
        with self._lock:
            if self._used >= self.limit:
                return False
            self._used += 1
            return True

    @property
    def used(self) -> int:
        with self._lock:
            return self._used


@dataclass(slots=True)
class InvestigationOutcome:
    task: AgentTaskResult
    observations: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    events: list[dict[str, Any]]


class InvestigationEvents(list):
    """Buffer locally and send immutable event snapshots to the parent queue."""

    def __init__(self, sink=None):
        super().__init__()
        self.sink = sink

    def append(self, event):
        super().append(event)
        if self.sink is not None:
            self.sink(deepcopy(event))


def investigate_service(
    session_factory: SessionFactory,
    registry: ToolRegistry,
    run_id: int,
    service: str,
    index: int,
    budget: ToolBudget,
    stop_event: Event,
    *,
    followup: bool = False,
    local_limit: int = MAX_AGENT_ATTEMPTS,
    run_deadline: float | None = None,
    retry_enabled: bool = True,
    agents: DiagnosticAgents | None = None,
    query: str = "",
    model_budget: ToolBudget | None = None,
    prior_evidence: list[dict[str, Any]] | None = None,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
) -> InvestigationOutcome:
    """Own all mutable branch state; return events for parent-only Trace writes."""
    task_id = "followup-1" if followup else f"service-{index}"
    agent_id = f"investigator-{index}"
    task = AgentTaskResult(
        task_id=task_id, parent_task_id="supervisor", agent_id=agent_id,
        service=service, status="failed", stop_reason="not_started",
    )
    observations: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = InvestigationEvents(event_sink)
    deadline = min(monotonic() + TASK_DEADLINE_SECONDS, run_deadline or float("inf"))
    next_tool: str | None = "query_error_logs"
    decision_reason: str | None = None
    _event(events, "task_started", input_json={
        "agent_id": agent_id, "task_id": task_id, "service": service,
        "followup": followup,
    })
    with session_factory() as session:
        while next_tool is not None and task.attempts_used < local_limit:
            if stop_event.is_set():
                task.stop_reason = "run_stopped"
                break
            if monotonic() >= deadline:
                task.stop_reason = "task_timeout"
                break
            inputs = _tool_inputs(next_tool, service, followup)
            definition = registry.get_tool(next_tool)
            if definition.risk_level != RiskLevel.LOW or not definition.enabled:
                raise ValueError("Investigator selected a tool outside LOW read-only permission.")
            if inputs.get("service_name", service) != service:
                raise ValueError("Investigator selected an out-of-scope service.")
            step_id = f"{task_id}-{len(steps) + 1}"
            decision = {"step_id": step_id, "tool": next_tool, "inputs": inputs,
                        "reason": decision_reason or _reason(next_tool, followup)}
            task.decisions.append(decision)
            steps.append({"tool": next_tool, "inputs": inputs})
            _event(events, "tool_selected", output_json={
                "agent_id": agent_id, "task_id": task_id, **decision,
            })
            tool_failed = False
            while True:
                if stop_event.is_set() or monotonic() >= deadline:
                    task.stop_reason = "run_stopped" if stop_event.is_set() else "task_timeout"
                    tool_failed = True
                    next_tool = None
                    break
                if task.attempts_used >= local_limit or not budget.claim():
                    task.stop_reason = "budget_exhausted"
                    tool_failed = True
                    next_tool = None
                    break
                task.attempts_used += 1
                attempt = 1 + sum(
                    1 for item in task.attempts if item["step_id"] == step_id
                )
                attempt_id = f"{step_id}-attempt-{attempt}"
                try:
                    output = registry.call_tool(
                        next_tool, inputs, ToolContext(session, run_id),
                    )
                except ToolExecutionError as exc:
                    if isinstance(exc.__cause__, SQLAlchemyError):
                        raise
                    cause = exc.__cause__
                    error_code = getattr(cause, "code", exc.code)
                    retryable = isinstance(cause, MCPTransientConnectionError)
                    task.attempts.append({
                        "step_id": step_id, "attempt_id": attempt_id,
                        "status": "failed", "error": error_code,
                    })
                    _event(events, "tool_failed", input_json={
                        "agent_id": agent_id, "task_id": task_id,
                        "tool": next_tool, "inputs": inputs,
                        "step_id": step_id, "attempt_id": attempt_id,
                    }, error=error_code)
                    if retry_enabled and retryable and attempt == 1 and task.attempts_used < local_limit:
                        # Timeout/outcome-unknown errors are intentionally excluded.
                        continue
                    task.errors.append(error_code)
                    tool_failed = True
                    break
                data = output.model_dump(mode="json")
                if not records_match_scope(next_tool, service, data):
                    error_code = "SERVICE_SCOPE_MISMATCH"
                    task.errors.append(error_code)
                    task.attempts.append({
                        "step_id": step_id, "attempt_id": attempt_id,
                        "status": "failed", "error": error_code,
                    })
                    _event(events, "tool_rejected", input_json={
                        "agent_id": agent_id, "task_id": task_id,
                        "tool": next_tool, "inputs": inputs,
                        "step_id": step_id, "attempt_id": attempt_id,
                    }, error=error_code)
                    tool_failed = True
                    break
                total = data.get("total")
                result_status = "empty_success" if total == 0 else "success_with_data"
                evidence_id = f"{run_id}:{task_id}:{step_id}:{attempt}"
                observation = {
                    "step": 0, "evidence_id": evidence_id,
                    "agent_id": agent_id, "task_id": task_id, "service": service,
                    "tool": next_tool, "inputs": inputs,
                    "attempt": attempt, "attempt_id": attempt_id,
                    "result_status": result_status, "source": "tool_registry",
                    "source_record_ids": [
                        item.get("id", item.get("document_id"))
                        for item in data.get("items", []) if isinstance(item, dict)
                    ],
                    "output": data,
                }
                observations.append(observation)
                task.evidence_ids.append(evidence_id)
                task.attempts.append({
                    "step_id": step_id, "attempt_id": attempt_id,
                    "status": "completed", "error": None,
                })
                _event(events, "tool_completed", input_json={
                    "agent_id": agent_id, "task_id": task_id,
                    "tool": next_tool, "inputs": inputs,
                    "step_id": step_id, "attempt_id": attempt_id,
                }, output_json={
                    "evidence_id": evidence_id, "result_status": result_status,
                    "total": total,
                })
                if next_tool == "query_error_logs" and total == 0 and not followup:
                    task.gaps.append("logs_empty_in_initial_window")
                break
            if agents is not None and model_budget is not None:
                used_tools = {step["tool"] for step in steps}
                allowed = [tool for tool in ("search_tickets", "search_knowledge") if tool not in used_tools]
                remaining = 0 if followup else min(local_limit-task.attempts_used, budget.limit-budget.used)
                # An initial empty window needs one of the same three attempts
                # for the Supervisor's wider-window query, not a fourth attempt.
                reserve_followup = not followup and "logs_empty_in_initial_window" in task.gaps
                if reserve_followup:
                    remaining = max(0, remaining - 1)
                try:
                    decision = agents.decide(
                        query, task, [*(prior_evidence or []), *observations],
                        remaining, allowed, model_budget, events,
                    )
                except LLMServiceError:
                    task.model_source = "failed"
                    task.errors.append("INVESTIGATOR_LLM_FAILED")
                    _event(events, "decision_failed", error="INVESTIGATOR_LLM_FAILED",
                           input_json={"agent_id": agent_id, "task_id": task_id})
                    if reserve_followup and remaining == 0:
                        next_tool = None
                else:
                    task.model_source = "llm"
                    task.summary, task.findings = decision.summary, decision.claims
                    task.decisions[-1]["next_tool"] = decision.next_tool
                    task.decisions[-1]["next_reason"] = decision.reason
                    decision_reason = decision.reason
                    retained = [gap for gap in task.gaps if gap == "logs_empty_in_initial_window"]
                    task.gaps = list(dict.fromkeys([*retained, *decision.gaps]))
                    _event(events, "investigation_decision", output_json={
                        "agent_id": agent_id, "task_id": task_id,
                        **decision.model_dump(mode="json"),
                    })
                    next_tool = None if decision.next_tool == "stop" else decision.next_tool
                    continue
            if followup or next_tool is None:
                next_tool = None
            elif tool_failed:
                next_tool = _next_tool(next_tool, failed=True, total=None)
            else:
                next_tool = _next_tool(next_tool, failed=False, total=total)
    if task.stop_reason == "not_started":
        task.stop_reason = (
            "budget_exhausted" if next_tool is not None else
            "partial_failure" if task.errors else "investigation_complete"
        )
    incomplete = task.stop_reason in {"budget_exhausted", "task_timeout", "run_stopped"}
    task.status = "partial" if (task.errors or incomplete) and observations else (
        "completed" if observations else "failed"
    )
    _event(events, "task_finished", output_json=task.model_dump(mode="json"))
    return InvestigationOutcome(task, observations, steps, events)


def records_match_scope(tool: str, service: str, output: dict[str, Any]) -> bool:
    if tool not in {"query_error_logs", "search_tickets"}:
        return True
    return all(
        isinstance(row, dict) and row.get("service_name") == service
        for row in output.get("items", [])
    )


def _event(
    events: list[dict[str, Any]], event_type: str, *,
    input_json: dict[str, Any] | None = None,
    output_json: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    events.append({"node": "service_investigator", "event_type": event_type,
                   "input_json": input_json, "output_json": output_json,
                   "error": error, "occurred_ns": perf_counter_ns()})


def _tool_inputs(tool: str, service: str, followup: bool) -> dict[str, Any]:
    if tool == "query_error_logs":
        return {"service_name": service, "minutes": 1_440 if followup else 60,
                "limit": 20}
    if tool == "search_tickets":
        return {"service_name": service, "limit": 10}
    return {"query": f"{service} 故障排查", "limit": 5}


def _reason(tool: str, followup: bool) -> str:
    if followup:
        return "初始时间窗日志为空，扩大到 24 小时验证缺口"
    return {
        "query_error_logs": "先检查本服务近期错误日志",
        "search_tickets": "核对本服务历史工单",
        "search_knowledge": "前序证据不足，检索只读知识",
    }[tool]


def _next_tool(tool: str, *, failed: bool, total: int | None) -> str | None:
    if tool == "query_error_logs":
        return "search_tickets"
    if tool == "search_tickets" and (failed or total == 0):
        return "search_knowledge"
    return None
