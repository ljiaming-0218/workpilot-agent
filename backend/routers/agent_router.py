"""HTTP entry point for coordinated Agent runs and offline comparison."""

from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Path, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.dependencies import get_db
from backend.models.agent_run import AgentRun
from backend.models.enums import AgentRunStatus
from backend.models.trace_event import AgentTraceEvent
from backend.schemas.agent import (
    AgentApprovalRequest,
    AgentRequest,
    AgentResult,
    AgentRunAccepted,
)
from backend.schemas.common import ErrorResponse, Page, PaginationParams, SuccessResponse
from backend.schemas.run_history import AgentRunDetail, AgentRunSummary
from backend.services.agent_async_service import execute_agent_run_background
from backend.services.agent_service import (
    AgentRunNotFoundError,
    prepare_agent_run,
    resume_agent,
    run_agent,
)
from backend.services.multi_agent_service import (
    MultiAgentScopeError, execute_multi_agent_run, prepare_multi_scope,
)
from backend.services.specialist_agent_service import (
    execute_specialist_run, resume_specialist_run,
)
from backend.services.run_history_service import get_agent_run_detail, list_agent_runs


router = APIRouter(
    prefix="/agent",
    tags=["agent"],
    responses={
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/runs", response_model=SuccessResponse[Page[AgentRunSummary]])
def get_agent_runs(
    pagination: Annotated[PaginationParams, Query()],
    session: DbSession,
) -> SuccessResponse[Page[AgentRunSummary]]:
    return SuccessResponse(data=list_agent_runs(session, pagination))


@router.get(
    "/runs/{run_id}",
    response_model=SuccessResponse[AgentRunDetail],
    responses={404: {"model": ErrorResponse}},
)
def get_agent_run(
    session: DbSession,
    run_id: int = Path(ge=1),
) -> SuccessResponse[AgentRunDetail]:
    return SuccessResponse(data=get_agent_run_detail(session, run_id))


@router.post("/runs", response_model=SuccessResponse[AgentResult])
def create_agent_run(
    payload: AgentRequest,
    request: Request,
    session: DbSession,
    comparison_mode: Annotated[
        Literal["single"] | None, Header(alias="X-WorkPilot-Comparison-Mode")
    ] = None,
) -> SuccessResponse[AgentResult]:
    if comparison_mode == "single":
        # Historical offline A/B runner only; never selected by the product UI.
        return SuccessResponse(data=run_agent(
            session, request.app.state.agent_graph,
            request.app.state.tool_registry, request.app.state.intent_router,
            request.app.state.planner, request.app.state.incident_analyzer,
            request.app.state.risk_checker, payload.query,
        ))
    try:
        routing, planned = prepare_multi_scope(
            payload.query, request.app.state.intent_router, request.app.state.planner,
        )
    except MultiAgentScopeError:
        # Supervisor delegates other intents to the approval-capable specialist.
        run_id, request_id = prepare_agent_run(session, payload.query)
        return SuccessResponse(data=execute_specialist_run(
            session, request.app.state.agent_graph,
            request.app.state.tool_registry, request.app.state.intent_router,
            request.app.state.planner, request.app.state.incident_analyzer,
            request.app.state.risk_checker, run_id, request_id, payload.query,
        ))
    run_id, request_id = prepare_agent_run(session, payload.query)
    return SuccessResponse(data=execute_multi_agent_run(
        session, request.app.state.tool_registry, request.app.state.risk_checker,
        run_id, request_id, payload.query, routing, planned,
        session_factory=request.app.state.session_factory,
        agents=getattr(request.app.state, "diagnostic_agents", None),
    ))


@router.post(
    "/runs/async",
    response_model=SuccessResponse[AgentRunAccepted],
    status_code=status.HTTP_202_ACCEPTED,
)
def create_async_agent_run(
    payload: AgentRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session: DbSession,
) -> SuccessResponse[AgentRunAccepted]:
    routing = planned = None
    execution_mode = "multi"
    try:
        routing, planned = prepare_multi_scope(
            payload.query, request.app.state.intent_router, request.app.state.planner,
        )
    except MultiAgentScopeError:
        execution_mode = "specialist"
    run_id, request_id = prepare_agent_run(session, payload.query)
    broker = request.app.state.run_event_broker
    broker.publish(
        run_id,
        "accepted",
        {"run_id": run_id, "status": AgentRunStatus.RUNNING.value},
    )
    background_tasks.add_task(
        execute_agent_run_background,
        request.app.state.session_factory,
        request.app.state.agent_graph,
        request.app.state.tool_registry,
        request.app.state.intent_router,
        request.app.state.planner,
        request.app.state.incident_analyzer,
        request.app.state.risk_checker,
        broker,
        run_id,
        request_id,
        payload.query,
        execution_mode,
        routing,
        planned,
        getattr(request.app.state, "diagnostic_agents", None),
    )
    return SuccessResponse(
        data=AgentRunAccepted(
            run_id=run_id,
            request_id=request_id,
            events_url=f"/agent/runs/{run_id}/events",
        )
    )


@router.get(
    "/runs/{run_id}/events",
    responses={404: {"model": ErrorResponse}},
    response_class=StreamingResponse,
)
def stream_agent_run_events(
    request: Request,
    session: DbSession,
    run_id: int = Path(ge=1),
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    run = session.get(AgentRun, run_id)
    if run is None:
        raise AgentRunNotFoundError("Agent run does not exist.")
    run_status = run.status
    session.rollback()
    broker = request.app.state.run_event_broker
    if not broker.has_events(run_id):
        if run_status == AgentRunStatus.RUNNING:
            broker.publish(
                run_id,
                "terminal",
                {
                    "run_id": run_id,
                    "status": run_status.value,
                    "error": "LIVE_STREAM_UNAVAILABLE",
                },
                terminal=True,
            )
        else:
            broker.publish(
                run_id,
                "terminal",
                {"run_id": run_id, "status": run_status.value, "error": None},
                terminal=True,
            )
    try:
        after_sequence = max(0, int(last_event_id or 0))
    except ValueError:
        after_sequence = 0
    return StreamingResponse(
        broker.stream(run_id, after_sequence=after_sequence),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/runs/{run_id}/approval",
    response_model=SuccessResponse[AgentResult],
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def approve_agent_run(
    payload: AgentApprovalRequest,
    request: Request,
    session: DbSession,
    run_id: int = Path(ge=1),
) -> SuccessResponse[AgentResult]:
    coordinated = session.scalar(select(AgentTraceEvent.id).where(
        AgentTraceEvent.agent_run_id == run_id,
        AgentTraceEvent.node == "supervisor",
        AgentTraceEvent.event_type == "specialist_dispatched",
    ).limit(1)) is not None
    resume = resume_specialist_run if coordinated else resume_agent
    result = resume(
        session,
        request.app.state.agent_graph,
        request.app.state.tool_registry,
        request.app.state.intent_router,
        request.app.state.planner,
        request.app.state.incident_analyzer,
        request.app.state.risk_checker,
        run_id,
        payload.action,
    )
    return SuccessResponse(data=result)
