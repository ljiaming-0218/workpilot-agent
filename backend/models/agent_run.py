"""Persistence contract for one Agent run, without any execution logic."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, String
from sqlalchemy.dialects.mysql import DATETIME, LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models.common import TABLE_OPTIONS, enum_type, utc_now
from backend.models.enums import AgentRunStatus

if TYPE_CHECKING:
    from backend.models.tool_call import ToolCallLog


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint("latency_ms >= 0", name="ck_agent_runs_latency_nonnegative"),
        TABLE_OPTIONS.copy(),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    user_query: Mapped[str] = mapped_column(LONGTEXT)
    intent: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[AgentRunStatus] = mapped_column(
        enum_type(AgentRunStatus), default=AgentRunStatus.RUNNING, index=True
    )
    final_answer: Mapped[str | None] = mapped_column(LONGTEXT)
    started_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    latency_ms: Mapped[int | None]

    # Retain audit history: deleting a run with tool calls must be rejected by MySQL.
    tool_calls: Mapped[list[ToolCallLog]] = relationship(
        back_populates="agent_run", passive_deletes="all"
    )
