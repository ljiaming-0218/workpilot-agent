"""Atomically accept an alert into the P3 queue with immutable provenance."""

from hashlib import sha256
import json

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from backend.models.investigation import IncidentSubmission
from backend.models.team import TeamMember
from backend.schemas.alert import IncidentAlertAccepted, IncidentAlertRequest
from backend.services.access_service import granted_services
from backend.services.agent_service import prepare_agent_run
from backend.services.durable_access import find_job, require_public_run
from backend.services.durable_queue import add_job, digests


def enqueue_incident_alert(session: Session, member: TeamMember,
                           payload: IncidentAlertRequest) -> IncidentAlertAccepted:
    """Deduplicate events across team members; caller-supplied origin is a declaration.

    Metadata is retained for display, never interpolated as Agent instructions.
    Only the validated service and bounded time window form the read-only task.
    """
    if payload.service_name not in granted_services(session, member.team_id):
        raise HTTPException(403, "团队没有告警服务的调查权限")
    body = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"))
    payload_hash = sha256(body.encode()).hexdigest()

    def existing():
        incident = session.scalar(select(IncidentSubmission).where(
            IncidentSubmission.team_id == member.team_id,
            IncidentSubmission.source == payload.source,
            IncidentSubmission.event_id == payload.event_id,
        ))
        if incident is not None and incident.payload_digest != payload_hash:
            raise HTTPException(409, "同一告警事件已用于不同内容；新事件请使用新的 event_id")
        return incident

    incident = existing()
    if incident is None:
        query = _build_incident_query(payload)
        key, _ = digests("alert:" + sha256(json.dumps(
            [member.team_id, payload.source, payload.event_id], separators=(",", ":"),
        ).encode()).hexdigest(), query)
        try:
            run_id, _ = prepare_agent_run(session, query, creator_user_id=member.user_id,
                                          team_id=member.team_id, commit=False,
                                          target_services=[payload.service_name])
            add_job(session, run_id=run_id, team_id=member.team_id, user_id=member.user_id,
                    key_digest=key, request_digest=payload_hash, retry_safe=True)
            incident = IncidentSubmission(
                agent_run_id=run_id, team_id=member.team_id, source=payload.source,
                event_id=payload.event_id, source_kind=payload.source_kind,
                payload_digest=payload_hash, payload=payload.model_dump(mode="json"),
            )
            session.add(incident)
            session.commit()
        except IntegrityError:
            session.rollback()
            incident = existing()
            if incident is None:
                raise
        except SQLAlchemyError:
            session.rollback()
            raise
    root = require_public_run(session, incident.agent_run_id, member.team_id)
    job = find_job(session, root.id)
    if job is None:
        raise HTTPException(409, "告警缺少持久任务，请联系管理员核查")
    return IncidentAlertAccepted(
        run_id=root.id, request_id=root.request_id, status=root.status, queue_status=job.status,
        events_url=f"/agent/runs/{root.id}/events", source=incident.source,
        event_id=incident.event_id, source_kind=incident.source_kind,
    )


def _build_incident_query(payload: IncidentAlertRequest) -> str:
    return (
        f"分析 service: {payload.service_name} 最近 {payload.window_minutes} 分钟的故障，"
        "结合日志、工单和知识手册给出证据与只读排查方案。"
    )
