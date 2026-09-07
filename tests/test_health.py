"""HTTP contract, session cleanup, and opt-in real MySQL acceptance tests."""

import os
from unittest.mock import MagicMock, Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError, TimeoutError
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.main import create_app


@pytest.fixture
def client():
    settings = Settings(
        _env_file=None,
        mysql_user="unit_test",
        mysql_password="unused",
        mysql_database="unit_test",
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_health_success_uses_independent_sessions_and_closes_them(client):
    sessions = [MagicMock(spec=Session), MagicMock(spec=Session)]
    for session in sessions:
        session.execute.return_value.scalar_one.return_value = 1
    factory = Mock(side_effect=sessions)
    client.app.state.session_factory = factory

    for session in sessions:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "database": "connected"}
        assert str(session.execute.call_args.args[0]) == "SELECT 1"
        session.close.assert_called_once()
        session.commit.assert_not_called()
    assert factory.call_count == 2


@pytest.mark.parametrize("error", [
    OperationalError("SELECT 1", {}, Exception("sensitive-connection-detail")),
    TimeoutError("sensitive-connection-detail"),
])
def test_database_failure_returns_503_and_rolls_back(client, caplog, error):
    session = MagicMock(spec=Session)
    session.execute.side_effect = error
    client.app.state.session_factory = Mock(return_value=session)

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "disconnected"}
    session.rollback.assert_called_once()
    session.close.assert_called_once()
    assert "DATABASE_ERROR" in caplog.text
    assert "sensitive-connection-detail" not in caplog.text + response.text


def test_unexpected_bug_is_not_reported_as_database_outage(client):
    session = MagicMock(spec=Session)
    session.execute.side_effect = RuntimeError("unexpected bug")
    client.app.state.session_factory = Mock(return_value=session)

    with pytest.raises(RuntimeError, match="unexpected bug"):
        client.get("/health")
    session.close.assert_called_once()


def test_password_special_characters_are_preserved_and_repr_is_redacted():
    password = "local@pass:/?#%"
    settings = Settings(_env_file=None, mysql_user="unit_test", mysql_password=password)
    assert settings.database_url.password == password
    assert password not in repr(settings)
    assert password not in str(settings.database_url)


@pytest.mark.mysql
@pytest.mark.skipif(os.getenv("RUN_MYSQL_TESTS") != "1", reason="Set RUN_MYSQL_TESTS=1 for real MySQL")
def test_health_real_mysql():
    # No dependency override: validates HTTP -> Session -> configured MySQL.
    with TestClient(create_app()) as client:
        response = client.get("/health")
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok", "database": "connected"}
