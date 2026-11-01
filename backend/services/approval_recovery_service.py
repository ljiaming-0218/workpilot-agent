"""Reconcile durable approval records when a process starts."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.agent_run import AgentRun
from backend.models.approval_checkpoint import AgentApprovalCheckpoint
from backend.models.common import utc_now
from backend.models.enums import AgentRunStatus
from backend.services.approval_lock import approval_lock


def reconcile_approval_checkpoints(session: Session) -> dict[str, int]:
    entries = session.execute(select(AgentApprovalCheckpoint.agent_run_id,
                                     AgentApprovalCheckpoint.request_id)).all()
    session.rollback()
    counts = {"waiting": 0, "failed": 0, "active": 0}
    for run_id, request_id in entries:
        with approval_lock(session.get_bind(), request_id) as acquired:
            if not acquired:
                counts["active"] += 1
                continue
            result = _reconcile_one(session, run_id)
            for key, value in result.items():
                counts[key] += value
    return counts


def _reconcile_one(session: Session, run_id: int) -> dict[str, int]:
    """Restore safe pending states and fail ambiguous or expired claims."""
    counts = {"waiting": 0, "failed": 0}
    checkpoints = session.scalars(select(AgentApprovalCheckpoint).where(
        AgentApprovalCheckpoint.agent_run_id == run_id)).all()
    now = utc_now()
    for checkpoint in checkpoints:
        run = session.get(AgentRun, checkpoint.agent_run_id)
        if run is None or run.status in {AgentRunStatus.COMPLETED, AgentRunStatus.FAILED}:
            session.delete(checkpoint)
            continue
        if checkpoint.expires_at <= now:
            run.status = AgentRunStatus.FAILED
            run.final_answer = "APPROVAL_CHECKPOINT_EXPIRED"
            run.finished_at = now
            session.delete(checkpoint)
            counts["failed"] += 1
            continue
        if checkpoint.decision is not None:
            run.status = AgentRunStatus.FAILED
            run.final_answer = "APPROVAL_EXECUTION_OUTCOME_UNKNOWN"
            run.finished_at = now
            session.delete(checkpoint)
            counts["failed"] += 1
            continue
        run.status = AgentRunStatus.WAITING_APPROVAL
        run.finished_at = None
        counts["waiting"] += 1
    session.commit()
    return counts
