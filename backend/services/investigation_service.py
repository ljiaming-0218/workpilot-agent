"""Team-shared review with optimistic concurrency and immutable change history."""

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from backend.models.agent_run import AgentRun
from backend.models.common import utc_now
from backend.models.enums import AgentRunStatus
from backend.models.investigation import IncidentSubmission, InvestigationNote, InvestigationReview
from backend.models.team import TeamMember
from backend.models.user import User
from backend.schemas.common import as_utc
from backend.schemas.investigation import ReviewUpdate


def collaboration_view(session: Session, run: AgentRun) -> dict:
    """Caller must first authorize this public run and its full service scope."""
    review = session.get(InvestigationReview, run.id)
    incident = session.get(IncidentSubmission, run.id)
    notes = session.scalars(select(InvestigationNote).where(
        InvestigationNote.agent_run_id == run.id,
    ).order_by(InvestigationNote.version.desc()).limit(50)).all()
    members = session.execute(select(User.id, User.username).join(
        TeamMember, TeamMember.user_id == User.id,
    ).where(TeamMember.team_id == run.team_id).order_by(User.username)).all()
    return {
        "run_id": run.id,
        "execution_status": run.status.value,
        "version": review.version if review else 0,
        "owner_user_id": review.owner_user_id if review else None,
        "handling_status": review.handling_status if review else "open",
        "feedback": review.feedback if review else "unreviewed",
        "members": [{"id": uid, "username": name} for uid, name in members],
        "incident": {"source": incident.source, "event_id": incident.event_id,
                     "source_kind": incident.source_kind, "payload": incident.payload,
                     "created_at": as_utc(incident.created_at)} if incident else None,
        "notes": [{"version": item.version, "actor_user_id": item.actor_user_id,
                   "owner_user_id": item.owner_user_id, "handling_status": item.handling_status,
                   "feedback": item.feedback, "note": item.note,
                   "created_at": as_utc(item.created_at)} for item in notes],
        "notes_total": session.scalar(select(func.count()).select_from(InvestigationNote).where(
            InvestigationNote.agent_run_id == run.id)) or 0,
    }


def update_review(session: Session, run: AgentRun, member: TeamMember, payload: ReviewUpdate) -> dict:
    """Compare-and-swap the review and insert its audit event in one transaction."""
    if payload.owner_user_id is not None and not session.scalar(select(TeamMember.id).where(
        TeamMember.team_id == run.team_id, TeamMember.user_id == payload.owner_user_id,
    )):
        raise HTTPException(422, "负责人必须是当前团队成员")
    if payload.handling_status != "open" and payload.owner_user_id is None:
        raise HTTPException(422, "处理中或已处理的调查需要负责人")
    if payload.feedback != "unreviewed" and run.status not in {AgentRunStatus.COMPLETED, AgentRunStatus.FAILED}:
        raise HTTPException(409, "执行结束后才能提交结果反馈")
    if payload.handling_status == "resolved" and (payload.feedback == "unreviewed" or not payload.note):
        raise HTTPException(422, "标记已处理时需要反馈和处理说明")
    version = payload.expected_version + 1
    values = {"version": version, "owner_user_id": payload.owner_user_id,
              "handling_status": payload.handling_status, "feedback": payload.feedback,
              "updated_by": member.user_id, "updated_at": utc_now()}
    try:
        if payload.expected_version == 0:
            session.add(InvestigationReview(agent_run_id=run.id, **values))
            session.flush()
        else:
            changed = session.execute(update(InvestigationReview).where(
                InvestigationReview.agent_run_id == run.id,
                InvestigationReview.version == payload.expected_version,
            ).values(**values).execution_options(synchronize_session=False))
            if changed.rowcount != 1:
                session.rollback()
                raise HTTPException(409, "其他成员已更新，请刷新协作记录后重试")
        session.add(InvestigationNote(
            agent_run_id=run.id, version=version, actor_user_id=member.user_id,
            owner_user_id=payload.owner_user_id, handling_status=payload.handling_status,
            feedback=payload.feedback, note=payload.note,
        ))
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "其他成员已更新，请刷新协作记录后重试") from exc
    except SQLAlchemyError:
        session.rollback()
        raise
    session.expire_all()
    return collaboration_view(session, run)
