"""Shared ORM timestamps and MySQL storage conventions."""

from datetime import datetime, timezone
from enum import Enum as PythonEnum

from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column


TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def utc_now() -> datetime:
    """Store UTC as naive DATETIME; convert to a display timezone at the API boundary."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def enum_type(enum_class: type[PythonEnum]) -> SQLAlchemyEnum:
    # Persist API values such as "open", rather than Python member names such as "OPEN".
    return SQLAlchemyEnum(
        enum_class,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
        name=enum_class.__name__.lower(),
    )


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=utc_now)


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), default=utc_now, onupdate=utc_now
    )
