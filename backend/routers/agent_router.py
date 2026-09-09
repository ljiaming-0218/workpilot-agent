"""HTTP entry point for one synchronous basic Agent run."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from backend.dependencies import get_db
from backend.schemas.agent import AgentRequest, AgentResult
from backend.schemas.common import ErrorResponse, SuccessResponse
from backend.services.agent_service import run_agent


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
        payload.query,
    )
    return SuccessResponse(data=result)
