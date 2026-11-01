"""Authorized public views and progress for durable Agent tasks."""

import json
from time import sleep

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from backend.agent.trace import get_agent_trace
from backend.models.agent_run import AgentRun
from backend.models.auth_session import AuthSession
from backend.models.common import utc_now
from backend.models.durable_job import DurableJob, DurableJobAttempt
from backend.models.enums import AgentRunStatus
from backend.models.team import TeamMember
from backend.models.trace_event import AgentTraceEvent
from backend.services.access_service import run_access_filter
from backend.services.agent_service import AgentRunNotFoundError
from backend.services.auth_service import token_digest
from backend.services.run_history_service import get_agent_run_detail


def require_public_run(session: Session, run_id: int, team_id: int) -> AgentRun:
    child = exists(select(DurableJobAttempt.id).where(DurableJobAttempt.execution_run_id == AgentRun.id))
    run = session.scalar(select(AgentRun).where(AgentRun.id == run_id, run_access_filter(team_id), ~child))
    if run is None:
        raise AgentRunNotFoundError("Agent run does not exist")
    return run


def find_job(session: Session, run_id: int) -> DurableJob | None:
    return session.scalar(select(DurableJob).where(DurableJob.agent_run_id == run_id))


def sync_published_approval(session: Session, root: AgentRun, job: DurableJob) -> None:
    """Repair a published approval result if the response process was interrupted."""
    if job.status != "waiting_approval" or job.published_run_id is None:
        return
    child = session.get(AgentRun, job.published_run_id)
    if child is None or child.status not in {AgentRunStatus.COMPLETED, AgentRunStatus.FAILED}:
        return
    root.status, root.intent, root.final_answer = child.status, child.intent, child.final_answer
    root.finished_at, root.latency_ms = child.finished_at, child.latency_ms
    job.status = child.status.value
    session.commit()


def public_detail(session: Session, root: AgentRun):
    job = find_job(session, root.id)
    if job:
        sync_published_approval(session, root, job)
    detail = get_agent_run_detail(session, job.published_run_id if job and job.published_run_id else root.id)
    return detail.model_copy(update={"id": root.id, "request_id": root.request_id,
                                    "status": root.status, "final_answer": root.final_answer,
                                    "queue_status": job.status if job else None})


def public_trace(session: Session, root: AgentRun):
    job = find_job(session, root.id)
    trace = get_agent_trace(session, job.published_run_id if job and job.published_run_id else root.id)
    return trace.model_copy(update={"run_id": root.id, "request_id": root.request_id, "run_status": root.status})


def stream_progress(factory, *, run_id: int, team_id: int, user_id: int,
                    cookie: str, after_id: int = 0):
    """Poll only the currently leased attempt, rechecking session and access."""
    cursor = after_id
    previous_attempt = None
    token_hash = token_digest(cookie)
    while True:
        outgoing = []
        terminal = False
        with factory() as session:
            authorized = session.scalar(select(AuthSession.id).where(
                AuthSession.token_hash == token_hash, AuthSession.user_id == user_id,
                AuthSession.expires_at > utc_now()))
            member = session.scalar(select(TeamMember.id).where(
                TeamMember.team_id == team_id, TeamMember.user_id == user_id))
            try:
                root = require_public_run(session, run_id, team_id) if authorized and member else None
            except AgentRunNotFoundError:
                root = None
            if root is None:
                outgoing.append('event: terminal\ndata: {"status":"failed","error":"ACCESS_REVOKED"}\n\n')
                terminal = True
            else:
                job = find_job(session, run_id)
                if job is None:
                    outgoing.append('event: terminal\ndata: {"status":"failed","error":"JOB_NOT_FOUND"}\n\n')
                    terminal = True
                else:
                    sync_published_approval(session, root, job)
                    attempt = None
                    db_now = session.scalar(select(func.utc_timestamp(6)))
                    if job.status == "running" and job.lease_until and job.lease_until > db_now:
                        attempt = session.scalar(select(DurableJobAttempt).where(
                            DurableJobAttempt.job_id == job.id, DurableJobAttempt.number == job.attempts,
                            DurableJobAttempt.status == "running", DurableJobAttempt.claim_token == job.claim_token))
                    visible_run = attempt.execution_run_id if attempt else job.published_run_id
                    attempt_number = attempt.number if attempt else (job.attempts if job.published_run_id else None)
                    if attempt_number != previous_attempt:
                        previous_attempt = attempt_number
                        outgoing.append(f"event: attempt\ndata: {json.dumps({'attempt': attempt_number})}\n\n")
                    if visible_run:
                        events = session.scalars(select(AgentTraceEvent).where(
                            AgentTraceEvent.agent_run_id == visible_run, AgentTraceEvent.id > cursor,
                        ).order_by(AgentTraceEvent.id)).all()
                        for item in events:
                            cursor = item.id
                            data = {"step": item.step, "node": item.node, "event_type": item.event_type,
                                    "status": item.status.value, "tool_name": item.tool_name,
                                    "latency_ms": item.latency_ms, "error": item.error}
                            outgoing.append(f"id: {cursor}\nevent: progress\ndata: {json.dumps(data, ensure_ascii=False)}\n\n")
                    if job.status in {"completed", "failed", "waiting_approval"}:
                        data = {"run_id": run_id, "status": root.status.value, "error": None}
                        outgoing.append(f"event: terminal\ndata: {json.dumps(data)}\n\n")
                        terminal = True
                    elif not outgoing:
                        outgoing.append(": heartbeat\n\n")
        yield from outgoing
        if terminal:
            return
        sleep(2)
