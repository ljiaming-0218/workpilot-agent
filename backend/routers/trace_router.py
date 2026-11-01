"""Read ordered execution traces for one Agent run."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from sqlalchemy.orm import Session
from sqlalchemy import select

from backend.agent.trace import get_agent_trace
from backend.dependencies import get_db
from backend.schemas.common import ErrorResponse, SuccessResponse
from backend.schemas.trace import AgentTraceRead
from backend.services.agent_service import AgentRunNotFoundError
from backend.services.access_service import current_team, run_access_filter
from backend.models.team import TeamMember
from backend.models.agent_run import AgentRun
from backend.services.durable_access import public_trace, require_public_run


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
    request: Request,
    session: DbSession,
    member: Annotated[TeamMember, Depends(current_team)],
    run_id: int = Path(ge=1),
) -> SuccessResponse[AgentTraceRead]:
    if (getattr(request.app.state, "p3_schema_present", False) or
            getattr(getattr(request.app.state, "settings", None), "p3_durable_enabled", False)):
        root = require_public_run(session, run_id, member.team_id)
        return SuccessResponse(data=public_trace(session, root))
    run = session.scalar(select(AgentRun).where(AgentRun.id == run_id, run_access_filter(member.team_id)))
    if run is None:
        raise AgentRunNotFoundError("Agent run does not exist.")
    trace = get_agent_trace(session, run_id)
    if trace is None:
        raise AgentRunNotFoundError("Agent run does not exist.")
    return SuccessResponse(data=trace)
