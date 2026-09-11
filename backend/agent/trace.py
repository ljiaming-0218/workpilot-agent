"""Collect, persist, and read ordered Agent execution trace events."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from inspect import signature
from time import perf_counter_ns
from typing import Any

from langgraph.errors import GraphInterrupt
from langgraph.runtime import Runtime
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.agent.state import AgentContext, AgentState
from backend.models.agent_run import AgentRun
from backend.models.enums import TraceStatus
from backend.models.trace_event import AgentTraceEvent
from backend.schemas.trace import AgentTraceEventRead, AgentTraceRead


NodeFunction = Callable[..., dict[str, object]]
MCP_TOOL_NAMES = frozenset({"search_tickets", "query_error_logs", "search_knowledge"})


@dataclass(slots=True)
class PendingTraceEvent:
    step: int
    node: str
    event_type: str
    latency_ms: int
    status: TraceStatus
    intent: str | None = None
    tool_name: str | None = None
    input_json: dict[str, Any] | None = None
    output_json: dict[str, Any] | None = None
    tool_input: dict[str, Any] | None = None
    tool_output_summary: dict[str, Any] | None = None
    sql: str | None = None
    sql_guard_result: dict[str, Any] | None = None
    retrieval_result: dict[str, Any] | None = None
    model: str | None = None
    tokens_json: dict[str, int] | None = None
    error: str | None = None


class TraceRecorder:
    """Buffer trace events without changing a node's transaction boundary."""

    def __init__(self, session: Session, agent_run_id: int) -> None:
        last_step = session.scalar(
            select(func.max(AgentTraceEvent.step)).where(
                AgentTraceEvent.agent_run_id == agent_run_id
            )
        )
        self.agent_run_id = agent_run_id
        self._next_step = (last_step or 0) + 1
        self._events: list[PendingTraceEvent] = []
        self._active_node = "llm"
        self._active_intent: str | None = None

    @contextmanager
    def node_scope(self, node: str, state: AgentState) -> Iterator[None]:
        previous_node = self._active_node
        previous_intent = self._active_intent
        self._active_node = node
        self._active_intent = state.get("intent")
        try:
            yield
        finally:
            self._active_node = previous_node
            self._active_intent = previous_intent

    def record_node(
        self,
        node: str,
        state: AgentState,
        output: dict[str, object] | None,
        *,
        latency_ms: int,
        status: TraceStatus,
        error: str | None = None,
    ) -> None:
        if node == "tool_executor" and output is not None:
            self._record_tool_events(state, output, latency_ms)
        event_type = {
            TraceStatus.SUCCESS: "node_completed",
            TraceStatus.FAILED: "node_failed",
            TraceStatus.INTERRUPTED: "node_interrupted",
        }[status]
        self._append(
            node=node,
            event_type=event_type,
            intent=(output or {}).get("intent") or state.get("intent"),
            input_json=_node_input_summary(node, state),
            output_json=_node_output_summary(node, output),
            latency_ms=latency_ms,
            status=status,
            error=error or _string_value((output or {}).get("error")),
        )

    def record_llm(
        self,
        *,
        model: str,
        response_schema: str,
        latency_ms: int,
        status: str,
        error: str | None,
        tokens: dict[str, int] | None,
    ) -> None:
        trace_status = TraceStatus.SUCCESS if status == "success" else TraceStatus.FAILED
        self._append(
            node=self._active_node,
            event_type="llm_call",
            intent=self._active_intent,
            input_json={"response_schema": response_schema},
            model=model,
            tokens_json=tokens,
            latency_ms=latency_ms,
            status=trace_status,
            error=error,
        )

    def record_sql(
        self,
        *,
        sql: str,
        guard_result: dict[str, Any],
        latency_ms: int,
        status: str,
        error: str | None,
    ) -> None:
        trace_status = TraceStatus.SUCCESS if status == "success" else TraceStatus.FAILED
        self._append(
            node="sql_guard",
            event_type="sql_guard_checked",
            intent=self._active_intent,
            sql=sql,
            sql_guard_result=guard_result,
            latency_ms=latency_ms,
            status=trace_status,
            error=error,
        )

    def persist(self, session: Session) -> None:
        session.add_all(
            AgentTraceEvent(agent_run_id=self.agent_run_id, **asdict(event))
            for event in self._events
        )
        self._events.clear()

    def _record_tool_events(
        self,
        state: AgentState,
        output: dict[str, object],
        latency_ms: int,
    ) -> None:
        step_index = state.get("current_step", 0)
        plan = state.get("plan", [])
        plan_step = plan[step_index] if step_index < len(plan) else {}
        tool_name = _string_value(plan_step.get("tool"))
        tool_input = plan_step.get("inputs")
        tool_input = tool_input if isinstance(tool_input, dict) else None
        error = _string_value(output.get("error"))
        raw_tool_output = _latest_tool_output(state, output)
        summary = _tool_output_summary(raw_tool_output)
        status = TraceStatus.FAILED if error else TraceStatus.SUCCESS
        self._append(
            node="tool_executor",
            event_type="tool_failed" if error else "tool_completed",
            intent=state.get("intent"),
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output_summary=summary,
            latency_ms=latency_ms,
            status=status,
            error=error,
        )
        if tool_name in MCP_TOOL_NAMES:
            self._append(
                node="mcp_client",
                event_type="mcp_call_failed" if error else "mcp_call_completed",
                intent=state.get("intent"),
                tool_name=tool_name,
                tool_input=tool_input,
                tool_output_summary=summary,
                latency_ms=latency_ms,
                status=status,
                error=error,
            )
        if tool_name == "search_knowledge" and isinstance(raw_tool_output, dict):
            self._append(
                node="retrieval",
                event_type="retrieval_completed",
                intent=state.get("intent"),
                tool_name=tool_name,
                retrieval_result=_retrieval_summary(raw_tool_output),
                latency_ms=latency_ms,
                status=status,
                error=error,
            )

    def _append(self, **values: Any) -> None:
        self._events.append(PendingTraceEvent(step=self._next_step, **values))
        self._next_step += 1


