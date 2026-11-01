"""Persistent queue metadata; not yet connected to Agent execution."""

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.common import TABLE_OPTIONS, utc_now


class DurableJob(Base):
    __tablename__ = "durable_jobs"
    __table_args__ = (
        UniqueConstraint("team_id", "creator_user_id", "idempotency_digest", name="uq_job_submission"),
        UniqueConstraint("agent_run_id", name="uq_job_run"),
        Index("ix_job_claim", "status", "lease_until", "id"),
        TABLE_OPTIONS.copy(),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    agent_run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"))
    team_id: Mapped[int] = mapped_column(Integer)
    creator_user_id: Mapped[int] = mapped_column(Integer)
    idempotency_digest: Mapped[str] = mapped_column(String(64))
    request_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    retry_safe: Mapped[bool] = mapped_column(Boolean, default=False)
    worker_id: Mapped[str | None] = mapped_column(String(128))
    claim_token: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    published_run_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=utc_now)


class DurableJobAttempt(Base):
    __tablename__ = "durable_job_attempts"
    __table_args__ = (
        UniqueConstraint("job_id", "number", name="uq_job_attempt_number"),
        UniqueConstraint("claim_token", name="uq_job_attempt_token"),
        TABLE_OPTIONS.copy(),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("durable_jobs.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[str] = mapped_column(String(128))
    claim_token: Mapped[str] = mapped_column(String(64))
    execution_run_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    started_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6))
    finished_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    error_code: Mapped[str | None] = mapped_column(String(64))
