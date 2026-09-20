"""HTTP entry point for one synchronous basic Agent run."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.orm import Session

from backend.dependencies import get_db
from backend.schemas.agent import AgentApprovalRequest, AgentRequest, AgentResult
from backend.schemas.common import ErrorResponse, Page, PaginationParams, SuccessResponse
from backend.schemas.run_history import AgentRunDetail, AgentRunSummary
from backend.services.agent_service import resume_agent, run_agent
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
) -> SuccessResponse[AgentResult]:
    result = run_agent(
        session,
        request.app.state.agent_graph,
        request.app.state.tool_registry,
        request.app.state.intent_router,
        request.app.state.planner,
        request.app.state.incident_analyzer,
        request.app.state.risk_checker,
        payload.query,
    )
    return SuccessResponse(data=result)


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
    result = resume_agent(
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
