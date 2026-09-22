"""Persist, interrupt, and resume one synchronous Agent run."""

import logging
from collections.abc import Callable
from datetime import timedelta
from time import perf_counter_ns
from typing import Any
from uuid import uuid4

from langgraph.graph.state import CompiledStateGraph
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
from backend.models.approval_checkpoint import AgentApprovalCheckpoint
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
APPROVAL_TTL = timedelta(hours=24)
APPROVAL_STATE_KEYS = frozenset(AgentState.__annotations__)


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
    run_id, request_id = prepare_agent_run(session, query)
    return execute_agent_run(
        session,
        graph,
        registry,
        intent_router,
        planner,
        incident_analyzer,
        risk_checker,
        run_id,
        request_id,
        query,
    )


def prepare_agent_run(session: Session, query: str) -> tuple[int, str]:
    """Persist RUNNING before synchronous or background execution begins."""
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
    return run_id, request_id


def execute_agent_run(
    session: Session,
    graph: CompiledStateGraph,
    registry: ToolRegistry,
    intent_router: IntentRouter,
    planner: Planner,
    incident_analyzer: IncidentAnalyzer,
    risk_checker: RiskChecker,
    run_id: int,
    request_id: str,
    query: str,
    progress_sink: Callable[[str, dict[str, Any]], None] | None = None,
) -> AgentResult:
    """Execute one previously persisted run with request-owned resources."""
    started_ns = perf_counter_ns()

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
    trace_recorder = TraceRecorder(session, run_id, progress_sink)
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
            request_id=request_id,
            state=final_state,
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
    checkpoint = session.get(AgentApprovalCheckpoint, run_id)
    if checkpoint is None or checkpoint.request_id != request_id:
        _fail_unrecoverable_approval(session, run, "APPROVAL_CHECKPOINT_MISSING")
        raise AgentRunStateError("The approval checkpoint is no longer available.")
    if checkpoint.expires_at <= utc_now():
        _fail_unrecoverable_approval(session, run, "APPROVAL_CHECKPOINT_EXPIRED")
        raise AgentRunStateError("The approval checkpoint has expired.")
    if checkpoint.decision is not None:
        raise AgentRunStateError("The approval action was already claimed.")

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
    checkpoint.decision = action
    session.commit()

    config = _graph_config(request_id)
    _delete_checkpoint(graph, request_id)
    restored_state: AgentState = {
        **checkpoint.state_json,
        "approval_action": action,
        "resume_after_approval": True,
    }

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
            final_state = graph.invoke(restored_state, config=config, context=context)
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
            request_id=request_id,
            state=final_state,
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
    request_id: str,
    state: AgentState,
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
    checkpoint = session.get(AgentApprovalCheckpoint, run_id)
    if checkpoint is None:
        checkpoint = AgentApprovalCheckpoint(
            agent_run_id=run_id,
            request_id=request_id,
            state_json=_serializable_approval_state(state),
            expires_at=utc_now() + APPROVAL_TTL,
        )
        session.add(checkpoint)
    else:
        checkpoint.state_json = _serializable_approval_state(state)
        checkpoint.decision = None
        checkpoint.expires_at = utc_now() + APPROVAL_TTL
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
    checkpoint = session.get(AgentApprovalCheckpoint, run_id)
    if checkpoint is not None:
        session.delete(checkpoint)
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


def _serializable_approval_state(state: AgentState) -> dict[str, Any]:
    """Exclude LangGraph internals and persist only declared JSON-compatible state."""
    return {key: state[key] for key in APPROVAL_STATE_KEYS if key in state}


def _fail_unrecoverable_approval(
    session: Session,
    run: AgentRun,
    error: str,
) -> None:
    run.status = AgentRunStatus.FAILED
    run.final_answer = error
    run.finished_at = utc_now()
    checkpoint = session.get(AgentApprovalCheckpoint, run.id)
    if checkpoint is not None:
        session.delete(checkpoint)
    session.commit()


def _latency_ms(started_ns: int) -> int:
    return max(0, (perf_counter_ns() - started_ns) // 1_000_000)
