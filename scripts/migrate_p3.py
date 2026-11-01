"""Create only the additive P3 queue tables; existing data is not modified."""

import backend.models  # noqa: F401 -- register foreign-key targets
from backend.config import Settings
from backend.database import Base, build_engine
from backend.models.durable_job import DurableJob, DurableJobAttempt
from sqlalchemy import inspect, text


def migrate(engine) -> None:
    Base.metadata.create_all(
        engine, tables=[DurableJob.__table__, DurableJobAttempt.__table__], checkfirst=True
    )
    # Support databases that applied the earlier queue-only version of P3.
    for table, column in (("durable_jobs", "published_run_id"),
                          ("durable_job_attempts", "execution_run_id")):
        columns = {item["name"] for item in inspect(engine).get_columns(table)}
        if column not in columns:
            with engine.begin() as connection:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} INTEGER NULL"))


if __name__ == "__main__":
    engine = build_engine(Settings())
    try:
        migrate(engine)
        print("P3 queue tables checked.")
    finally:
        engine.dispose()
