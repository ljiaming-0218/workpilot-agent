"""Read knowledge documents from MySQL; ranking lives in retrieval_service."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.knowledge import KnowledgeDoc


def list_knowledge_documents(
    session: Session,
    category: str | None = None,
) -> list[KnowledgeDoc]:
    statement = select(KnowledgeDoc)
    if category is not None:
        statement = statement.where(KnowledgeDoc.category == category)
    statement = statement.order_by(KnowledgeDoc.id)
    return list(session.scalars(statement))
