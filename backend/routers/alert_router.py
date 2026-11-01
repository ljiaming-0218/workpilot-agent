"""Authenticated HTTP entry point for external incident alerts."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.dependencies import get_db
from backend.routers.ingestion_router import require_ingestion_key
from backend.schemas.alert import IncidentAlertRequest, IncidentAlertAccepted
from backend.schemas.common import ErrorResponse, SuccessResponse
from backend.services.alert_service import enqueue_incident_alert
from backend.services.access_service import current_team
from backend.models.team import TeamMember
from backend.routers.investigation_router import require_p4


router = APIRouter(
    prefix="/alerts",
    tags=["alerts"],
    dependencies=[Depends(require_ingestion_key)],
    responses={
        401: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
DbSession = Annotated[Session, Depends(get_db)]


@router.post("/incidents", response_model=SuccessResponse[IncidentAlertAccepted], status_code=202,
             dependencies=[Depends(require_p4)])
def receive_incident_alert(
    payload: IncidentAlertRequest,
    request: Request,
    session: DbSession,
    member: Annotated[TeamMember, Depends(current_team)],
) -> SuccessResponse[IncidentAlertAccepted]:
    if not request.app.state.settings.p3_durable_enabled:
        raise HTTPException(503, "告警接入需要启用 P3 持久队列并启动独立 Worker")
    result = enqueue_incident_alert(session, member, payload)
    return SuccessResponse(data=result)
