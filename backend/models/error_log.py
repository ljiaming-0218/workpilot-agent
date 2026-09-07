"""Service log records used as incident evidence."""

from sqlalchemy import Index, String
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.common import TABLE_OPTIONS, CreatedAtMixin, enum_type
from backend.models.enums import LogLevel


class ErrorLog(CreatedAtMixin, Base):
    __tablename__ = "error_logs"
    __table_args__ = (Index("ix_error_logs_created_at", "created_at"), TABLE_OPTIONS.copy())

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    service_name: Mapped[str] = mapped_column(String(128), index=True)
    level: Mapped[LogLevel] = mapped_column(enum_type(LogLevel), index=True)
    error_type: Mapped[str | None] = mapped_column(String(128), index=True)
    message: Mapped[str] = mapped_column(LONGTEXT)
    stack_trace: Mapped[str | None] = mapped_column(LONGTEXT)
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
