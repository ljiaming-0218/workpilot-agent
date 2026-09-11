"""Persist, interrupt, and resume one synchronous Agent run."""

import logging
from time import perf_counter_ns
from uuid import uuid4

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.agent.analyzer import IncidentAnalyzer
from backend.agent.planner import Planner
from backend.agent.risk import RiskChecker
from backend.agent.router import IntentRouter
from backend.agent.state import AgentContext, AgentState
from backend.agent.trace import TraceRecorder
from backend.models.agent_run import AgentRun
from backend.models.common import utc_now
from backend.models.enums import AgentRunStatus
from backend.schemas.agent import AgentResult, ApprovalAction
from backend.tools.registry import ToolRegistry
from backend.utils.trace_context import bind_trace_sink


logger = logging.getLogger(__name__)
WAITING_MESSAGE = (
    "\u9ad8\u98ce\u9669\u8ba1\u5212\u6b63\u5728\u7b49\u5f85\u4eba\u5de5\u5ba1\u6279\uff0c"
    "\u5c1a\u672a\u6267\u884c\u4efb\u4f55\u5de5\u5177\u3002"
)


class AgentExecutionError(RuntimeError):
    code = "AGENT_EXECUTION_ERROR"


class AgentRunNotFoundError(RuntimeError):
    code = "AGENT_RUN_NOT_FOUND"


class AgentRunStateError(RuntimeError):
    code = "AGENT_RUN_STATE_ERROR"


def run_agent(
    session: Session,
    graph: CompiledStateGraph,
    registry: ToolRegistry,
    intent_router: IntentRouter,
    planner: Planner,
    incident_analyzer: IncidentAnalyzer,
    risk_checker: RiskChecker,
    query: str,
) -> AgentResult:
    """Create a run and either finish it or persist its interrupted state."""
    started_ns = perf_counter_ns()
    request_id = uuid4().hex
    run = AgentRun(
        request_id=request_id,
        user_query=query,
        status=AgentRunStatus.RUNNING,
    )
    session.add(run)
    session.flush()
    run_id = run.id
    session.commit()

    initial_state: AgentState = {
        "query": query,
        "plan": [],
        "current_step": 0,
        "max_steps": planner.max_steps,
        "tool_results": [],
        "evidence": [],
        "risk_level": "LOW",
        "requires_approval": False,
        "risk_reasons": [],
        "error": None,
    }
    config = _graph_config(request_id)
    trace_recorder = TraceRecorder(session, run_id)
    context = _graph_context(
        session,
        registry,
        run_id,
        intent_router,
        planner,
        incident_analyzer,
        risk_checker,
        trace_recorder,
    )
    try:
        with bind_trace_sink(trace_recorder):
            final_state = graph.invoke(initial_state, config=config, context=context)
    except Exception as exc:
        _finish_run(
            session,
            run_id,
            status=AgentRunStatus.FAILED,
            intent=None,
            final_answer=None,
            latency_ms=_latency_ms(started_ns),
            trace_recorder=trace_recorder,
        )
        _delete_checkpoint(graph, request_id)
        raise AgentExecutionError("Agent graph execution failed.") from exc

    if _is_interrupted(final_state):
        _mark_waiting(
            session,
            run_id,
            intent=final_state.get("intent"),
            latency_ms=_latency_ms(started_ns),
            trace_recorder=trace_recorder,
        )
        return _build_result(
            run_id,
            request_id,
            final_state,
            status=AgentRunStatus.WAITING_APPROVAL,
            final_answer=WAITING_MESSAGE,
        )

    status = _terminal_status(final_state)
    final_answer = final_state.get("final_answer", "")
    _finish_run(
        session,
        run_id,
        status=status,
        intent=final_state.get("intent"),
        final_answer=final_answer,
        latency_ms=_latency_ms(started_ns),
        trace_recorder=trace_recorder,
    )
    _delete_checkpoint(graph, request_id)
    return _build_result(
        run_id,
        request_id,
        final_state,
        status=status,
        final_answer=final_answer,
    )


