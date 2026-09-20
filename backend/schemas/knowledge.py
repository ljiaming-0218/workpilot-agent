"""Validated output contracts for BM25 knowledge retrieval."""

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.common import UtcDatetime


class KnowledgeSearchHit(BaseModel):
    document_id: int
    title: str
    excerpt: str
    category: str | None
    source: str | None
    score: float = Field(ge=0)


class KnowledgeRead(BaseModel):
    """Read-only document view for the Business Data console."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    content: str
    category: str | None
    source: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
