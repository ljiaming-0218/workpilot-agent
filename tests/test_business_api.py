"""Validation/error contracts without MySQL, plus opt-in real database API tests."""

import os
from datetime import datetime
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, inspect, select
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database import Base, build_engine
from backend.main import create_app
from backend.models import ErrorLog, Ticket
from backend.models.enums import LogLevel, TicketCategory, TicketPriority, TicketStatus


@pytest.fixture
def mock_api():
    settings = Settings(_env_file=None, mysql_user="unit_test", mysql_password="unused")
    with TestClient(create_app(settings)) as client:
        session = MagicMock(spec=Session)
        client.app.state.session_factory = Mock(return_value=session)
        yield client, session


@pytest.mark.parametrize("payload", [
    {}, {"title": "  ", "content": "test"}, {"title": "test", "content": "\n"},
    {"title": "x" * 256, "content": "test"},
    {"title": "test", "content": "x" * 100_001},
    {"title": "test", "content": "test", "status": "closed"},
    {"title": "test", "content": "test", "priority": "urgent"},
    {"title": "test", "content": "test", "service_name": " "},
])
def test_invalid_create_is_rejected_before_sql(mock_api, payload):
    client, session = mock_api
    response = client.post("/tickets", json=payload)
    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False and body["data"] is None
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["details"]
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_not_called()
    session.close.assert_called_once()


@pytest.mark.parametrize("url", [
    "/tickets?page=0", "/tickets?page_size=101", "/tickets?page=abc",
    "/tickets?status=unknown", "/tickets?category=unknown", "/tickets?typo=1",
    "/tickets/0", "/tickets/not-an-id", "/logs?level=error", "/logs?page_size=0",
    "/logs?created_from=2026-09-01T00:00:00",
    "/logs?created_from=2026-09-02T00:00:00Z&created_to=2026-09-01T00:00:00Z",
])
def test_invalid_query_is_rejected_before_sql(mock_api, url):
    client, session = mock_api
    response = client.get(url)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    session.scalar.assert_not_called()
    session.scalars.assert_not_called()
    session.get.assert_not_called()
    session.close.assert_called_once()


def test_validation_response_does_not_echo_body_values(mock_api):
    client, _ = mock_api
    response = client.post("/tickets", json={"title": "test", "content": "test", "secret": "private-value"})
    assert response.status_code == 422
    assert "private-value" not in response.text


@pytest.mark.parametrize("url,method", [("/tickets", "scalar"), ("/tickets/1", "get"), ("/logs", "scalar")])
def test_read_failure_rolls_back_closes_and_redacts(mock_api, caplog, url, method):
    client, session = mock_api
    getattr(session, method).side_effect = OperationalError("private SQL", {}, Exception("private-password"))
    response = client.get(url)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_ERROR"
    session.rollback.assert_called_once()
    session.close.assert_called_once()
    session.commit.assert_not_called()
    assert "private" not in response.text + caplog.text


@pytest.mark.parametrize("method", ["flush", "commit"])
def test_failed_create_does_not_return_success(mock_api, caplog, method):
    client, session = mock_api
    if method == "commit":
        # Supply SQLAlchemy-generated defaults so serialization can reach commit().
        def assign_defaults():
            ticket = session.add.call_args.args[0]
            ticket.id = 1
            ticket.status = TicketStatus.OPEN
            ticket.created_at = ticket.updated_at = datetime(2026, 9, 1)
        session.flush.side_effect = assign_defaults
    getattr(session, method).side_effect = OperationalError("private SQL", {}, Exception("private-password"))
    response = client.post("/tickets", json={"title": "test", "content": "test"})
    assert response.status_code == 503, response.text
    assert response.json()["success"] is False
    session.rollback.assert_called_once()
    session.close.assert_called_once()
    if method == "flush":
        session.commit.assert_not_called()
    assert "private" not in response.text + caplog.text


@pytest.mark.parametrize("error_type,status,code", [
    (IntegrityError, 409, "DATABASE_CONFLICT"),
    (ProgrammingError, 500, "DATABASE_ERROR"),
])
def test_database_error_categories(mock_api, error_type, status, code):
    client, session = mock_api
    session.flush.side_effect = error_type("private SQL", {}, Exception("private-password"))
    response = client.post("/tickets", json={"title": "test", "content": "test"})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    session.rollback.assert_called_once()
    session.close.assert_called_once()