def resume_agent(
    session: Session,
    graph: CompiledStateGraph,
    registry: ToolRegistry,
    intent_router: IntentRouter,
    planner: Planner,
    incident_analyzer: IncidentAnalyzer,
    risk_checker: RiskChecker,
    run_id: int,
    action: ApprovalAction,
) -> AgentResult:
    """Atomically claim one waiting run and resume its LangGraph checkpoint."""
    run = session.get(AgentRun, run_id)
    if run is None:
        raise AgentRunNotFoundError("Agent run does not exist.")
    if run.status != AgentRunStatus.WAITING_APPROVAL:
        raise AgentRunStateError("Agent run is not waiting for approval.")

    request_id = run.request_id
    config = _graph_config(request_id)
    snapshot = graph.get_state(config)
    if not snapshot.values or not snapshot.interrupts:
        raise AgentRunStateError("The approval checkpoint is no longer available.")

    claimed = session.execute(
        update(AgentRun)
        .where(
            AgentRun.id == run_id,
            AgentRun.status == AgentRunStatus.WAITING_APPROVAL,
        )
        .values(status=AgentRunStatus.RUNNING, final_answer=None)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        session.rollback()
        raise AgentRunStateError("Agent run approval was already handled.")
    session.commit()

    started_ns = perf_counter_ns()
    trace_recorder = TraceRecorder(session, run_id)
    context = _graph_context(
        session,
        registry,
        run_id,
        intent_router,
        planner,
        incident_analyzer,
        risk_checker,
        trace_recorder,
    )
    try:
        with bind_trace_sink(trace_recorder):
            final_state = graph.invoke(
                Command(resume={"action": action}),
                config=config,
                context=context,
            )
    except Exception as exc:
        _finish_run(
            session,
            run_id,
            status=AgentRunStatus.FAILED,
            intent=run.intent,
            final_answer=None,
            latency_ms=(run.latency_ms or 0) + _latency_ms(started_ns),
            trace_recorder=trace_recorder,
        )
        _delete_checkpoint(graph, request_id)
        raise AgentExecutionError("Agent graph resume failed.") from exc

    if _is_interrupted(final_state):
        _mark_waiting(
            session,
            run_id,
            intent=final_state.get("intent"),
            latency_ms=(run.latency_ms or 0) + _latency_ms(started_ns),
            trace_recorder=trace_recorder,
        )
        return _build_result(
            run_id,
            request_id,
            final_state,
            status=AgentRunStatus.WAITING_APPROVAL,
            final_answer=WAITING_MESSAGE,
        )

    status = _terminal_status(final_state)
    final_answer = final_state.get("final_answer", "")
    _finish_run(
        session,
        run_id,
        status=status,
        intent=final_state.get("intent"),
        final_answer=final_answer,
        latency_ms=(run.latency_ms or 0) + _latency_ms(started_ns),
        trace_recorder=trace_recorder,
    )
    _delete_checkpoint(graph, request_id)
    return _build_result(
        run_id,
        request_id,
        final_state,
        status=status,
        final_answer=final_answer,
    )


def _graph_context(
    session: Session,
    registry: ToolRegistry,
    run_id: int,
    intent_router: IntentRouter,
    planner: Planner,
    incident_analyzer: IncidentAnalyzer,
    risk_checker: RiskChecker,
    trace_recorder: TraceRecorder,
) -> AgentContext:
    return AgentContext(
        session=session,
        registry=registry,
        agent_run_id=run_id,
        intent_router=intent_router,
        planner=planner,
        incident_analyzer=incident_analyzer,
        risk_checker=risk_checker,
        trace_recorder=trace_recorder,
    )


def _graph_config(request_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": request_id}}


def _is_interrupted(state: dict[str, object]) -> bool:
    return bool(state.get("__interrupt__"))


def _terminal_status(state: AgentState) -> AgentRunStatus:
    return AgentRunStatus.FAILED if state.get("error") else AgentRunStatus.COMPLETED


def _build_result(
    run_id: int,
    request_id: str,
    state: AgentState,
    *,
    status: AgentRunStatus,
    final_answer: str,
) -> AgentResult:
    return AgentResult(
        run_id=run_id,
        request_id=request_id,
        intent=state.get("intent", "general"),
        routing_source=state.get("routing_source", "fallback"),
        routing_confidence=state.get("routing_confidence", 0),
        plan=state.get("plan", []),
        planner_source=state.get("planner_source", "fallback"),
        steps_executed=state.get("current_step", 0),
        selected_tool=state.get("selected_tool") or None,
        final_answer=final_answer,
        tool_results=state.get("tool_results", []),
        evidence=state.get("evidence", []),
        analysis=state.get("analysis"),
        analysis_source=state.get("analysis_source"),
        risk_level=state.get("risk_level", "LOW"),
        requires_approval=state.get("requires_approval", False),
        risk_reasons=state.get("risk_reasons", []),
        status=status,
        approval_status=state.get("approval_status"),
        error=state.get("error"),
    )


def _mark_waiting(
    session: Session,
    run_id: int,
    *,
    intent: str | None,
    latency_ms: int,
    trace_recorder: TraceRecorder,
) -> None:
    run = session.get(AgentRun, run_id)
    if run is None:
        raise AgentExecutionError("Agent run disappeared before interruption.")
    run.intent = intent
    run.status = AgentRunStatus.WAITING_APPROVAL
    run.final_answer = WAITING_MESSAGE
    run.finished_at = None
    run.latency_ms = latency_ms
    trace_recorder.persist(session)
    session.commit()


def _finish_run(
    session: Session,
    run_id: int,
    *,
    status: AgentRunStatus,
    intent: str | None,
    final_answer: str | None,
    latency_ms: int,
    trace_recorder: TraceRecorder,
) -> None:
    run = session.get(AgentRun, run_id)
    if run is None:
        raise AgentExecutionError("Agent run disappeared before completion.")
    run.intent = intent
    run.status = status
    run.final_answer = final_answer
    run.finished_at = utc_now()
    run.latency_ms = latency_ms
    trace_recorder.persist(session)
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise


def _delete_checkpoint(graph: CompiledStateGraph, thread_id: str) -> None:
    checkpointer = graph.checkpointer
    if checkpointer is None:
        return
    try:
        checkpointer.delete_thread(thread_id)
    except Exception as exc:
        logger.warning("CHECKPOINT_CLEANUP_ERROR: %s", type(exc).__name__)


def _latency_ms(started_ns: int) -> int:
    return max(0, (perf_counter_ns() - started_ns) // 1_000_000)
