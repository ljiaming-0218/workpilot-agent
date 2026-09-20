"""Cache BM25 corpora by database content version and return bounded evidence."""

from dataclasses import dataclass
from datetime import datetime
from threading import RLock

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.knowledge import KnowledgeDoc
from backend.retrieval.retriever import BM25Retriever, RetrievalDocument
from backend.schemas.knowledge import KnowledgeSearchHit
from backend.services.knowledge_service import list_knowledge_documents


@dataclass(frozen=True, slots=True)
class _KnowledgeVersion:
    count: int
    latest_update: datetime | None
    highest_id: int | None


@dataclass(frozen=True, slots=True)
class _CachedIndex:
    version: _KnowledgeVersion
    retriever: BM25Retriever


_index_lock = RLock()
_index_cache: dict[tuple[int, str | None], _CachedIndex] = {}


def search_knowledge(
    session: Session,
    query: str,
    *,
    category: str | None = None,
    limit: int = 5,
) -> tuple[list[KnowledgeSearchHit], int]:
    retriever = _get_retriever(session, category)
    result = retriever.search(query, limit)
    hits = [
        KnowledgeSearchHit(
            document_id=hit.document.id,
            title=hit.document.title,
            excerpt=_excerpt(hit.document.content),
            category=hit.document.category,
            source=hit.document.source,
            score=round(hit.score, 6),
        )
        for hit in result.hits
    ]
    return hits, result.total


def _get_retriever(session: Session, category: str | None) -> BM25Retriever:
    """Reuse an immutable index until the database corpus version changes."""
    version = _knowledge_version(session, category)
    cache_key = (id(session.get_bind()), category)
    with _index_lock:
        cached = _index_cache.get(cache_key)
        if cached is not None and cached.version == version:
            return cached.retriever

        rows = list_knowledge_documents(session, category)
        documents = [
            RetrievalDocument(
                id=row.id,
                title=row.title,
                content=row.content,
                category=row.category,
                source=row.source,
            )
            for row in rows
        ]
        retriever = BM25Retriever(documents)
        _index_cache[cache_key] = _CachedIndex(version=version, retriever=retriever)
        return retriever


def _knowledge_version(
    session: Session,
    category: str | None,
) -> _KnowledgeVersion:
    statement = select(
        func.count(KnowledgeDoc.id),
        func.max(KnowledgeDoc.updated_at),
        func.max(KnowledgeDoc.id),
    )
    if category is not None:
        statement = statement.where(KnowledgeDoc.category == category)
    count, latest_update, highest_id = session.execute(statement).one()
    return _KnowledgeVersion(
        count=int(count),
        latest_update=latest_update,
        highest_id=highest_id,
    )


def _excerpt(content: str, max_length: int = 500) -> str:
    normalized = " ".join(content.split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 3].rstrip() + "..."
