"""Exercise queue primitives against isolated, randomly named database tables."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4
import re

from sqlalchemy import event, func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateTable

import backend.models  # noqa: F401
from backend.config import Settings
from backend.database import Base, build_engine
from backend.models.durable_job import DurableJob, DurableJobAttempt
from backend.services.durable_queue import (
    IdempotencyConflict, add_job, claim_next, digests, find_submission, finish_claim, renew,
)


def main() -> None:
    engine = build_engine(Settings())
    suffix = uuid4().hex
    names = {name: f"p3_probe_{short}_{suffix}" for name, short in (
        ("agent_runs", "run"), ("durable_jobs", "job"), ("durable_job_attempts", "attempt"))}
    created = []

    def rewrite(connection, cursor, statement, parameters, context, executemany):
        for original, replacement in names.items():
            statement = re.sub(r"\b" + original + r"\b", replacement, statement)
        return statement, parameters

    try:
        with engine.begin() as connection:
            connection.execute(text(f"CREATE TABLE `{names['agent_runs']}` (id INT PRIMARY KEY)"))
        created.append(names["agent_runs"])
        event.listen(engine, "before_cursor_execute", rewrite, retval=True)
        for table in (DurableJob.__table__, DurableJobAttempt.__table__):
            with engine.begin() as connection:
                connection.execute(CreateTable(table))
            created.append(names[table.name])
        factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        key, request_hash = digests("submission-1", "payment-service")
        with factory.begin() as session:
            session.execute(text("INSERT INTO agent_runs (id) VALUES (1), (2), (3)"))
            add_job(session, run_id=1, team_id=1, user_id=1, key_digest=key,
                    request_digest=request_hash, retry_safe=True)
        try:
            with factory.begin() as session:
                session.execute(text("INSERT INTO agent_runs (id) VALUES (4)"))
                rollback_key, rollback_body = digests("rollback-submission", "rollback-service")
                add_job(session, run_id=4, team_id=1, user_id=1, key_digest=rollback_key,
                        request_digest=rollback_body)
                raise RuntimeError("deliberate transaction rollback")
        except RuntimeError:
            pass
        with factory() as session:
            assert session.scalar(text("SELECT COUNT(*) FROM agent_runs WHERE id=4")) == 0
            assert session.scalar(select(DurableJob).where(DurableJob.agent_run_id == 4)) is None
        with factory() as session:
            job = find_submission(session, team_id=1, user_id=1,
                                  key_digest=key, request_digest=request_hash)
            assert job and job.agent_run_id == 1
            try:
                find_submission(session, team_id=1, user_id=1, key_digest=key,
                                request_digest=digests("submission-1", "other-service")[1])
            except IdempotencyConflict:
                pass
            else:
                raise AssertionError("different request accepted for same key")
        try:
            with factory.begin() as session:
                add_job(session, run_id=2, team_id=1, user_id=1, key_digest=key,
                        request_digest=request_hash)
        except IntegrityError:
            pass
        else:
            raise AssertionError("duplicate idempotency key accepted")

        barrier = Barrier(2)
        def competing_claim(worker):
            barrier.wait(timeout=15)
            try:
                return claim_next(factory, worker, lease_seconds=60)
            except DBAPIError as exc:
                # A transaction conflict also means this attempt did not win.
                print("Competing transaction failed:", type(exc).__name__)
                return None
        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = [c for c in executor.map(competing_claim, ("a", "b")) if c]
        assert len(claims) == 1, f"expected one winner, got {len(claims)}"
        old = claims[0]
        assert renew(factory, old)
        with factory.begin() as session:
            now = session.scalar(select(func.utc_timestamp(6)))
            session.execute(update(DurableJob).where(DurableJob.id == old.job_id)
                            .values(lease_until=now - timedelta(seconds=1)))
        assert not renew(factory, old)
        new = claim_next(factory, "recovery", lease_seconds=60)
        assert new and new.attempt == 2 and new.token != old.token
        assert not finish_claim(factory, old, succeeded=True)
        assert finish_claim(factory, new, succeeded=True)
        with factory() as session:
            attempts = session.scalars(select(DurableJobAttempt).order_by(DurableJobAttempt.number)).all()
            assert [a.status for a in attempts] == ["expired", "completed"]
        key2, body2 = digests("unsafe-submission", "other-service")
        with factory.begin() as session:
            add_job(session, run_id=2, team_id=1, user_id=1, key_digest=key2,
                    request_digest=body2, retry_safe=False)
        unsafe = claim_next(factory, "unsafe-worker")
        assert unsafe
        with factory.begin() as session:
            now = session.scalar(select(func.utc_timestamp(6)))
            session.execute(update(DurableJob).where(DurableJob.id == unsafe.job_id)
                            .values(lease_until=now - timedelta(seconds=1)))
        assert claim_next(factory, "recovery") is None
        with factory() as session:
            assert session.get(DurableJob, unsafe.job_id).status == "failed"
            assert session.scalar(select(DurableJobAttempt).where(
                DurableJobAttempt.job_id == unsafe.job_id)).status == "expired"
        key3, body3 = digests("limited-submission", "third-service")
        with factory.begin() as session:
            add_job(session, run_id=3, team_id=1, user_id=1, key_digest=key3,
                    request_digest=body3, retry_safe=True, max_attempts=1)
        limited = claim_next(factory, "limited-worker")
        assert limited
        with factory.begin() as session:
            now = session.scalar(select(func.utc_timestamp(6)))
            session.execute(update(DurableJob).where(DurableJob.id == limited.job_id)
                            .values(lease_until=now - timedelta(seconds=1)))
        assert claim_next(factory, "recovery") is None
        with factory() as session:
            assert session.get(DurableJob, limited.job_id).status == "failed"
        print("P3 queue smoke passed: atomic rollback, idempotency, concurrent claim, renewal, recovery, stale token, history, unsafe expiry, retry limit")
    finally:
        event.remove(engine, "before_cursor_execute", rewrite) if event.contains(engine, "before_cursor_execute", rewrite) else None
        for table in reversed(created):
            with engine.begin() as connection:
                connection.execute(text(f"DROP TABLE `{table}`"))
        engine.dispose()


if __name__ == "__main__":
    main()
