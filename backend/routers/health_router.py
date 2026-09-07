from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database import is_database_connected
from backend.dependencies import get_db


router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["connected", "disconnected"]


@router.get("/health", response_model=HealthResponse, responses={503: {"model": HealthResponse}})
def health(response: Response, session: Annotated[Session, Depends(get_db)]) -> HealthResponse:
    if not is_database_connected(session):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="degraded", database="disconnected")
    return HealthResponse(status="ok", database="connected")
