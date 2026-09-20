"""Atomic imports for external logs and versioned knowledge documents."""

from datetime import timezone

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.models.error_log import ErrorLog
from backend.models.knowledge import KnowledgeDoc
from backend.schemas.ingestion import (
    ImportResult,
    KnowledgeImportRequest,
    LogImportRequest,
)


def import_logs(session: Session, payload: LogImportRequest) -> ImportResult:
    """Create immutable log events and skip duplicate request IDs."""
    unique_items = {}
    for item in payload.items:
        unique_items.setdefault(item.request_id, item)

    request_ids = set(unique_items)
    existing_ids = set(
        session.scalars(
            select(ErrorLog.request_id).where(ErrorLog.request_id.in_(request_ids))
        )
    )
    rows = []
    for request_id, item in unique_items.items():
        if request_id in existing_ids:
            continue
        values = item.model_dump(exclude={"created_at"})
        if item.created_at is not None:
            values["created_at"] = (
                item.created_at.astimezone(timezone.utc).replace(tzinfo=None)
            )
        rows.append(ErrorLog(**values))

    try:
        session.add_all(rows)
        session.flush()
        result = ImportResult(
            requested=len(payload.items),
            created=len(rows),
            updated=0,
            skipped=len(payload.items) - len(rows),
            item_ids=[row.id for row in rows],
        )
        session.commit()
        return result
    except SQLAlchemyError:
        session.rollback()
        raise


def import_knowledge(
    session: Session,
    payload: KnowledgeImportRequest,
) -> ImportResult:
    """Create or update documents by stable source and skip unchanged content."""
    unique_items = {}
    for item in payload.items:
        unique_items.setdefault(item.source, item)

    existing = {
        row.source: row
        for row in session.scalars(
            select(KnowledgeDoc).where(KnowledgeDoc.source.in_(set(unique_items)))
        )
    }
    created_rows = []
    updated_rows = []
    for source, item in unique_items.items():
        row = existing.get(source)
        if row is None:
            row = KnowledgeDoc(**item.model_dump())
            session.add(row)
            created_rows.append(row)
            continue
        changed = any(
            getattr(row, field) != getattr(item, field)
            for field in ("title", "content", "category")
        )
        if changed:
            row.title = item.title
            row.content = item.content
            row.category = item.category
            updated_rows.append(row)

    try:
        session.flush()
        changed_rows = [*created_rows, *updated_rows]
        result = ImportResult(
            requested=len(payload.items),
            created=len(created_rows),
            updated=len(updated_rows),
            skipped=len(payload.items) - len(changed_rows),
            item_ids=[row.id for row in changed_rows],
        )
        session.commit()
        return result
    except SQLAlchemyError:
        session.rollback()
        raise

