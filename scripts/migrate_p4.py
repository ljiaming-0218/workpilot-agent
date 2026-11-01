"""Add P4 provenance and collaboration tables without rewriting existing rows."""

from sqlalchemy import inspect

import backend.models  # noqa: F401
from backend.config import Settings
from backend.database import Base, build_engine
from backend.models.investigation import IncidentSubmission, InvestigationNote, InvestigationReview


def migrate(engine) -> None:
    required = {"users", "teams", "team_members", "service_grants", "agent_run_services",
                "durable_jobs", "durable_job_attempts"}
    if required - set(inspect(engine).get_table_names()):
        raise RuntimeError("Apply P1, P2 and P3 migrations before P4")
    Base.metadata.create_all(engine, tables=[IncidentSubmission.__table__,
        InvestigationReview.__table__, InvestigationNote.__table__], checkfirst=True)


if __name__ == "__main__":
    engine = build_engine(Settings())
    try:
        migrate(engine)
        print("P4 provenance and collaboration tables checked; historical rows unchanged.")
    finally:
        engine.dispose()
