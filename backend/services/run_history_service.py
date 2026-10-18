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
    ).all()
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
    merged = next((
        event.output_json for event in events
        if event.node == "supervisor" and event.event_type == "results_merged"
        and isinstance(event.output_json, dict)
    ), None)
    specialist = next((
        event.output_json for event in reversed(events)
        if event.node == "supervisor" and event.event_type == "specialist_result"
        and isinstance(event.output_json, dict)
    ), None)
    coordinated = any(
        event.node == "supervisor" and event.event_type in {
            "tasks_dispatched", "specialist_dispatched",
        }
        for event in events
    )
    task_events = [
        event for event in events
        if event.node == "service_investigator" and event.event_type == "tool_completed"
    ]

    calls = session.scalars(
        select(ToolCallLog)
        .where(ToolCallLog.agent_run_id == run_id)
        .order_by(ToolCallLog.id)
    ).all()
    evidence = []
    completed = [
        call for call in calls
        if call.status == ToolCallStatus.COMPLETED and isinstance(call.output_json, dict)
    ]
    if merged is None:
        evidence = [{"tool": call.tool_name, "output": call.output_json} for call in completed]
    else:
        events_by_id = {
            (event.output_json or {}).get("evidence_id"): event
            for event in task_events
        }
        available = list(completed)
        for evidence_id in merged.get("evidence_ids", []):
            event = events_by_id.get(evidence_id)
            if event is None:
                continue
            scope = event.input_json or {}
            match_index = next((
                index for index, call in enumerate(available)
                if call.tool_name == scope.get("tool")
                and {key: value for key, value in call.input_json.items() if value is not None}
                == {key: value for key, value in (scope.get("inputs") or {}).items() if value is not None}
            ), None)
            if match_index is None:
                continue
            call = available.pop(match_index)
            output = call.output_json
            evidence.append({
                "step": len(evidence) + 1,
                "evidence_id": evidence_id,
                "result_status": (event.output_json or {}).get("result_status"),
                "agent_id": scope.get("agent_id"),
                "task_id": scope.get("task_id"),
                "service": call.input_json.get("service_name") or scope.get("service"),
                "tool": call.tool_name,
                "source_record_ids": [
                    item.get("id", item.get("document_id"))
                    for item in output.get("items", []) if isinstance(item, dict)
                ],
                "output": output,
            })

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
        "steps_executed": merged.get("tool_budget", {}).get("used", 0)
        if merged is not None else len(completed),
        "mode": "multi" if coordinated or merged is not None or specialist is not None else "single",
        "agent_tasks": merged.get("tasks", []) if merged is not None else
        [specialist["task"]] if specialist is not None else [],
        "investigation_status": merged.get("investigation_status") if merged is not None else None,
        "tool_budget": merged.get("tool_budget") if merged is not None else None,
        "verifier": merged.get("verifier") if merged is not None else None,
        "model_budget": merged.get("model_budget") if merged is not None else None,
    })
