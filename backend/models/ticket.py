"""Support tickets and their current resolution state."""

from sqlalchemy import Index, String
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.common import TABLE_OPTIONS, TimestampMixin, enum_type
from backend.models.enums import TicketCategory, TicketPriority, TicketStatus


class Ticket(TimestampMixin, Base):
    __tablename__ = "tickets"
    __table_args__ = (Index("ix_tickets_created_at", "created_at"), TABLE_OPTIONS.copy())

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(LONGTEXT)
    category: Mapped[TicketCategory] = mapped_column(
        enum_type(TicketCategory), default=TicketCategory.OTHER, index=True
    )
    priority: Mapped[TicketPriority] = mapped_column(
        enum_type(TicketPriority), default=TicketPriority.MEDIUM
    )
    status: Mapped[TicketStatus] = mapped_column(
        enum_type(TicketStatus), default=TicketStatus.OPEN, index=True
    )
    service_name: Mapped[str | None] = mapped_column(String(128), index=True)
    resolution: Mapped[str | None] = mapped_column(LONGTEXT)
