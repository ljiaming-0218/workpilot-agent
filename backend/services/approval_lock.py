"""Cross-process exclusion between approval execution and checkpoint recovery."""

from contextlib import contextmanager
from hashlib import sha256

from sqlalchemy import text


@contextmanager
def approval_lock(engine, request_id: str):
    """Hold a database-session advisory lock until execution or recovery ends."""
    if engine.dialect.name != "mysql":
        # SQLite is used only by local isolated tests, not the supported deployment.
        yield True
        return
    name = "wp-approval-" + sha256(request_id.encode()).hexdigest()[:40]
    with engine.connect() as connection:
        acquired = connection.execute(text("SELECT GET_LOCK(:name, 0)"), {"name": name}).scalar() == 1
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": name})
                except Exception:
                    # A broken connection cannot safely be returned to the pool.
                    connection.invalidate()
                    raise
