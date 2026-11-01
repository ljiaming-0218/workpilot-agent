"""Read-only log HTTP endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.dependencies import get_db
from backend.schemas.common import ErrorResponse, Page, SuccessResponse
from backend.schemas.log import LogFilters, LogRead
from backend.services.log_service import list_logs
from backend.services.access_service import current_team, granted_services
from backend.models.team import TeamMember


router = APIRouter(
    prefix="/logs", tags=["logs"],
    responses={
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)


@router.get("", response_model=SuccessResponse[Page[LogRead]])
def get_logs(
    filters: Annotated[LogFilters, Query()], session: Annotated[Session, Depends(get_db)],
    member: Annotated[TeamMember, Depends(current_team)],
) -> SuccessResponse[Page[LogRead]]:
    return SuccessResponse(data=list_logs(session, filters, allowed_services=granted_services(session, member.team_id)))
