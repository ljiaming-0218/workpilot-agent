"""Read-only log API schemas; filter timestamps must include a timezone."""

from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from backend.models.enums import LogLevel
from backend.schemas.common import PaginationParams, UtcDatetime


class LogFilters(PaginationParams):
    sort_by: Literal["event_time", "added"] = "event_time"
    service_name: str | None = Field(default=None, min_length=1, max_length=128)
    level: LogLevel | None = None
    error_type: str | None = Field(default=None, min_length=1, max_length=128)
    request_id: str | None = Field(default=None, min_length=1, max_length=64)
    created_from: AwareDatetime | None = None
    created_to: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_time_range(self) -> Self:
        if self.created_from is not None and self.created_to is not None:
            if self.created_from >= self.created_to:
                raise ValueError("created_from must be earlier than created_to")
        return self


class LogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    service_name: str
    level: LogLevel
    error_type: str | None
    message: str
    stack_trace: str | None
    request_id: str | None
    created_at: UtcDatetime
