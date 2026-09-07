"""Ticket HTTP endpoints; persistence lives in ticket_service."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session

from backend.dependencies import get_db
from backend.schemas.common import ErrorResponse, Page, SuccessResponse
from backend.schemas.ticket import TicketCreate, TicketFilters, TicketRead
from backend.services import ticket_service


router = APIRouter(
    prefix="/tickets", tags=["tickets"],
    responses={
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
DbSession = Annotated[Session, Depends(get_db)]


@router.post(
    "", response_model=SuccessResponse[TicketRead], status_code=status.HTTP_201_CREATED,
    responses={409: {"model": ErrorResponse}},
)
def create_ticket(payload: TicketCreate, session: DbSession) -> SuccessResponse[TicketRead]:
    return SuccessResponse(data=ticket_service.create_ticket(session, payload))


@router.get("", response_model=SuccessResponse[Page[TicketRead]])
def list_tickets(
    filters: Annotated[TicketFilters, Query()], session: DbSession,
) -> SuccessResponse[Page[TicketRead]]:
    return SuccessResponse(data=ticket_service.list_tickets(session, filters))


@router.get(
    "/{ticket_id}", response_model=SuccessResponse[TicketRead],
    responses={404: {"model": ErrorResponse}},
)
def get_ticket(
    ticket_id: Annotated[int, Path(ge=1)], session: DbSession,
) -> SuccessResponse[TicketRead]:
    ticket = ticket_service.get_ticket(session, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    return SuccessResponse(data=ticket)
