"""Probe conditional claims using one disposable table in the configured database.

Run with the workpilot environment: python -m scripts.smoke_p3_claim_db
The randomly named table contains no application data and is dropped on exit.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.config import Settings
from backend.database import build_engine


def main() -> None:
    engine = build_engine(Settings())
    table = "p3_claim_probe_" + uuid4().hex
    created = False
    try:
        with engine.begin() as connection:
            connection.execute(text(f"CREATE TABLE `{table}` (id INT PRIMARY KEY, state VARCHAR(16) NOT NULL, token VARCHAR(32) NULL)"))
        created = True
        with engine.begin() as connection:
            connection.execute(text(f"INSERT INTO `{table}` (id, state) VALUES (1, 'queued')"))

        barrier = Barrier(2)

        def claim(token: str) -> tuple[str, int | str]:
            try:
                with engine.begin() as connection:
                    barrier.wait(timeout=15)
                    result = connection.execute(
                        text(f"UPDATE `{table}` SET state='running', token=:token WHERE id=1 AND state='queued'"),
                        {"token": token},
                    )
                    return token, result.rowcount
            except DBAPIError as exc:
                return token, type(exc).__name__

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(claim, ("worker-a", "worker-b")))
        with engine.connect() as connection:
            row = connection.execute(text(f"SELECT state, token FROM `{table}` WHERE id=1")).one()
        winners = [token for token, result in outcomes if result == 1]
        if len(winners) != 1 or row.state != "running" or row.token != winners[0]:
            raise RuntimeError(f"Unexpected claim outcomes: {outcomes}; persisted state: {row}")
        if any(result not in (0, 1) for _, result in outcomes):
            print("One competing transaction conflicted and requires bounded retry:", outcomes)
        else:
            print("Conditional claim: exactly one winner; outcomes:", outcomes)
    finally:
        if created:
            with engine.begin() as connection:
                connection.execute(text(f"DROP TABLE `{table}`"))
        engine.dispose()


if __name__ == "__main__":
    main()
