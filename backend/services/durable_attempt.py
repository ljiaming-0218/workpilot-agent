"""Isolate execution per attempt and publish only the current claim's result.

These primitives are not wired to HTTP or an executing worker yet. In particular,
approval and history readers must resolve a published child before integration.
"""

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from backend.models.agent_run import AgentRun, AgentRunService
from backend.models.approval_checkpoint import AgentApprovalCheckpoint
from backend.models.durable_job import DurableJob, DurableJobAttempt
from backend.models.enums import AgentRunStatus
from backend.services.access_service import resolve_run_scope
from backend.services.durable_queue import Claim


@dataclass(frozen=True, slots=True)
class AttemptExecution:
    run_id: int
    request_id: str
    query: str
    retry_safe: bool


def start_attempt(factory: sessionmaker[Session], claim: Claim) -> AttemptExecution | None:
    """Allocate a private run once, atomically with the attempt link.

    Row locking serializes allocation with renewal, expiration, and publication.
    An already allocated attempt is not returned again: launching it twice would
    violate the isolation contract even though the token itself still matches.
    """
    with factory.begin() as session:
        job = session.scalar(select(DurableJob).where(
            DurableJob.id == claim.job_id, DurableJob.agent_run_id == claim.run_id,
            DurableJob.status == "running", DurableJob.claim_token == claim.token,
            DurableJob.lease_until > func.utc_timestamp(6),
        ).with_for_update())
        if job is None:
            return None
        attempt = session.scalar(select(DurableJobAttempt).where(
            DurableJobAttempt.job_id == job.id, DurableJobAttempt.number == claim.attempt,
            DurableJobAttempt.claim_token == claim.token, DurableJobAttempt.status == "running",
        ))
        if attempt is None or attempt.execution_run_id is not None:
            raise RuntimeError("Attempt missing or already allocated")
        root = session.get(AgentRun, claim.run_id)
        if root is None or root.team_id != job.team_id or root.creator_user_id != job.creator_user_id:
            raise ValueError("Job identity does not match the submitted run")
        _, services = resolve_run_scope(session, root.id)
        child = AgentRun(
            request_id=uuid4().hex, user_query=root.user_query,
            creator_user_id=root.creator_user_id, team_id=root.team_id,
            service_scope_recorded=True, status=AgentRunStatus.RUNNING,
        )
        session.add(child)
        session.flush()
        session.add_all(AgentRunService(agent_run_id=child.id, service_name=name)
                        for name in sorted(services))
        attempt.execution_run_id = child.id
        return AttemptExecution(child.id, child.request_id, child.user_query, job.retry_safe)


def publish_attempt(factory: sessionmaker[Session], claim: Claim) -> bool:
    """Publish a finished isolated run and queue status in the same transaction.

    A stale token or expired lease returns False without changing the public run.
    A waiting approval is publishable only with its matching durable checkpoint.
    """
    with factory.begin() as session:
        attempt = session.scalar(select(DurableJobAttempt).where(
            DurableJobAttempt.job_id == claim.job_id, DurableJobAttempt.number == claim.attempt,
            DurableJobAttempt.claim_token == claim.token, DurableJobAttempt.status == "running",
        ))
        if attempt is None or attempt.execution_run_id is None:
            return False
        child = session.get(AgentRun, attempt.execution_run_id)
        terminal = {AgentRunStatus.COMPLETED, AgentRunStatus.FAILED, AgentRunStatus.WAITING_APPROVAL}
        if child is None or child.status not in terminal:
            raise ValueError("Attempt execution has not reached a publishable state")
        if child.status == AgentRunStatus.WAITING_APPROVAL:
            checkpoint = session.scalar(select(AgentApprovalCheckpoint).where(
                AgentApprovalCheckpoint.agent_run_id == child.id,
            ).with_for_update())
            now = session.scalar(select(func.utc_timestamp(6)))
            if (checkpoint is None or checkpoint.request_id != child.request_id
                    or checkpoint.decision is not None or checkpoint.expires_at <= now):
                raise ValueError("Attempt has no valid pending approval checkpoint")
        now = session.scalar(select(func.utc_timestamp(6)))
        queue_status = child.status.value
        result = session.execute(update(DurableJob).where(
            DurableJob.id == claim.job_id, DurableJob.agent_run_id == claim.run_id,
            DurableJob.status == "running", DurableJob.claim_token == claim.token,
            DurableJob.lease_until > func.utc_timestamp(6),
        ).values(status=queue_status, published_run_id=child.id, claim_token=None,
                 worker_id=None, lease_until=None, updated_at=now))
        if result.rowcount != 1:
            return False
        root = session.get(AgentRun, claim.run_id)
        job = session.get(DurableJob, claim.job_id)
        if (root is None or job is None or child.team_id != root.team_id
                or child.creator_user_id != root.creator_user_id
                or root.team_id != job.team_id or root.creator_user_id != job.creator_user_id
                or child.user_query != root.user_query or not child.service_scope_recorded):
            raise ValueError("Attempt identity does not match the submitted run")
        root_scope = set(session.scalars(select(AgentRunService.service_name).where(
            AgentRunService.agent_run_id == root.id)))
        child_scope = set(session.scalars(select(AgentRunService.service_name).where(
            AgentRunService.agent_run_id == child.id)))
        if root_scope != child_scope:
            raise ValueError("Attempt scope does not match the submitted run")
        root.intent = child.intent
        root.status = child.status
        root.final_answer = child.final_answer
        root.finished_at = child.finished_at
        root.latency_ms = child.latency_ms
        attempt.status = queue_status
        attempt.finished_at = now
        return True


def fail_unstarted_attempt(factory: sessionmaker[Session], claim: Claim, error_code: str) -> bool:
    """Fail a claim that never allocated an execution run, including its public run."""
    with factory.begin() as session:
        now = session.scalar(select(func.utc_timestamp(6)))
        result = session.execute(update(DurableJob).where(
            DurableJob.id == claim.job_id, DurableJob.agent_run_id == claim.run_id,
            DurableJob.status == "running", DurableJob.claim_token == claim.token,
            DurableJob.lease_until > func.utc_timestamp(6),
        ).values(status="failed", claim_token=None, worker_id=None, lease_until=None, updated_at=now))
        if result.rowcount != 1:
            return False
        result = session.execute(update(DurableJobAttempt).where(
            DurableJobAttempt.job_id == claim.job_id, DurableJobAttempt.number == claim.attempt,
            DurableJobAttempt.claim_token == claim.token, DurableJobAttempt.status == "running",
            DurableJobAttempt.execution_run_id.is_(None),
        ).values(status="failed", finished_at=now, error_code=error_code))
        if result.rowcount != 1:
            raise RuntimeError("Claim already allocated or attempt missing")
        root = session.get(AgentRun, claim.run_id)
        if root is None:
            raise ValueError("Submitted run no longer exists")
        root.status = AgentRunStatus.FAILED
        root.final_answer = error_code
        root.finished_at = now
        return True
