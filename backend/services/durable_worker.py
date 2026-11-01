"""Worker orchestration for isolated attempts, pending integration with HTTP."""

from collections.abc import Callable
import logging
from threading import Event, Thread

from sqlalchemy.orm import Session, sessionmaker

from backend.models.agent_run import AgentRun
from backend.models.common import utc_now
from backend.models.enums import AgentRunStatus
from backend.services.durable_attempt import (
    AttemptExecution, fail_unstarted_attempt, publish_attempt, start_attempt,
)
from backend.services.durable_queue import claim_next, renew


logger = logging.getLogger(__name__)


def run_one(factory: sessionmaker[Session], worker_id: str,
            execute: Callable[[AttemptExecution], None], *,
            lease_seconds: int = 60, heartbeat_seconds: float = 15) -> bool:
    """Claim and run at most one attempt. Return False if no job was claimed.

    `execute` must write only to the supplied private run. It may be called more
    than once for a retry-safe job after a process dies. Lease loss does not cancel
    an in-flight operation; publication is always independently fenced.
    """
    if not 0 < heartbeat_seconds < lease_seconds:
        raise ValueError("Heartbeat must be shorter than the lease")
    claim = claim_next(factory, worker_id, lease_seconds, on_exhausted=_mark_exhausted)
    if claim is None:
        return False
    try:
        execution = start_attempt(factory, claim)
    except ValueError:
        fail_unstarted_attempt(factory, claim, "SCOPE_CHANGED")
        return True
    if execution is None:
        return True  # lease expired while allocating; the next owner recovers it

    stop = Event()
    def heartbeat() -> None:
        while not stop.wait(heartbeat_seconds):
            try:
                if not renew(factory, claim, lease_seconds):
                    return
            except Exception:
                # A transient database failure is not evidence that the lease
                # was renewed. Keep trying; the final write is fenced regardless.
                logger.warning("P3_HEARTBEAT_FAILED: job_id=%s", claim.job_id)

    thread = Thread(target=heartbeat, name=f"p3-lease-{claim.job_id}", daemon=True)
    thread.start()
    try:
        try:
            execute(execution)
        except Exception:
            logger.exception("P3_EXECUTION_FAILED: job_id=%s", claim.job_id)
            _fail_private_run(factory, execution.run_id)
        else:
            # An adapter that returns without finishing the child must not leave
            # a successfully completed job with no usable result.
            with factory() as session:
                child = session.get(AgentRun, execution.run_id)
                unfinished = child is None or child.status == AgentRunStatus.RUNNING
            if unfinished:
                _fail_private_run(factory, execution.run_id)
        try:
            publish_attempt(factory, claim)
        except ValueError:
            # E.g. an invalid approval checkpoint; never expose it as pending.
            _fail_private_run(factory, execution.run_id, "INVALID_ATTEMPT_RESULT")
            publish_attempt(factory, claim)
    finally:
        stop.set()
        thread.join(timeout=5)
    return True


def _fail_private_run(factory: sessionmaker[Session], run_id: int,
                      message: str = "ATTEMPT_EXECUTION_FAILED") -> None:
    with factory.begin() as session:
        run = session.get(AgentRun, run_id)
        if run is not None:
            run.status = AgentRunStatus.FAILED
            run.final_answer = message
            run.finished_at = utc_now()


def _mark_exhausted(session: Session, run_id: int, retry_safe: bool) -> None:
    """Called inside the transaction that closes an expired queue claim."""
    run = session.get(AgentRun, run_id)
    if run is None:
        raise ValueError("Submitted run no longer exists")
    run.status = AgentRunStatus.FAILED
    run.final_answer = "RETRY_LIMIT_REACHED" if retry_safe else "EXECUTION_OUTCOME_UNKNOWN"
    run.finished_at = utc_now()
