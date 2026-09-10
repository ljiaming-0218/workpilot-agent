"""Validated contracts for the first three read-only Agent tools."""

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.knowledge import KnowledgeSearchHit
from backend.schemas.log import LogRead
from backend.schemas.text2sql import Text2SQLResult
from backend.schemas.ticket import TicketRead


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SearchTicketsInput(ToolInput):
    keyword: str | None = Field(default=None, min_length=1, max_length=100)
    service_name: str | None = Field(default=None, min_length=1, max_length=128)
    limit: int = Field(default=5, ge=1, le=100)


class SearchTicketsOutput(BaseModel):
    items: list[TicketRead]
    total: int


class GetTicketDetailInput(ToolInput):
    ticket_id: int = Field(ge=1)


class GetTicketDetailOutput(BaseModel):
    ticket: TicketRead | None


class QueryErrorLogsInput(ToolInput):
    service_name: str = Field(min_length=1, max_length=128)
    error_type: str | None = Field(default=None, min_length=1, max_length=128)
    minutes: int = Field(default=60, ge=1, le=10_080)
    limit: int = Field(default=100, ge=1, le=100)


class QueryErrorLogsOutput(BaseModel):
    items: list[LogRead]
    total: int


class QueryDatabaseInput(ToolInput):
    question: str = Field(min_length=1, max_length=2_000)


class QueryDatabaseOutput(Text2SQLResult):
    pass


class SearchKnowledgeInput(ToolInput):
    query: str = Field(min_length=1, max_length=500)
    category: str | None = Field(default=None, min_length=1, max_length=64)
    limit: int = Field(default=5, ge=1, le=20)


class SearchKnowledgeOutput(BaseModel):
    items: list[KnowledgeSearchHit]
    total: int


class SimulateHighRiskOperationInput(ToolInput):
    request: str = Field(min_length=1, max_length=500)


class SimulateHighRiskOperationOutput(BaseModel):
    simulated: bool
    message: str
