"""Text2SQL input, model output, and safe query result contracts."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Text2SQLRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=2_000)


class GeneratedSQL(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sql: str = Field(min_length=1, max_length=20_000)


class Text2SQLResult(BaseModel):
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
