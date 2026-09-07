"""Public ticket inputs and outputs, separate from the database model."""

from pydantic import BaseModel, ConfigDict, Field

from backend.models.enums import TicketCategory, TicketPriority, TicketStatus
from backend.schemas.common import PaginationParams, UtcDatetime


class TicketCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=100_000)
    category: TicketCategory = TicketCategory.OTHER
    priority: TicketPriority = TicketPriority.MEDIUM
    service_name: str | None = Field(default=None, min_length=1, max_length=128)


class TicketFilters(PaginationParams):
    service_name: str | None = Field(default=None, min_length=1, max_length=128)
    category: TicketCategory | None = None
    priority: TicketPriority | None = None
    status: TicketStatus | None = None
    keyword: str | None = Field(default=None, min_length=1, max_length=100)


class TicketRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    content: str
    category: TicketCategory
    priority: TicketPriority
    status: TicketStatus
    service_name: str | None
    resolution: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
