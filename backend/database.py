"""MySQL engine construction and a read-only connectivity probe."""

import logging

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session

from backend.config import Settings


logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Shared ORM metadata populated when backend.models is imported."""


def build_engine(settings: Settings) -> Engine:
    """Create a connection pool; the first query opens the actual connection."""
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=5,
        connect_args={
            "connect_timeout": settings.mysql_connect_timeout,
            "read_timeout": 5,
            "write_timeout": 5,
        },
        echo=False,
        hide_parameters=True,
    )


def is_database_connected(session: Session) -> bool:
    """Probe the configured database without reading or changing business data."""
    try:
        return session.execute(text("SELECT 1")).scalar_one() == 1
    except SQLAlchemyError as exc:
        session.rollback()
        
        logger.warning("DATABASE_ERROR: health probe failed (%s)", type(exc).__name__)
        return False
