"""Read knowledge documents from MySQL; ranking lives in retrieval_service."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.knowledge import KnowledgeDoc
from backend.schemas.common import Page, PaginationParams
from backend.schemas.knowledge import KnowledgeRead


def list_knowledge_documents(
    session: Session,
    category: str | None = None,
) -> list[KnowledgeDoc]:
    statement = select(KnowledgeDoc)
    if category is not None:
        statement = statement.where(KnowledgeDoc.category == category)
    statement = statement.order_by(KnowledgeDoc.id)
    return list(session.scalars(statement))


def list_knowledge_page(
    session: Session, pagination: PaginationParams,
) -> Page[KnowledgeRead]:
    """Show newest documents without changing the BM25 corpus query."""
    total = session.scalar(select(func.count()).select_from(KnowledgeDoc)) or 0
    statement = (
        select(KnowledgeDoc)
        .order_by(KnowledgeDoc.updated_at.desc(), KnowledgeDoc.id.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    )
    items = [KnowledgeRead.model_validate(row) for row in session.scalars(statement)]
    return Page(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )
