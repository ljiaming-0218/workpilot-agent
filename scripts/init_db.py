from pydantic import ValidationError
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

import backend.models  # Register all models before inspecting Base.metadata.
from backend.config import Settings
from backend.database import Base, build_engine


def create_tables(engine: Engine) -> list[str]:
    """Create only missing model tables and return their names; never drop data."""
    existing = set(inspect(engine).get_table_names())
    missing = sorted(set(Base.metadata.tables) - existing)
    Base.metadata.create_all(engine, checkfirst=True)
    return missing


def format_database_error(exc: SQLAlchemyError, settings: Settings, stage: str) -> str:
    """Return useful driver diagnostics without exposing the database password."""
    original = getattr(exc, "orig", None)
    arguments = getattr(original, "args", ())
    error_code = arguments[0] if arguments and isinstance(arguments[0], int) else "unknown"
    if len(arguments) >= 2:
        message = str(arguments[1])
    elif arguments:
        message = str(arguments[0])
    else:
        message = type(original).__name__ if original is not None else type(exc).__name__

    password = settings.mysql_password.get_secret_value()
    if password:
        message = message.replace(password, "[REDACTED]")
    message = " ".join(message.split())[:500]
    return f"DATABASE_ERROR stage={stage} code={error_code}: {message}"


def main() -> int:
    engine = None
    settings = None
    stage = "configuration"
    try:
        settings = Settings()
        engine = build_engine(settings)
        stage = "connectivity"
        with engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()
        print("Database connectivity: OK")

        stage = "schema_initialization"
        created = create_tables(engine)
        print("Created tables: " + (", ".join(created) if created else "none (already present)"))
        return 0
    except ValidationError:
        print("CONFIG_ERROR: check the project .env configuration.")
        return 1
    except SQLAlchemyError as exc:
        if settings is None:
            print(f"DATABASE_ERROR stage={stage} code=unknown: {type(exc).__name__}")
        else:
            print(format_database_error(exc, settings, stage))
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
