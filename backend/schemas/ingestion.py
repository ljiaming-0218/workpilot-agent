"""Validated contracts for idempotent business-data ingestion."""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from backend.models.enums import LogLevel


class LogImportItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    service_name: str = Field(min_length=1, max_length=128)
    level: LogLevel
    error_type: str | None = Field(default=None, min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=20_000)
    stack_trace: str | None = Field(default=None, max_length=100_000)
    request_id: str = Field(min_length=1, max_length=64)
    created_at: AwareDatetime | None = None


class KnowledgeImportItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=100_000)
    category: str | None = Field(default=None, min_length=1, max_length=64)
    source: str = Field(min_length=1, max_length=512)


class LogImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[LogImportItem] = Field(min_length=1, max_length=100)


class KnowledgeImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[KnowledgeImportItem] = Field(min_length=1, max_length=100)


class ImportResult(BaseModel):
    requested: int = Field(ge=1)
    created: int = Field(ge=0)
    updated: int = Field(ge=0)
    skipped: int = Field(ge=0)
    item_ids: list[int]

