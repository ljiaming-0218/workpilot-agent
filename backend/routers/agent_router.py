"""HTTP entry point for coordinated Agent runs and offline comparison."""

from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Path, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.dependencies import get_db
from backend.models.agent_run import AgentRun
from backend.models.team import TeamMember, ServiceGrant
from backend.agent.planner import _service_names
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
from backend.services.access_service import current_team, run_access_filter
from backend.services.durable_access import (
    find_job, public_detail, require_public_run, stream_progress, sync_published_approval,
)
from backend.services.durable_queue import IdempotencyConflict, add_job, digests, find_submission
from backend.models.durable_job import DurableJobAttempt
from backend.services.auth_service import COOKIE_NAME


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
CurrentTeam = Annotated[TeamMember, Depends(current_team)]


def durable_views(request: Request) -> bool:
    return bool(getattr(request.app.state, "p3_schema_present", False) or
                getattr(getattr(request.app.state, "settings", None), "p3_durable_enabled", False))


def require_run(session: Session, run_id: int, member: TeamMember, *, durable: bool = False) -> AgentRun:
    if durable:
        return require_public_run(session, run_id, member.team_id)
    run = session.scalar(select(AgentRun).where(AgentRun.id == run_id, run_access_filter(member.team_id)))
    if run is None:
        raise AgentRunNotFoundError("Agent run does not exist.")
    return run


def require_requested_services(session: Session, query: str, member: TeamMember) -> None:
    services = set(_service_names(query))
    if not services:
        return
    granted = set(session.scalars(select(ServiceGrant.service_name).where(
        ServiceGrant.team_id == member.team_id, ServiceGrant.service_name.in_(services))).all())
    if services - granted:
        raise HTTPException(status_code=403, detail='团队没有所请求服务的调查权限')


@router.get("/runs", response_model=SuccessResponse[Page[AgentRunSummary]])
def get_agent_runs(
    pagination: Annotated[PaginationParams, Query()],
    request: Request,
    session: DbSession,
    member: CurrentTeam,
) -> SuccessResponse[Page[AgentRunSummary]]:
    durable = durable_views(request)
    page = list_agent_runs(session, pagination, team_id=member.team_id, durable=durable)
    if durable:
        for item in page.items:
            job = find_job(session, item.id)
            if job:
                item.queue_status = job.status
    return SuccessResponse(data=page)


@router.get(
    "/runs/{run_id}",
    response_model=SuccessResponse[AgentRunDetail],
    responses={404: {"model": ErrorResponse}},
)
def get_agent_run(
    request: Request,
    session: DbSession,
    member: CurrentTeam,
    run_id: int = Path(ge=1),
) -> SuccessResponse[AgentRunDetail]:
    durable = durable_views(request)
    root = require_run(session, run_id, member, durable=durable)
    return SuccessResponse(data=public_detail(session, root) if durable else get_agent_run_detail(session, run_id))


@router.get("/runs/{run_id}/attempts")
def get_attempts(request: Request, session: DbSession, member: CurrentTeam,
                 run_id: int = Path(ge=1)):
    durable = durable_views(request)
    if not durable:
        raise HTTPException(status_code=404, detail="持久任务未启用")
    require_run(session, run_id, member, durable=True)
    job = find_job(session, run_id)
    if job is None:
        return SuccessResponse(data=[])
    attempts = session.scalars(select(DurableJobAttempt).where(
        DurableJobAttempt.job_id == job.id).order_by(DurableJobAttempt.number)).all()
    return SuccessResponse(data=[{"number": a.number, "status": a.status,
                                  "started_at": a.started_at, "finished_at": a.finished_at,
                                  "error_code": a.error_code} for a in attempts])


