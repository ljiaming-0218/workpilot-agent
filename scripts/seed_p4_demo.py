"""Add dedicated P4 test accounts and grants, preserving existing passwords/data."""

from getpass import getpass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database import build_engine
from backend.models.team import ServiceGrant, Team, TeamMember
from backend.models.user import User
from backend.services.auth_service import hash_password


SPECIFICATIONS = (
    ("wp-p4-owner", "wp-p4-http-team", "p4-http-service", "admin"),
    ("wp-p4-peer", "wp-p4-http-team", "p4-http-service", "member"),
    ("wp-p4-other", "wp-p4-other-team", "p4-other-service", "member"),
)


def seed(session: Session, passwords: dict[str, str]) -> dict:
    profiles = {}
    for username, team_name, service, role in SPECIFICATIONS:
        team = session.scalar(select(Team).where(Team.name == team_name))
        if team is None:
            team = Team(name=team_name)
            session.add(team)
            session.flush()
        user = session.scalar(select(User).where(User.username == username))
        if user is None:
            if len(passwords.get(username, "")) < 12:
                raise ValueError("New demo passwords must contain at least 12 characters")
            user = User(username=username, password_hash=hash_password(passwords[username]))
            session.add(user)
            session.flush()
        if not session.scalar(select(TeamMember.id).where(
            TeamMember.user_id == user.id, TeamMember.team_id == team.id,
        )):
            session.add(TeamMember(user_id=user.id, team_id=team.id, role=role))
        if not session.scalar(select(ServiceGrant.id).where(
            ServiceGrant.team_id == team.id, ServiceGrant.service_name == service,
        )):
            session.add(ServiceGrant(team_id=team.id, service_name=service))
        profiles[username] = {"user_id": user.id, "team_id": team.id, "service": service}
    session.commit()
    return profiles


if __name__ == "__main__":
    passwords = {name: getpass(f"Password for new {name} (existing password unchanged): ")
                 for name, *_ in SPECIFICATIONS}
    engine = build_engine(Settings())
    try:
        with Session(engine) as session:
            for name, profile in seed(session, passwords).items():
                print(name, profile)
    finally:
        engine.dispose()