def test_missing_ticket_is_404_and_closes_session(mock_api):
    client, session = mock_api
    session.get.return_value = None
    response = client.get("/tickets/123")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    session.commit.assert_not_called()
    session.close.assert_called_once()


@pytest.fixture
def mysql_api():
    """Real request Sessions use savepoints; the outer transaction removes test rows."""
    engine = build_engine(Settings())
    try:
        assert set(Base.metadata.tables) <= set(inspect(engine).get_table_names()), "Run python -m scripts.init_db"
        with engine.connect() as connection:
            outer = connection.begin()
            try:
                with TestClient(create_app()) as client:
                    factory = Mock(side_effect=lambda: Session(bind=connection, join_transaction_mode="create_savepoint"))
                    client.app.state.session_factory = factory
                    yield client, connection, factory
            finally:
                outer.rollback()
    finally:
        engine.dispose()


mysql = pytest.mark.skipif(os.getenv("RUN_MYSQL_TESTS") != "1", reason="Set RUN_MYSQL_TESTS=1 for real MySQL")


@pytest.mark.mysql
@mysql
def test_mysql_create_detail_and_independent_request_sessions(mysql_api):
    client, connection, factory = mysql_api
    before = connection.scalar(select(func.count()).select_from(Ticket))
    response = client.post("/tickets", json={"title": "  支付故障  ", "content": "连接池超时 🛠"})
    assert response.status_code == 201, response.text
    body = response.json()
    ticket = body["data"]
    assert body["success"] is True and body["error"] is None
    assert ticket["title"] == "支付故障" and ticket["content"] == "连接池超时 🛠"
    assert (ticket["category"], ticket["priority"], ticket["status"]) == ("other", "medium", "open")
    assert ticket["service_name"] is None and ticket["resolution"] is None
    assert ticket["created_at"].endswith("Z") and ticket["updated_at"].endswith("Z")
    detail = client.get(f"/tickets/{ticket['id']}")
    assert detail.status_code == 200 and detail.json() == body
    assert factory.call_count == 2
    assert connection.scalar(select(func.count()).select_from(Ticket)) == before + 1


@pytest.mark.mysql
@mysql
def test_mysql_ticket_filters_total_and_stable_pages(mysql_api):
    client, connection, _ = mysql_api
    service = f"test_{uuid4().hex}"
    same_time = datetime(2026, 9, 1)
    with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
        tickets = [Ticket(title=f"ticket{i}", content="test", service_name=service,
                          category=TicketCategory.INCIDENT, priority=TicketPriority.HIGH,
                          created_at=same_time) for i in range(3)]
        session.add_all(tickets)
        session.flush()
        expected_ids = sorted([ticket.id for ticket in tickets], reverse=True)
        session.add_all([
            Ticket(title="other service", content="test", service_name=service + "_other",
                   category=TicketCategory.INCIDENT, priority=TicketPriority.HIGH),
            Ticket(title="other status", content="test", service_name=service, status=TicketStatus.CLOSED,
                   category=TicketCategory.INCIDENT, priority=TicketPriority.HIGH),
            Ticket(title="other priority", content="test", service_name=service, category=TicketCategory.INCIDENT),
            Ticket(title="other category", content="test", service_name=service, priority=TicketPriority.HIGH),
        ])
        session.commit()
    params = dict(service_name=service, status="open", category="incident", priority="high", page_size=2)
    first = client.get("/tickets", params=params).json()["data"]
    second = client.get("/tickets", params={**params, "page": 2}).json()["data"]
    assert first["total"] == second["total"] == 3
    assert first["page"] == 1 and first["page_size"] == 2
    assert [item["id"] for item in first["items"] + second["items"]] == expected_ids
    beyond = client.get("/tickets", params={**params, "page": 3}).json()["data"]
    assert beyond["items"] == [] and beyond["total"] == 3
    empty = client.get("/tickets", params={"service_name": service + "' OR 1=1 --"}).json()["data"]
    assert empty["items"] == [] and empty["total"] == 0


