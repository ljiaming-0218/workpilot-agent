"""Transactional queue primitives, intentionally not connected to run execution.

Each public operation owns its transaction except add_job, which participates in
the caller's transaction so the run and queued job can be inserted atomically.
"""

from dataclasses import dataclass
from collections.abc import Callable
from datetime import timedelta
from hashlib import sha256
import json
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from backend.models.durable_job import DurableJob, DurableJobAttempt


@dataclass(frozen=True, slots=True)
class Claim:
    job_id: int
    run_id: int
    attempt: int
    token: str


class IdempotencyConflict(ValueError):
    """The same submission key was reused with a different request."""


def digests(key: str, query: str) -> tuple[str, str]:
    """Hash a client submission key and the versioned, validated request body."""
    if not key or len(key) > 200:
        raise ValueError("Invalid idempotency key")
    body = json.dumps({"version": 1, "query": query}, ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"))
    return sha256(key.encode()).hexdigest(), sha256(body.encode()).hexdigest()


def find_submission(session: Session, *, team_id: int, user_id: int,
                    key_digest: str, request_digest: str) -> DurableJob | None:
    """Find a prior submission. The caller must also check current run access."""
    job = session.scalar(select(DurableJob).where(
        DurableJob.team_id == team_id, DurableJob.creator_user_id == user_id,
        DurableJob.idempotency_digest == key_digest,
    ))
    if job is not None and job.request_digest != request_digest:
        raise IdempotencyConflict("The submission key belongs to a different request")
    return job


def add_job(session: Session, *, run_id: int, team_id: int, user_id: int,
            key_digest: str, request_digest: str, retry_safe: bool = False,
            max_attempts: int = 3) -> DurableJob:
    """Insert a job without committing; database uniqueness handles races."""
    if not 1 <= max_attempts <= 10:
        raise ValueError("max_attempts must be between 1 and 10")
    job = DurableJob(agent_run_id=run_id, team_id=team_id, creator_user_id=user_id,
                     idempotency_digest=key_digest, request_digest=request_digest,
                     status="queued", attempts=0, max_attempts=max_attempts,
                     retry_safe=retry_safe)
    session.add(job)
    session.flush()
    return job


def _database_now(session: Session):
    return session.scalar(select(func.utc_timestamp(6)))


def claim_next(factory: sessionmaker[Session], worker_id: str,
               lease_seconds: int = 60, *,
               on_exhausted: Callable[[Session, int, bool], None] | None = None) -> Claim | None:
    """Claim one queued or expired job with a compare-and-swap update.

    Database transaction conflicts are deliberately propagated to the worker,
    which must retry with backoff. An expired unsafe job is failed, never replayed.
    """
    if not worker_id or len(worker_id) > 128 or not 1 <= lease_seconds <= 3600:
        raise ValueError("Invalid worker or lease")
    with factory.begin() as session:
        now = _database_now(session)
        candidate = session.scalar(select(DurableJob).where(or_(
            DurableJob.status == "queued",
            and_(DurableJob.status == "running", DurableJob.lease_until <= now),
        )).order_by(DurableJob.id).limit(1))
        if candidate is None:
            return None
        old_status, old_token = candidate.status, candidate.claim_token
        expired = old_status == "running"
        can_retry = not expired or (candidate.retry_safe and candidate.attempts < candidate.max_attempts)
        token = uuid4().hex if can_retry else None
        conditions = [DurableJob.id == candidate.id, DurableJob.status == old_status]
        if expired:
            conditions.extend((DurableJob.claim_token == old_token,
                               DurableJob.lease_until <= func.utc_timestamp(6)))
        values = {
            "status": "running" if can_retry else "failed",
            "claim_token": token,
            "worker_id": worker_id if can_retry else None,
            "lease_until": now + timedelta(seconds=lease_seconds) if can_retry else None,
            "updated_at": now,
        }
        if can_retry:
            values["attempts"] = candidate.attempts + 1
        result = session.execute(update(DurableJob).where(*conditions).values(**values)
                                 .execution_options(synchronize_session=False))
        if result.rowcount != 1:
            return None
        if expired:
            changed = session.execute(update(DurableJobAttempt).where(
                DurableJobAttempt.job_id == candidate.id,
                DurableJobAttempt.claim_token == old_token,
                DurableJobAttempt.status == "running",
            ).values(status="expired", finished_at=now, error_code="LEASE_EXPIRED"))
            if changed.rowcount != 1:
                raise RuntimeError("Missing active attempt for expired job")
        if not can_retry:
            if on_exhausted is not None:
                on_exhausted(session, candidate.agent_run_id, candidate.retry_safe)
            return None
        number = candidate.attempts + 1
        session.add(DurableJobAttempt(job_id=candidate.id, number=number, worker_id=worker_id,
                                      claim_token=token, status="running", started_at=now))
        return Claim(candidate.id, candidate.agent_run_id, number, token)


def renew(factory: sessionmaker[Session], claim: Claim, lease_seconds: int = 60) -> bool:
    if not 1 <= lease_seconds <= 3600:
        raise ValueError("Invalid lease")
    with factory.begin() as session:
        now = _database_now(session)
        result = session.execute(update(DurableJob).where(
            DurableJob.id == claim.job_id, DurableJob.status == "running",
            DurableJob.claim_token == claim.token,
            DurableJob.lease_until > func.utc_timestamp(6),
        ).values(lease_until=now + timedelta(seconds=lease_seconds), updated_at=now))
        return result.rowcount == 1


def finish_claim(factory: sessionmaker[Session], claim: Claim, *, succeeded: bool,
                 error_code: str | None = None) -> bool:
    """Finish queue metadata only. This does not publish any Agent result."""
    with factory.begin() as session:
        now = _database_now(session)
        status = "completed" if succeeded else "failed"
        result = session.execute(update(DurableJob).where(
            DurableJob.id == claim.job_id, DurableJob.status == "running",
            DurableJob.claim_token == claim.token,
            DurableJob.lease_until > func.utc_timestamp(6),
        ).values(status=status, claim_token=None, worker_id=None, lease_until=None, updated_at=now))
        if result.rowcount != 1:
            return False
        changed = session.execute(update(DurableJobAttempt).where(
            DurableJobAttempt.job_id == claim.job_id,
            DurableJobAttempt.claim_token == claim.token,
            DurableJobAttempt.status == "running",
            DurableJobAttempt.execution_run_id.is_(None),
        ).values(status=status, finished_at=now, error_code=error_code))
        if changed.rowcount != 1:
            raise RuntimeError("Missing active attempt or attempt requires result publication")
        return True
