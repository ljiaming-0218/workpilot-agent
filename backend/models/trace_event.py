"""Ordered observability event belonging to one Agent run."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.mysql import DATETIME, LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models.common import TABLE_OPTIONS, enum_type, utc_now
from backend.models.enums import TraceStatus

if TYPE_CHECKING:
    from backend.models.agent_run import AgentRun


class AgentTraceEvent(Base):
    __tablename__ = "agent_trace_events"
    __table_args__ = (
        CheckConstraint("step >= 1", name="ck_trace_events_step_positive"),
        CheckConstraint("latency_ms >= 0", name="ck_trace_events_latency_nonnegative"),
        UniqueConstraint("agent_run_id", "step", name="uq_trace_events_run_step"),
        TABLE_OPTIONS.copy(),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    agent_run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", name="fk_trace_events_agent_run", ondelete="RESTRICT"),
        index=True,
    )
    step: Mapped[int]
    node: Mapped[str] = mapped_column(String(128), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    intent: Mapped[str | None] = mapped_column(String(64))
    tool_name: Mapped[str | None] = mapped_column(String(128))
    input_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    tool_input: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    tool_output_summary: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True)
    )
    sql: Mapped[str | None] = mapped_column(LONGTEXT)
    sql_guard_result: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True)
    )
    retrieval_result: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True)
    )
    model: Mapped[str | None] = mapped_column(String(255))
    tokens_json: Mapped[dict[str, int] | None] = mapped_column(JSON(none_as_null=True))
    latency_ms: Mapped[int]
    status: Mapped[TraceStatus] = mapped_column(enum_type(TraceStatus), index=True)
    error: Mapped[str | None] = mapped_column(LONGTEXT)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=utc_now)

    agent_run: Mapped[AgentRun] = relationship(back_populates="trace_events")