@router.post("/runs", response_model=SuccessResponse[AgentResult])
def create_agent_run(
    payload: AgentRequest,
    request: Request,
    session: DbSession,
    member: CurrentTeam,
    comparison_mode: Annotated[
        Literal["single"] | None, Header(alias="X-WorkPilot-Comparison-Mode")
    ] = None,
) -> SuccessResponse[AgentResult]:
    require_requested_services(session, payload.query, member)
    if comparison_mode == "single":
        # Historical offline A/B runner only; never selected by the product UI.
        return SuccessResponse(data=run_agent(
            session, request.app.state.agent_graph,
            request.app.state.tool_registry, request.app.state.intent_router,
            request.app.state.planner, request.app.state.incident_analyzer,
            request.app.state.risk_checker, payload.query, creator_user_id=member.user_id, team_id=member.team_id,
        ))
    try:
        routing, planned = prepare_multi_scope(
            payload.query, request.app.state.intent_router, request.app.state.planner,
        )
    except MultiAgentScopeError:
        # Supervisor delegates other intents to the approval-capable specialist.
        run_id, request_id = prepare_agent_run(session, payload.query, creator_user_id=member.user_id, team_id=member.team_id)
        return SuccessResponse(data=execute_specialist_run(
            session, request.app.state.agent_graph,
            request.app.state.tool_registry, request.app.state.intent_router,
            request.app.state.planner, request.app.state.incident_analyzer,
            request.app.state.risk_checker, run_id, request_id, payload.query,
        ))
    run_id, request_id = prepare_agent_run(session, payload.query, creator_user_id=member.user_id, team_id=member.team_id)
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
    member: CurrentTeam,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> SuccessResponse[AgentRunAccepted]:
    durable = getattr(getattr(request.app.state, "settings", None), "p3_durable_enabled", False)
    if durable:
        if not idempotency_key:
            raise HTTPException(status_code=422, detail="需要 Idempotency-Key")
        try:
            key_hash, request_hash = digests(idempotency_key, payload.query)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="无效的 Idempotency-Key") from exc
        def existing():
            try:
                return find_submission(session, team_id=member.team_id, user_id=member.user_id,
                                       key_digest=key_hash, request_digest=request_hash)
            except IdempotencyConflict as exc:
                raise HTTPException(status_code=409, detail="幂等键已用于不同请求") from exc
        job = existing()
        if job is None:
            require_requested_services(session, payload.query, member)
            try:
                prepare_multi_scope(payload.query, request.app.state.intent_router, request.app.state.planner)
                retry_safe = True
            except MultiAgentScopeError:
                retry_safe = False
            run_id, _ = prepare_agent_run(session, payload.query, creator_user_id=member.user_id,
                                          team_id=member.team_id, commit=False)
            try:
                job = add_job(session, run_id=run_id, team_id=member.team_id, user_id=member.user_id,
                              key_digest=key_hash, request_digest=request_hash, retry_safe=retry_safe)
                session.commit()
            except IntegrityError:
                session.rollback()
                job = existing()
                if job is None:
                    raise
        root = require_run(session, job.agent_run_id, member, durable=True)
        return SuccessResponse(data=AgentRunAccepted(run_id=root.id, request_id=root.request_id,
                               queue_status=job.status, events_url=f"/agent/runs/{root.id}/events"))
    require_requested_services(session, payload.query, member)
    routing = planned = None
    execution_mode = "multi"
    try:
        routing, planned = prepare_multi_scope(
            payload.query, request.app.state.intent_router, request.app.state.planner,
        )
    except MultiAgentScopeError:
        execution_mode = "specialist"
    run_id, request_id = prepare_agent_run(session, payload.query, creator_user_id=member.user_id, team_id=member.team_id)
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
    member: CurrentTeam,
    run_id: int = Path(ge=1),
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    durable = durable_views(request)
    run = require_run(session, run_id, member, durable=durable)
    if durable and find_job(session, run_id) is not None:
        cookie = request.cookies.get(COOKIE_NAME, "")
        try:
            cursor = max(0, int(last_event_id or 0))
        except ValueError:
            cursor = 0
        session.rollback()
        return StreamingResponse(stream_progress(request.app.state.session_factory, run_id=run_id,
                                 team_id=member.team_id, user_id=member.user_id,
                                 cookie=cookie, after_id=cursor), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
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
    member: CurrentTeam,
    run_id: int = Path(ge=1),
) -> SuccessResponse[AgentResult]:
    durable = durable_views(request)
    root = require_run(session, run_id, member, durable=durable)
    if member.role not in {'approver', 'admin'}:
        raise HTTPException(status_code=403, detail='需要审批权限')
    job = find_job(session, run_id) if durable else None
    if job:
        sync_published_approval(session, root, job)
        if job.status != "waiting_approval" or job.published_run_id is None:
            raise HTTPException(status_code=409, detail="任务不在等待审批状态")
    execution_id = job.published_run_id if job else run_id
    coordinated = session.scalar(select(AgentTraceEvent.id).where(
        AgentTraceEvent.agent_run_id == execution_id,
        AgentTraceEvent.node == "supervisor",
        AgentTraceEvent.event_type == "specialist_dispatched",
    ).limit(1)) is not None
    resume = resume_specialist_run if coordinated else resume_agent
    try:
        result = resume(
            session, request.app.state.agent_graph, request.app.state.tool_registry,
            request.app.state.intent_router, request.app.state.planner,
            request.app.state.incident_analyzer, request.app.state.risk_checker,
            execution_id, payload.action,
        )
    finally:
        if job:
            session.rollback()
            root = session.get(AgentRun, run_id)
            job = find_job(session, run_id)
            if root and job:
                sync_published_approval(session, root, job)
    if job:
        result = result.model_copy(update={"run_id": run_id, "request_id": root.request_id})
    return SuccessResponse(data=result)
