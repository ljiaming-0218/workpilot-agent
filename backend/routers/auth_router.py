"""Login and session APIs for the console."""
from datetime import timedelta
import secrets
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from backend.dependencies import get_db
from backend.models.auth_session import AuthSession
from backend.models.common import utc_now
from backend.models.user import User
from backend.services.auth_service import COOKIE_NAME, get_current_user, token_digest, verify_password

router = APIRouter(prefix='/auth', tags=['auth'])
Db = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


def _profile(db: Session, user: User) -> dict:
    from backend.models.team import Team, TeamMember
    memberships = db.execute(select(Team, TeamMember).join(TeamMember, Team.id == TeamMember.team_id)
                             .where(TeamMember.user_id == user.id).order_by(Team.id)).all()
    return {'id': user.id, 'username': user.username, 'role': user.role.value,
            'teams': [{'id': team.id, 'name': team.name, 'role': member.role.value if hasattr(member.role, 'value') else member.role}
                      for team, member in memberships]}


@router.post('/login')
def login(payload: LoginInput, request: Request, response: Response, db: Db):
    user = db.scalar(select(User).where(User.username == payload.username))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail='用户名或密码错误')
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(user_id=user.id, token_hash=token_digest(token), expires_at=utc_now() + timedelta(hours=12)))
    db.commit()
    response.set_cookie(COOKIE_NAME, token, max_age=12 * 3600, httponly=True, samesite='strict',
                        secure=request.url.scheme == 'https', path='/')
    return {'success': True, 'data': _profile(db, user), 'error': None}


@router.get('/me')
def me(db: Db, user: CurrentUser):
    return {'success': True, 'data': _profile(db, user), 'error': None}


@router.post('/logout')
def logout(request: Request, response: Response, db: Db):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == token_digest(token)))
        db.commit()
    response.delete_cookie(COOKIE_NAME, path='/')
    return {'success': True, 'data': {'ok': True}, 'error': None}
