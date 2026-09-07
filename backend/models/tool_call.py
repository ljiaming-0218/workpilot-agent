"""One tool invocation belonging to an Agent run."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey, String
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models.common import TABLE_OPTIONS, CreatedAtMixin, enum_type
from backend.models.enums import RiskLevel, ToolCallStatus

if TYPE_CHECKING:
    from backend.models.agent_run import AgentRun


class ToolCallLog(CreatedAtMixin, Base):
    __tablename__ = "tool_call_logs"
    __table_args__ = (
        CheckConstraint("latency_ms >= 0", name="ck_tool_call_logs_latency_nonnegative"),
        TABLE_OPTIONS.copy(),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    agent_run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", name="fk_tool_call_logs_agent_run", ondelete="RESTRICT"),
        index=True,
    )
    tool_name: Mapped[str] = mapped_column(String(128))
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON(none_as_null=True))
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    risk_level: Mapped[RiskLevel] = mapped_column(enum_type(RiskLevel))
    status: Mapped[ToolCallStatus] = mapped_column(
        enum_type(ToolCallStatus), default=ToolCallStatus.RUNNING
    )
    latency_ms: Mapped[int | None]
    error_message: Mapped[str | None] = mapped_column(LONGTEXT)

    agent_run: Mapped[AgentRun] = relationship(back_populates="tool_calls")
