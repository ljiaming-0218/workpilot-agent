"""Read-only log queries; timestamps in MySQL are naive UTC."""

from datetime import timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.error_log import ErrorLog
from backend.schemas.common import Page
from backend.schemas.log import LogFilters, LogRead


def list_logs(session: Session, filters: LogFilters) -> Page[LogRead]:
    """Return a filtered page; time windows include the start and exclude the end."""
    conditions = []
    for name in ("service_name", "level", "error_type", "request_id"):
        value = getattr(filters, name)
        if value is not None:
            conditions.append(getattr(ErrorLog, name) == value)
    if filters.created_from is not None:
        start = filters.created_from.astimezone(timezone.utc).replace(tzinfo=None)
        conditions.append(ErrorLog.created_at >= start)
    if filters.created_to is not None:
        end = filters.created_to.astimezone(timezone.utc).replace(tzinfo=None)
        conditions.append(ErrorLog.created_at < end)

    total = session.scalar(select(func.count()).select_from(ErrorLog).where(*conditions))
    order = (
        (ErrorLog.id.desc(),)
        if filters.sort_by == "added"
        else (ErrorLog.created_at.desc(), ErrorLog.id.desc())
    )
    statement = (
        select(ErrorLog)
        .where(*conditions)
        .order_by(*order)
        .offset(filters.offset)
        .limit(filters.page_size)
    )
    items = [LogRead.model_validate(log) for log in session.scalars(statement)]
    return Page(items=items, total=total, page=filters.page, page_size=filters.page_size)
