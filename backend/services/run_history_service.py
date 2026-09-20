"""Reopen saved Agent runs using only persisted audit data."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.agent_run import AgentRun
from backend.models.enums import ToolCallStatus
from backend.models.tool_call import ToolCallLog
from backend.models.trace_event import AgentTraceEvent
from backend.schemas.common import Page, PaginationParams
from backend.schemas.run_history import AgentRunDetail, AgentRunSummary
from backend.services.agent_service import AgentRunNotFoundError


def list_agent_runs(session: Session, pagination: PaginationParams) -> Page[AgentRunSummary]:
    total = session.scalar(select(func.count()).select_from(AgentRun)) or 0
    runs = session.scalars(
        select(AgentRun)
        .order_by(AgentRun.id.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    )
    return Page(
        items=[AgentRunSummary.model_validate(run) for run in runs],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


def get_agent_run_detail(session: Session, run_id: int) -> AgentRunDetail:
    run = session.get(AgentRun, run_id)
    if run is None:
        raise AgentRunNotFoundError("Agent run does not exist.")

    events = session.scalars(
        select(AgentTraceEvent)
        .where(AgentTraceEvent.agent_run_id == run_id)
        .order_by(AgentTraceEvent.step)
    )
    node_outputs = {
        event.node: event.output_json
        for event in events
        if event.event_type == "node_completed" and isinstance(event.output_json, dict)
    }
    routing = node_outputs.get("intent_router") or {}
    planning = node_outputs.get("planner") or {}
    risk = node_outputs.get("risk_checker") or {}
    plan = planning.get("plan")
    reasons = risk.get("risk_reasons")

    calls = session.scalars(
        select(ToolCallLog)
        .where(ToolCallLog.agent_run_id == run_id)
        .order_by(ToolCallLog.id)
    )
    evidence = []
    steps_executed = 0
    for call in calls:
        if call.status != ToolCallStatus.COMPLETED:
            continue
        steps_executed += 1
        if isinstance(call.output_json, dict):
            evidence.append({"tool": call.tool_name, "output": call.output_json})

    return AgentRunDetail.model_validate({
        **AgentRunSummary.model_validate(run).model_dump(),
        "final_answer": run.final_answer,
        "plan": plan if isinstance(plan, list) else [],
        "evidence": evidence,
        "routing_source": routing.get("routing_source"),
        "routing_confidence": routing.get("routing_confidence"),
        "planner_source": planning.get("planner_source"),
        "risk_level": risk.get("risk_level"),
        "risk_reasons": reasons if isinstance(reasons, list) else [],
        "steps_executed": steps_executed,
    })
