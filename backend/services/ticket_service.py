"""Ticket persistence and queries; the caller owns the Session lifecycle."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.ticket import Ticket
from backend.schemas.common import Page
from backend.schemas.ticket import TicketCreate, TicketFilters, TicketRead


def create_ticket(session: Session, payload: TicketCreate) -> TicketRead:
    """Commit one ticket; get_db rolls back database failures and closes the Session."""
    ticket = Ticket(**payload.model_dump())
    session.add(ticket)
    session.flush()
    # Snapshot generated IDs/defaults before commit expires the ORM attributes.
    # This avoids an extra SELECT (and possible read failure) after a successful write.
    result = TicketRead.model_validate(ticket)
    session.commit()
    return result


def get_ticket(session: Session, ticket_id: int) -> TicketRead | None:
    """Find a ticket by primary key; absence is a business result, not a DB error."""
    ticket = session.get(Ticket, ticket_id)
    return TicketRead.model_validate(ticket) if ticket is not None else None


def list_tickets(session: Session, filters: TicketFilters) -> Page[TicketRead]:
    """Count all matches, then fetch a bounded page ordered by newest time and ID."""
    conditions = []
    for name in ("service_name", "category", "priority", "status"):
        value = getattr(filters, name)
        if value is not None:
            conditions.append(getattr(Ticket, name) == value)

    if filters.keyword is not None:
        conditions.append(
            Ticket.title.contains(filters.keyword, autoescape=True)
        )
    total = session.scalar(select(func.count()).select_from(Ticket).where(*conditions))
    statement = (
        select(Ticket)
        .where(*conditions)
        .order_by(Ticket.created_at.desc(), Ticket.id.desc())
        .offset(filters.offset)
        .limit(filters.page_size)
    )

    items = [TicketRead.model_validate(ticket) for ticket in session.scalars(statement)]
    return Page(items=items, total=total, page=filters.page, page_size=filters.page_size)
