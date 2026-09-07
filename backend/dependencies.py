"""Request-scoped database session dependency."""

from collections.abc import Generator

from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session


def get_db(request: Request) -> Generator[Session, None, None]:
    """Yield an independent session and release it on success or failure."""
    session = request.app.state.session_factory()
    try:
        yield session
    except SQLAlchemyError:
        session.rollback()
        raise
    finally:
        # Closing also ends any uncommitted read transaction and returns its connection.
        session.close()
