"""Read ordered execution traces for one Agent run."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from backend.agent.trace import get_agent_trace
from backend.dependencies import get_db
from backend.schemas.common import ErrorResponse, SuccessResponse
from backend.schemas.trace import AgentTraceRead
from backend.services.agent_service import AgentRunNotFoundError


router = APIRouter(
    prefix="/agent/runs",
    tags=["agent-trace"],
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/{run_id}/trace", response_model=SuccessResponse[AgentTraceRead])
def read_agent_trace(
    session: DbSession,
    run_id: int = Path(ge=1),
) -> SuccessResponse[AgentTraceRead]:
    trace = get_agent_trace(session, run_id)
    if trace is None:
        raise AgentRunNotFoundError("Agent run does not exist.")
    return SuccessResponse(data=trace)
