"""Build a BM25 corpus from MySQL knowledge documents and return bounded evidence."""

from sqlalchemy.orm import Session

from backend.retrieval.retriever import BM25Retriever, RetrievalDocument
from backend.schemas.knowledge import KnowledgeSearchHit
from backend.services.knowledge_service import list_knowledge_documents


def search_knowledge(
    session: Session,
    query: str,
    *,
    category: str | None = None,
    limit: int = 5,
) -> tuple[list[KnowledgeSearchHit], int]:
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
    result = BM25Retriever(documents).search(query, limit)
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


def _excerpt(content: str, max_length: int = 500) -> str:
    normalized = " ".join(content.split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 3].rstrip() + "..."
