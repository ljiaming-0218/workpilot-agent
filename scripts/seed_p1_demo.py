"""Create two explicitly named demonstration users and team service grants."""

from getpass import getpass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database import build_engine
from backend.models.team import ServiceGrant, Team, TeamMember
from backend.models.user import User
from backend.services.auth_service import hash_password


def seed(session, passwords):
    specifications = (
        ("wp-demo-order", "wp-demo-order-team", "order-service", "admin"),
        ("wp-demo-inventory", "wp-demo-inventory-team", "inventory-service", "member"),
    )
    for username, team_name, service, role in specifications:
        user = session.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(username=username, password_hash=hash_password(passwords[username]))
            session.add(user)
            session.flush()
        team = session.scalar(select(Team).where(Team.name == team_name))
        if team is None:
            team = Team(name=team_name)
            session.add(team)
            session.flush()
        membership = session.scalar(select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == user.id))
        if membership is None:
            session.add(TeamMember(team_id=team.id, user_id=user.id, role=role))
        elif username == "wp-demo-order":
            membership.role = "admin"
        if not session.scalar(select(ServiceGrant).where(ServiceGrant.team_id == team.id, ServiceGrant.service_name == service)):
            session.add(ServiceGrant(team_id=team.id, service_name=service))
    session.commit()


def main():
    names = ("wp-demo-order", "wp-demo-inventory")
    passwords = {name: getpass(f"Password for new {name} (existing user is unchanged): ") for name in names}
    if any(len(value) < 8 for value in passwords.values()):
        raise ValueError("Each demo password must be at least 8 characters")
    engine = build_engine(Settings())
    try:
        with Session(engine) as session:
            seed(session, passwords)
        print("P1 demo accounts and grants ready; existing account passwords were not changed.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
