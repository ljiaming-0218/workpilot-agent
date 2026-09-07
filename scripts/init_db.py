from pydantic import ValidationError
from sqlalchemy import Engine, inspect
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


def main() -> int:
    engine = None
    try:
        engine = build_engine(Settings())
        created = create_tables(engine)
        print("Created tables: " + (", ".join(created) if created else "none (already present)"))
        return 0
    except ValidationError:
        print("CONFIG_ERROR: check the project .env configuration.")
        return 1
    except SQLAlchemyError as exc:
        # Do not print driver messages or URLs containing connection details.
        print(f"DATABASE_ERROR ({type(exc).__name__}): check connectivity and CREATE permission.")
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
