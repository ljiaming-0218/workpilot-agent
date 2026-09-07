"""Real MySQL model tests; all test writes are rolled back, never committed to the database."""

import os
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, StatementError
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database import Base, build_engine
from backend.models import AgentRun, ErrorLog, KnowledgeDoc, Ticket, ToolCallLog, User
from backend.models.enums import AgentRunStatus, LogLevel, RiskLevel, TicketStatus, UserRole


pytestmark = [
    pytest.mark.mysql,
    pytest.mark.skipif(os.getenv("RUN_MYSQL_TESTS") != "1", reason="Set RUN_MYSQL_TESTS=1 for real MySQL"),
]


@pytest.fixture(scope="module")
def engine():
    db_engine = build_engine(Settings())
    try:
        missing = set(Base.metadata.tables) - set(inspect(db_engine).get_table_names())
        assert not missing, f"Run python -m scripts.init_db first; missing tables: {sorted(missing)}"
        yield db_engine
    finally:
        db_engine.dispose()


@pytest.fixture
def session(engine):
    # A Session commit releases a savepoint; the outer test transaction still rolls back.
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            with Session(bind=connection, join_transaction_mode="create_savepoint") as db_session:
                yield db_session
        finally:
            transaction.rollback()


def test_all_models_round_trip_and_defaults(session):
    token = uuid4().hex
    user = User(username=f"test_{token}")
    ticket = Ticket(title="支付服务超时", content="连接池排查 🛠", service_name="payment-service")
    log = ErrorLog(service_name="payment-service", level=LogLevel.ERROR, message="DB_TIMEOUT")
    doc = KnowledgeDoc(title="排查手册", content="检查连接池", source="test")
    run = AgentRun(request_id=token, user_query="为什么支付失败？")
    call = ToolCallLog(
        agent_run=run, tool_name="query_error_logs", risk_level=RiskLevel.LOW,
        input_json={"service_name": "payment-service"},
        output_json={"rows": [{"error": "连接超时"}]},
    )
    session.add_all([user, ticket, log, doc, run, call])
    session.flush()
    assert all(item.id is not None for item in (user, ticket, log, doc, run, call))
    session.expire_all()
    assert user.role == UserRole.DEVELOPER
    assert ticket.status == TicketStatus.OPEN
    assert run.status == AgentRunStatus.RUNNING
    assert run.finished_at is None and run.latency_ms is None
    assert ticket.content == "连接池排查 🛠"
    assert call.output_json == {"rows": [{"error": "连接超时"}]}
    assert run.tool_calls == [call] and call.agent_run is run
    assert session.scalar(text("SELECT status FROM tickets WHERE id=:id"), {"id": ticket.id}) == "open"
    assert doc.created_at is not None and log.created_at is not None

    ticket.updated_at = datetime(2000, 1, 1)
    session.flush()
    ticket.title = "支付服务已恢复"
    session.flush()
    session.refresh(ticket)
    assert ticket.updated_at > datetime(2000, 1, 1)


def test_duplicate_username_is_rejected_without_losing_outer_transaction(session):
    username = f"test_{uuid4().hex}"
    user = User(username=username)
    session.add(user)
    session.flush()
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(User(username=username))
            session.flush()
    assert session.get(User, user.id) is user


def test_tool_call_cannot_reference_missing_run(session):
    assert session.get(AgentRun, -1) is None
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(ToolCallLog(
                agent_run_id=-1, tool_name="test", input_json={}, risk_level=RiskLevel.LOW
            ))
            session.flush()


def test_run_with_tool_calls_cannot_be_deleted(session):
    run = AgentRun(request_id=uuid4().hex, user_query="test")
    call = ToolCallLog(agent_run=run, tool_name="test", input_json={}, risk_level=RiskLevel.LOW)
    session.add_all([run, call])
    session.flush()
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.delete(run)
            session.flush()


@pytest.mark.parametrize("target", ["run", "tool_call"])
def test_negative_latency_is_rejected(session, target):
    run = AgentRun(request_id=uuid4().hex, user_query="test")
    session.add(run)
    session.flush()
    # PyMySQL reports MySQL CHECK violations (3819) as OperationalError.
    with pytest.raises(DBAPIError) as error:
        with session.begin_nested():
            if target == "run":
                run.latency_ms = -1
            else:
                session.add(ToolCallLog(
                    agent_run=run, tool_name="test", input_json={}, risk_level=RiskLevel.LOW,
                    latency_ms=-1,
                ))
            session.flush()
    assert error.value.orig.args[0] == 3819


def test_invalid_ticket_status_is_rejected(session):
    with pytest.raises(StatementError):
        with session.begin_nested():
            session.add(Ticket(title="test", content="test", status="unknown"))
            session.flush()


def test_session_commit_stays_inside_rollback_only_test_transaction(engine):
    username = f"rollback_{uuid4().hex}"
    with engine.connect() as connection:
        outer = connection.begin()
        try:
            with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
                session.add(User(username=username))
                session.commit()
                assert session.scalar(select(User).where(User.username == username)) is not None
        finally:
            outer.rollback()
    with Session(engine) as verification:
        assert verification.scalar(select(User).where(User.username == username)) is None


def test_live_schema_has_lookup_indexes_and_foreign_key(engine):
    inspector = inspect(engine)
    for table, columns in {
        "tickets": {"service_name", "category", "status", "created_at"},
        "error_logs": {"service_name", "level", "error_type", "created_at"},
    }.items():
        indexed = {tuple(index["column_names"]) for index in inspector.get_indexes(table)}
        assert all((column,) in indexed for column in columns)
    foreign_keys = inspector.get_foreign_keys("tool_call_logs")
    assert any(
        key["constrained_columns"] == ["agent_run_id"]
        and key["referred_table"] == "agent_runs"
        and key["referred_columns"] == ["id"]
        for key in foreign_keys
    )