def trace_node(node_name: str, node: NodeFunction) -> NodeFunction:
    """Wrap one LangGraph node and record success, failure, or interruption."""

    accepts_runtime = len(signature(node).parameters) > 1

    def wrapped(
        state: AgentState,
        runtime: Runtime[AgentContext],
    ) -> dict[str, object]:
        recorder = runtime.context.trace_recorder
        started_ns = perf_counter_ns()
        try:
            with recorder.node_scope(node_name, state):
                output = node(state, runtime) if accepts_runtime else node(state)
        except GraphInterrupt:
            recorder.record_node(
                node_name,
                state,
                None,
                latency_ms=_latency_ms(started_ns),
                status=TraceStatus.INTERRUPTED,
            )
            raise
        except Exception as exc:
            recorder.record_node(
                node_name,
                state,
                None,
                latency_ms=_latency_ms(started_ns),
                status=TraceStatus.FAILED,
                error=type(exc).__name__,
            )
            raise

        status = TraceStatus.FAILED if output.get("error") else TraceStatus.SUCCESS
        recorder.record_node(
            node_name,
            state,
            output,
            latency_ms=_latency_ms(started_ns),
            status=status,
        )
        return output

    wrapped.__name__ = f"traced_{node_name}"
    return wrapped


def get_agent_trace(session: Session, run_id: int) -> AgentTraceRead | None:
    run = session.get(AgentRun, run_id)
    if run is None:
        return None
    events = session.scalars(
        select(AgentTraceEvent)
        .where(AgentTraceEvent.agent_run_id == run_id)
        .order_by(AgentTraceEvent.step, AgentTraceEvent.id)
    )
    return AgentTraceRead(
        run_id=run.id,
        request_id=run.request_id,
        run_status=run.status,
        events=[AgentTraceEventRead.model_validate(event) for event in events],
    )


def _node_input_summary(node: str, state: AgentState) -> dict[str, Any]:
    if node == "intent_router":
        return {"query": state.get("query")}
    if node == "planner":
        return {"query": state.get("query"), "intent": state.get("intent")}
    if node == "risk_checker":
        return {"intent": state.get("intent"), "plan": state.get("plan", [])}
    if node == "approval_gate":
        return {
            "risk_level": state.get("risk_level"),
            "risk_reasons": state.get("risk_reasons", []),
            "plan": state.get("plan", []),
        }
    if node == "tool_executor":
        index = state.get("current_step", 0)
        plan = state.get("plan", [])
        return {"step": index + 1, "plan_step": plan[index] if index < len(plan) else None}
    if node == "incident_analyzer":
        return {"query": state.get("query"), "evidence_count": len(state.get("evidence", []))}
    return {
        "error": state.get("error"),
        "result_count": len(state.get("tool_results", [])),
    }


def _node_output_summary(
    node: str,
    output: dict[str, object] | None,
) -> dict[str, Any] | None:
    if output is None:
        return None
    if node == "tool_executor":
        return {
            "selected_tool": output.get("selected_tool"),
            "current_step": output.get("current_step"),
            "error": output.get("error"),
        }
    if node == "answer_generator":
        answer = output.get("final_answer")
        return {"final_answer_preview": str(answer)[:500] if answer is not None else None}
    if node == "incident_analyzer":
        analysis = output.get("analysis")
        return {
            "analysis_source": output.get("analysis_source"),
            "summary": analysis.get("summary") if isinstance(analysis, dict) else None,
        }
    return dict(output)


def _latest_tool_output(
    state: AgentState,
    output: dict[str, object],
) -> dict[str, Any] | None:
    previous_count = len(state.get("tool_results", []))
    results = output.get("tool_results")
    if not isinstance(results, list) or len(results) <= previous_count:
        return None
    observation = results[-1]
    if not isinstance(observation, dict) or not isinstance(observation.get("output"), dict):
        return None
    return observation["output"]


def _tool_output_summary(output: dict[str, Any] | None) -> dict[str, Any] | None:
    if output is None:
        return None
    summary: dict[str, Any] = {"keys": sorted(output)}
    for key in ("total", "row_count", "simulated"):
        if key in output:
            summary[key] = output[key]
    if isinstance(output.get("items"), list):
        summary["item_count"] = len(output["items"])
    if "ticket" in output:
        ticket = output["ticket"]
        summary["ticket_found"] = ticket is not None
        if isinstance(ticket, dict):
            summary["ticket_id"] = ticket.get("id")
    return summary


def _retrieval_summary(output: dict[str, Any]) -> dict[str, Any]:
    items = output.get("items")
    if not isinstance(items, list):
        items = []
    return {
        "total": output.get("total", 0),
        "hits": [
            {"document_id": item.get("document_id"), "score": item.get("score")}
            for item in items
            if isinstance(item, dict)
        ],
    }


def _string_value(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _latency_ms(started_ns: int) -> int:
    return max(0, (perf_counter_ns() - started_ns) // 1_000_000)
