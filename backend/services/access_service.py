"""Resolve the authenticated member and selected team for task access."""
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session

from backend.dependencies import get_db
from backend.models.team import TeamMember, ServiceGrant
from backend.models.agent_run import AgentRun, AgentRunService
from backend.models.user import User
from backend.services.auth_service import get_current_user


def granted_services(session: Session, team_id: int) -> list[str]:
    return list(session.scalars(select(ServiceGrant.service_name).where(ServiceGrant.team_id == team_id)))


def resolve_run_scope(session: Session, run_id: int) -> tuple[int, set[str]]:
    run = session.get(AgentRun, run_id)
    if run is None or run.team_id is None or run.creator_user_id is None or not run.service_scope_recorded:
        raise ValueError('任务缺少有效的身份和服务范围')
    member = session.scalar(select(TeamMember).where(TeamMember.team_id == run.team_id,
                                                     TeamMember.user_id == run.creator_user_id))
    if member is None:
        raise ValueError('任务创建人已失去团队权限')
    granted = set(granted_services(session, run.team_id))
    requested = set(session.scalars(select(AgentRunService.service_name).where(AgentRunService.agent_run_id == run_id)))
    if not requested.issubset(granted):
        raise ValueError('任务服务权限已变更')
    return run.team_id, requested


def run_access_filter(team_id: int):
    granted = select(ServiceGrant.service_name).where(ServiceGrant.team_id == team_id)
    missing = exists(select(AgentRunService.id).where(
        AgentRunService.agent_run_id == AgentRun.id,
        AgentRunService.service_name.not_in(granted),
    ))
    return and_(AgentRun.team_id == team_id, AgentRun.service_scope_recorded.is_(True), ~missing)


def current_team(request: Request, user: Annotated[User, Depends(get_current_user)],
                 session: Annotated[Session, Depends(get_db)]) -> TeamMember:
    raw = request.headers.get('X-WorkPilot-Team-Id') or request.query_params.get('team_id')
    if not raw or not raw.isdecimal() or int(raw) < 1:
        raise HTTPException(status_code=400, detail='请选择团队')
    member = session.scalar(select(TeamMember).where(TeamMember.team_id == int(raw), TeamMember.user_id == user.id))
    if member is None:
        raise HTTPException(status_code=403, detail='无权访问该团队')
    return member
