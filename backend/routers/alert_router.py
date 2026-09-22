"""Authenticated HTTP entry point for external incident alerts."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from backend.dependencies import get_db
from backend.routers.ingestion_router import require_ingestion_key
from backend.schemas.alert import IncidentAlertRequest, IncidentAlertResult
from backend.schemas.common import ErrorResponse, SuccessResponse
from backend.services.alert_service import process_incident_alert


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


@router.post("/incidents", response_model=SuccessResponse[IncidentAlertResult])
def receive_incident_alert(
    payload: IncidentAlertRequest,
    request: Request,
    session: DbSession,
) -> SuccessResponse[IncidentAlertResult]:
    result = process_incident_alert(
        session,
        request.app.state.agent_graph,
        request.app.state.tool_registry,
        request.app.state.intent_router,
        request.app.state.planner,
        request.app.state.incident_analyzer,
        request.app.state.risk_checker,
        payload,
    )
    return SuccessResponse(data=result)
