"""Read-only log HTTP endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.dependencies import get_db
from backend.schemas.common import ErrorResponse, Page, SuccessResponse
from backend.schemas.log import LogFilters, LogRead
from backend.services.log_service import list_logs


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
) -> SuccessResponse[Page[LogRead]]:
    return SuccessResponse(data=list_logs(session, filters))
