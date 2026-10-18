"""Delegate non-investigation tasks to the existing approval-capable specialist."""

from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.orm import Session

from backend.agent.analyzer import IncidentAnalyzer
from backend.agent.planner import Planner
from backend.agent.risk import RiskChecker
from backend.agent.router import IntentRouter
from backend.agent.trace import TraceRecorder
from backend.schemas.agent import AgentResult, AgentTaskResult, ApprovalAction
from backend.services.agent_service import execute_agent_run, resume_agent
from backend.tools.registry import ToolRegistry


def execute_specialist_run(
    session: Session, graph: CompiledStateGraph, registry: ToolRegistry,
    intent_router: IntentRouter, planner: Planner,
    incident_analyzer: IncidentAnalyzer, risk_checker: RiskChecker,
    run_id: int, request_id: str, query: str, progress_sink=None,
) -> AgentResult:
    """Supervisor handoff; the specialist keeps its own graph and checkpoint."""
    trace = TraceRecorder(session, run_id, progress_sink)
    trace.record_multi_event(
        node="supervisor", event_type="specialist_dispatched",
        output_json={"task_id": f"task-{run_id}-1", "agent_id": "workflow_specialist"},
    )
    trace.persist(session)
    session.commit()
    result = execute_agent_run(
        session, graph, registry, intent_router, planner, incident_analyzer,
        risk_checker, run_id, request_id, query, progress_sink,
    )
    return _complete_handoff(session, result, progress_sink)


def resume_specialist_run(
    session: Session, graph: CompiledStateGraph, registry: ToolRegistry,
    intent_router: IntentRouter, planner: Planner,
    incident_analyzer: IncidentAnalyzer, risk_checker: RiskChecker,
    run_id: int, action: ApprovalAction,
) -> AgentResult:
    result = resume_agent(
        session, graph, registry, intent_router, planner,
        incident_analyzer, risk_checker, run_id, action,
    )
    return _complete_handoff(session, result)


def _complete_handoff(session: Session, result: AgentResult, progress_sink=None) -> AgentResult:
    status = (
        "failed" if result.status.value == "failed" else
        "partial" if result.status.value == "waiting_approval" else "completed"
    )
    task = AgentTaskResult(
        task_id=f"task-{result.run_id}-1", parent_task_id=f"run-{result.run_id}",
        agent_id="workflow_specialist", service=result.intent, status=status,
        decisions=[{"intent": result.intent, "selected_tool": result.selected_tool}],
        errors=[result.error] if result.error else [],
        attempts_used=result.steps_executed,
        stop_reason="waiting_approval" if status == "partial" else status,
    )
    trace = TraceRecorder(session, result.run_id, progress_sink)
    trace.record_multi_event(
        node="supervisor", event_type="specialist_result",
        output_json={"mode": "multi", "task": task.model_dump(mode="json"),
                     "run_status": result.status.value},
    )
    trace.persist(session)
    session.commit()
    return result.model_copy(update={"mode": "multi", "agent_tasks": [task]})
