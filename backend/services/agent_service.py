"""Persist and execute one synchronous Agent run."""

from time import perf_counter_ns
from uuid import uuid4

from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.agent.analyzer import IncidentAnalyzer
from backend.agent.planner import Planner
from backend.agent.risk import RiskChecker
from backend.agent.router import IntentRouter
from backend.agent.state import AgentContext, AgentState
from backend.models.agent_run import AgentRun
from backend.models.common import utc_now
from backend.models.enums import AgentRunStatus
from backend.schemas.agent import AgentResult
from backend.tools.registry import ToolRegistry


class AgentExecutionError(RuntimeError):
    code = "AGENT_EXECUTION_ERROR"


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
    """Create AgentRun, invoke the graph, then persist one terminal run status."""
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
    try:
        final_state = graph.invoke(
            initial_state,
            context=AgentContext(
                session=session,
                registry=registry,
                agent_run_id=run_id,
                intent_router=intent_router,
                planner=planner,
                incident_analyzer=incident_analyzer,
                risk_checker=risk_checker,
            ),
        )
    except Exception as exc:
        _finish_run(
            session,
            run_id,
            status=AgentRunStatus.FAILED,
            intent=None,
            final_answer=None,
            latency_ms=_latency_ms(started_ns),
        )
        raise AgentExecutionError("Agent graph execution failed.") from exc

    error = final_state.get("error")
    status = AgentRunStatus.FAILED if error else AgentRunStatus.COMPLETED
    final_answer = final_state.get("final_answer", "")
    _finish_run(
        session,
        run_id,
        status=status,
        intent=final_state.get("intent"),
        final_answer=final_answer,
        latency_ms=_latency_ms(started_ns),
    )
    return AgentResult(
        run_id=run_id,
        request_id=request_id,
        intent=final_state.get("intent", "general"),
        routing_source=final_state.get("routing_source", "fallback"),
        routing_confidence=final_state.get("routing_confidence", 0),
        plan=final_state.get("plan", []),
        planner_source=final_state.get("planner_source", "fallback"),
        steps_executed=final_state.get("current_step", 0),
        selected_tool=final_state.get("selected_tool") or None,
        final_answer=final_answer,
        tool_results=final_state.get("tool_results", []),
        evidence=final_state.get("evidence", []),
        analysis=final_state.get("analysis"),
        analysis_source=final_state.get("analysis_source"),
        risk_level=final_state.get("risk_level", "LOW"),
        requires_approval=final_state.get("requires_approval", False),
        risk_reasons=final_state.get("risk_reasons", []),
        error=error,
    )


def _finish_run(
    session: Session,
    run_id: int,
    *,
    status: AgentRunStatus,
    intent: str | None,
    final_answer: str | None,
    latency_ms: int,
) -> None:
    """Update the existing run; database failures are handled by the request dependency."""
    run = session.get(AgentRun, run_id)
    if run is None:
        raise AgentExecutionError("Agent run disappeared before completion.")
    run.intent = intent
    run.status = status
    run.final_answer = final_answer
    run.finished_at = utc_now()
    run.latency_ms = latency_ms
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise


def _latency_ms(started_ns: int) -> int:
    return max(0, (perf_counter_ns() - started_ns) // 1_000_000)
