"""Grant or revoke one service for one team using the configured database."""

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database import build_engine
from backend.models.team import ServiceGrant, Team


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team", required=True, help="Existing team name")
    parser.add_argument("--service", required=True, help="Service name, e.g. order-service")
    parser.add_argument("--revoke", action="store_true", help="Revoke this team's access to the service")
    args = parser.parse_args()
    if not args.service.strip() or len(args.service) > 128:
        parser.error("--service must be 1 to 128 characters")
    engine = build_engine(Settings())
    try:
        with Session(engine) as session:
            team = session.scalar(select(Team).where(Team.name == args.team))
            if team is None:
                parser.error("team does not exist")
            grant = session.scalar(select(ServiceGrant).where(
                ServiceGrant.team_id == team.id, ServiceGrant.service_name == args.service))
            if args.revoke and grant is not None:
                session.delete(grant)
            elif not args.revoke and grant is None:
                session.add(ServiceGrant(team_id=team.id, service_name=args.service))
            session.commit()
            print(f"Service grant {'revoked' if args.revoke else 'active'}: {args.team} / {args.service}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