@pytest.mark.mysql
@mysql
def test_mysql_ticket_keyword_combines_with_service_and_escapes_wildcards(mysql_api):
    client, connection, _ = mysql_api
    service = f"test_{uuid4().hex}"
    with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
        session.add_all([
            Ticket(title="payment timeout", content="test", service_name=service),
            Ticket(title="payment timeout", content="test", service_name=service + "_other"),
            Ticket(title="database timeout", content="test", service_name=service),
            Ticket(title="rate 100% failed", content="test", service_name=service),
            Ticket(title="rate 1000 failed", content="test", service_name=service),
            Ticket(title="field_name failed", content="test", service_name=service),
            Ticket(title="fieldXname failed", content="test", service_name=service),
        ])
        session.commit()

    keyword_result = client.get(
        "/tickets", params={"service_name": service, "keyword": "payment"}
    )
    assert keyword_result.status_code == 200, keyword_result.text
    keyword_data = keyword_result.json()["data"]
    assert keyword_data["total"] == 1
    assert [item["title"] for item in keyword_data["items"]] == ["payment timeout"]

    percent_data = client.get(
        "/tickets", params={"service_name": service, "keyword": "%"}
    ).json()["data"]
    assert percent_data["total"] == 1
    assert [item["title"] for item in percent_data["items"]] == ["rate 100% failed"]

    underscore_data = client.get(
        "/tickets", params={"service_name": service, "keyword": "_"}
    ).json()["data"]
    assert underscore_data["total"] == 1
    assert [item["title"] for item in underscore_data["items"]] == ["field_name failed"]


@pytest.mark.mysql
@mysql
def test_mysql_log_filters_timezones_and_pagination(mysql_api):
    client, connection, _ = mysql_api
    service = f"test_{uuid4().hex}"
    request_id = uuid4().hex
    with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
        logs = [ErrorLog(service_name=service, level=LogLevel.ERROR, error_type="DB_TIMEOUT",
                         request_id=request_id, message="数据库超时", stack_trace="test stack",
                         created_at=datetime(2026, 9, 1, hour)) for hour in (0, 1, 2)]
        session.add_all(logs)
        session.flush()
        expected = [logs[1].id, logs[0].id]
        for changes in [dict(level=LogLevel.INFO), dict(error_type="OTHER"),
                        dict(request_id="other"), dict(service_name=service + "_other")]:
            values = dict(service_name=service, level=LogLevel.ERROR, error_type="DB_TIMEOUT",
                          request_id=request_id, message="excluded", created_at=datetime(2026, 9, 1))
            session.add(ErrorLog(**(values | changes)))
        session.commit()
    params = dict(service_name=service, level="ERROR", error_type="DB_TIMEOUT", request_id=request_id,
                  created_from="2026-09-01T08:00:00+08:00", created_to="2026-09-01T10:00:00+08:00", page_size=1)
    first_response = client.get("/logs", params=params)
    assert first_response.status_code == 200, first_response.text
    first = first_response.json()["data"]
    second = client.get("/logs", params=params | {"page": 2}).json()["data"]
    assert first["total"] == second["total"] == 2
    assert [item["id"] for item in first["items"] + second["items"]] == expected
    assert first["items"][0]["created_at"] == "2026-09-01T01:00:00Z"
    assert first["items"][0]["message"] == "数据库超时"
    empty = client.get("/logs", params={"service_name": service + "_missing"}).json()["data"]
    assert empty["items"] == [] and empty["total"] == 0


@pytest.mark.mysql
@mysql
def test_mysql_insert_is_rolled_back_when_commit_fails(mysql_api):
    client, connection, factory = mysql_api
    before = connection.scalar(select(func.count()).select_from(Ticket))
    real_session = Session(bind=connection, join_transaction_mode="create_savepoint")
    factory.side_effect = None
    factory.return_value = real_session

    @event.listens_for(real_session, "before_commit")
    def fail_commit(session):
        raise OperationalError("test commit failure", {}, Exception("private-password"))

    response = client.post("/tickets", json={"title": "rollback test", "content": "must not persist"})
    assert response.status_code == 503, response.text
    assert not real_session.in_transaction()
    assert connection.scalar(select(func.count()).select_from(Ticket)) == before
