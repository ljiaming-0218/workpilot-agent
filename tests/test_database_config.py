"""Regression coverage for the Phase 0 timeout configuration exercise."""

from unittest.mock import patch

import pytest
from pydantic import ValidationError
from sqlalchemy import event

from backend.config import Settings
from backend.database import build_engine


def test_timeout_default_and_environment_override(monkeypatch):
    with patch.dict("os.environ", {}, clear=True):
        args = {"_env_file": None, "mysql_user": "test", "mysql_password": "unused"}
        assert Settings(**args).mysql_connect_timeout == 5
        monkeypatch.setenv("MYSQL_CONNECT_TIMEOUT", "10")
        assert Settings(**args).mysql_connect_timeout == 10


@pytest.mark.parametrize("value", [0, -1, 31, "abc"])
def test_invalid_timeout_is_rejected(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, mysql_user="test", mysql_password="unused", mysql_connect_timeout=value)


def test_driver_receives_configured_timeout_and_keeps_read_write_limits():
    settings = Settings(
        _env_file=None, mysql_user="test", mysql_password="unused", mysql_connect_timeout=10
    )
    engine = build_engine(settings)

    class ProbeComplete(Exception):
        pass

    @event.listens_for(engine, "do_connect")
    def inspect_parameters(dialect, record, args, kwargs):
        assert {name: kwargs[name] for name in ("connect_timeout", "read_timeout", "write_timeout")} == {
            "connect_timeout": 10, "read_timeout": 5, "write_timeout": 5,
        }
        raise ProbeComplete()  # Stop before connecting; this test needs no database.

    try:
        with pytest.raises(ProbeComplete):
            engine.connect()
    finally:
        engine.dispose()
