"""Validated output contracts for BM25 knowledge retrieval."""

from pydantic import BaseModel, Field


class KnowledgeSearchHit(BaseModel):
    document_id: int
    title: str
    excerpt: str
    category: str | None
    source: str | None
    score: float = Field(ge=0)
