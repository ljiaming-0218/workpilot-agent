"""Apply P1 additive schema changes without assigning old data to a team."""

from sqlalchemy import inspect, text

import backend.models  # noqa: F401
from backend.config import Settings
from backend.database import Base, build_engine


def migrate(engine):
    Base.metadata.create_all(engine, checkfirst=True)
    changes = {
        "users": {"password_hash": "VARCHAR(255) NULL"},
        "agent_runs": {"creator_user_id": "INTEGER NULL", "team_id": "INTEGER NULL",
                       "service_scope_recorded": "BOOLEAN NULL"},
    }
    added = []
    for table, columns in changes.items():
        existing = {column["name"] for column in inspect(engine).get_columns(table)}
        for name, definition in columns.items():
            if name not in existing:
                with engine.begin() as connection:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
                added.append(f"{table}.{name}")
    return added


def main():
    engine = build_engine(Settings())
    try:
        print("P1 columns added:", ", ".join(migrate(engine)) or "none")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
