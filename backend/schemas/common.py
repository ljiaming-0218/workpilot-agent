"""Shared API envelopes, pagination and UTC serialization."""

from datetime import datetime, timezone
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


T = TypeVar("T")


def as_utc(value: datetime) -> datetime:
    # MySQL DATETIME stores naive UTC in this project.
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


UtcDatetime = Annotated[datetime, AfterValidator(as_utc)]


class SuccessResponse(BaseModel, Generic[T]):
    success: Literal[True] = True
    data: T
    error: None = None


class ValidationIssue(BaseModel):
    location: list[str | int]
    message: str
    type: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list[ValidationIssue] | None = None


class ErrorResponse(BaseModel):
    success: Literal[False] = False
    data: None = None
    error: ErrorDetail


class PaginationParams(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
