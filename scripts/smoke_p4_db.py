"""P4 transaction/concurrency checks on disposable random MySQL/TiDB tables.

Creates and drops only its own p4_* tables. No models or production operations.
"""

from concurrent.futures import ThreadPoolExecutor
import re
from threading import Barrier
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateTable

import backend.models  # noqa: F401
from backend.config import Settings
from backend.database import build_engine
from backend.models.agent_run import AgentRun, AgentRunService
from backend.models.durable_job import DurableJob, DurableJobAttempt
from backend.models.investigation import IncidentSubmission, InvestigationNote, InvestigationReview
from backend.models.team import ServiceGrant, Team, TeamMember
from backend.models.user import User
from backend.routers.investigation_router import investigation_summary
from backend.schemas.alert import IncidentAlertRequest
from backend.schemas.investigation import ReviewUpdate
from backend.services.agent_service import AgentRunNotFoundError, prepare_agent_run
from backend.services.alert_service import enqueue_incident_alert
from backend.services.durable_access import require_public_run
from backend.services.investigation_service import collaboration_view, update_review


def main():
    engine = build_engine(Settings())
    suffix = uuid4().hex
    tables = [User.__table__, Team.__table__, TeamMember.__table__, ServiceGrant.__table__,
              AgentRun.__table__, AgentRunService.__table__, DurableJob.__table__,
              DurableJobAttempt.__table__, IncidentSubmission.__table__,
              InvestigationReview.__table__, InvestigationNote.__table__]
    names = {table.name: f"p4_{index}_{suffix}" for index, table in enumerate(tables)}
    created = []
    print("Probe table suffix:", suffix, flush=True)

    def rewrite(connection, cursor, statement, parameters, context, executemany):
        for original, replacement in names.items():
            statement = re.sub(r"\b" + original + r"\b", replacement, statement)
        return statement, parameters

    event.listen(engine, "before_cursor_execute", rewrite, retval=True)
    try:
        for table in tables:
            with engine.begin() as connection:
                connection.execute(CreateTable(table))
            created.append(names[table.name])
        factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        with factory.begin() as db:
            db.add_all([User(id=1, username="owner"), User(id=2, username="peer"),
                        User(id=3, username="other"), Team(id=1, name="test"), Team(id=2, name="other")])
            db.flush()
            db.add_all([TeamMember(id=1, team_id=1, user_id=1, role="admin"),
                        TeamMember(id=2, team_id=1, user_id=2, role="member"),
                        TeamMember(id=3, team_id=2, user_id=3, role="member"),
                        ServiceGrant(team_id=1, service_name="p4-http-service")])
        payload = IncidentAlertRequest(source="db-smoke", event_id="concurrent", source_kind="local_test",
                                       service_name="p4-http-service", alert_name="smoke", severity="warning")
        barrier = Barrier(2)

        def accept(user):
            with factory() as db:
                member = db.get(TeamMember, user)
                barrier.wait(timeout=15)
                return enqueue_incident_alert(db, member, payload).run_id

        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(accept, [1, 2]))
        assert ids[0] == ids[1]
        run_id = ids[0]
        with factory() as db:
            for model in (AgentRun, DurableJob, IncidentSubmission):
                assert db.scalar(select(func.count()).select_from(model)) == 1
            member = db.get(TeamMember, 1)
            root = require_public_run(db, run_id, member.team_id)
            assert db.scalars(select(AgentRunService.service_name)).all() == ["p4-http-service"]
            try:
                enqueue_incident_alert(db, member, payload.model_copy(update={"window_minutes": 30}))
            except HTTPException as exc:
                assert exc.status_code == 409
            else:
                raise AssertionError("event content conflict was accepted")
            assert collaboration_view(db, root)["version"] == 0
        barrier = Barrier(2)

        def review(user):
            with factory() as db:
                member = db.get(TeamMember, user)
                root = require_public_run(db, run_id, 1)
                barrier.wait(timeout=15)
                try:
                    update_review(db, root, member, ReviewUpdate(expected_version=0,
                        owner_user_id=user, handling_status="in_progress", note="Concurrent smoke"))
                    return 200
                except HTTPException as exc:
                    return exc.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(review, [1, 2]))
        assert sorted(results) == [200, 409]
        with factory() as db:
            assert db.scalar(select(func.count()).select_from(InvestigationNote)) == 1
            assert db.get(InvestigationReview, run_id).version == 1
            member = db.get(TeamMember, 1)
            enqueue_incident_alert(db, member, payload.model_copy(update={"event_id": "synthetic", "source_kind": "synthetic"}))
            prepare_agent_run(db, "service: p4-http-service 故障", creator_user_id=1, team_id=1)
            summary = investigation_summary(db, member).data
            assert {item["source_kind"] for item in summary["groups"]} == {"local_test", "synthetic", "unspecified"}
            assert summary["root_cause_accuracy"] is None
            grant = db.scalar(select(ServiceGrant).where(ServiceGrant.team_id == 1))
            db.delete(grant)
            db.commit()
            try:
                require_public_run(db, run_id, 1)
            except AgentRunNotFoundError:
                pass
            else:
                raise AssertionError("revoked service remained accessible")
        print("P4 DB smoke passed: concurrent alert deduplication, atomic review history, conflict, provenance groups, revoked access", flush=True)
    finally:
        event.remove(engine, "before_cursor_execute", rewrite)
        try:
            for name in reversed(created):
                with engine.begin() as connection:
                    connection.execute(text(f"DROP TABLE `{name}`"))
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
