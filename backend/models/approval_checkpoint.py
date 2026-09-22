"""Durable approval state used when LangGraph memory is lost on restart."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.common import TABLE_OPTIONS, TimestampMixin


class AgentApprovalCheckpoint(TimestampMixin, Base):
    __tablename__ = "agent_approval_checkpoints"
    __table_args__ = (TABLE_OPTIONS.copy(),)

    agent_run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", name="fk_approval_checkpoints_agent_run", ondelete="RESTRICT"),
        primary_key=True,
    )
    request_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON(none_as_null=False))
    decision: Mapped[str | None] = mapped_column(String(16))
    expires_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), index=True)
