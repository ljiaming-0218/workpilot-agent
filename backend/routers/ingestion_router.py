"""HTTP entry points for validated batch ingestion."""

from secrets import compare_digest
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.dependencies import get_db
from backend.schemas.common import ErrorResponse, SuccessResponse
from backend.schemas.ingestion import (
    ImportResult,
    KnowledgeImportRequest,
    LogImportRequest,
)
from backend.services.ingestion_service import import_knowledge, import_logs
from backend.services.access_service import current_team, granted_services
from backend.models.team import TeamMember


def require_ingestion_key(
    request: Request,
    provided_key: Annotated[str | None, Header(alias="X-Ingestion-Key")] = None,
) -> None:
    configured_key = request.app.state.settings.ingestion_api_key
    if configured_key is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Data ingestion is not configured.",
        )
    expected = configured_key.get_secret_value()
    if provided_key is None or not compare_digest(provided_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid ingestion key.",
        )


router = APIRouter(
    prefix="/ingestion",
    tags=["ingestion"],
    dependencies=[Depends(require_ingestion_key)],
    responses={
        401: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
DbSession = Annotated[Session, Depends(get_db)]


@router.post("/logs", response_model=SuccessResponse[ImportResult])
def ingest_logs(
    payload: LogImportRequest,
    session: DbSession,
    member: Annotated[TeamMember, Depends(current_team)],
) -> SuccessResponse[ImportResult]:
    if any(item.service_name not in granted_services(session, member.team_id) for item in payload.items):
        raise HTTPException(status_code=403, detail="无权导入该服务的日志")
    return SuccessResponse(data=import_logs(session, payload))


@router.post("/knowledge", response_model=SuccessResponse[ImportResult])
def ingest_knowledge(
    payload: KnowledgeImportRequest,
    session: DbSession,
    member: Annotated[TeamMember, Depends(current_team)],
) -> SuccessResponse[ImportResult]:
    return SuccessResponse(data=import_knowledge(session, payload, team_id=member.team_id))
