"""Alert provenance and human collaboration belong to the stable public run."""

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import DATETIME, JSON
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.common import TABLE_OPTIONS, utc_now


class IncidentSubmission(Base):
    __tablename__ = "incident_submissions"
    __table_args__ = (
        UniqueConstraint("team_id", "source", "event_id", name="uq_incident_event"),
        TABLE_OPTIONS.copy(),
    )

    agent_run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), primary_key=True)
    team_id: Mapped[int] = mapped_column(Integer, index=True)
    source: Mapped[str] = mapped_column(String(64))
    event_id: Mapped[str] = mapped_column(String(128))
    source_kind: Mapped[str] = mapped_column(String(24))
    payload_digest: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=utc_now)


class InvestigationReview(Base):
    __tablename__ = "investigation_reviews"
    __table_args__ = (TABLE_OPTIONS.copy(),)

    agent_run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    handling_status: Mapped[str] = mapped_column(String(24), default="open")
    feedback: Mapped[str] = mapped_column(String(24), default="unreviewed")
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=utc_now)


class InvestigationNote(Base):
    """Append-only review history; no model or Worker writes to this table."""

    __tablename__ = "investigation_notes"
    __table_args__ = (
        UniqueConstraint("agent_run_id", "version", name="uq_investigation_revision"),
        TABLE_OPTIONS.copy(),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    agent_run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    owner_user_id: Mapped[int | None] = mapped_column(Integer)
    handling_status: Mapped[str] = mapped_column(String(24))
    feedback: Mapped[str] = mapped_column(String(24))
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=utc_now)
