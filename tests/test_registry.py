"""Tool Registry unit tests plus opt-in real MySQL execution and audit tests."""

import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database import Base, build_engine
from backend.models import AgentRun, ErrorLog, Ticket, ToolCallLog
from backend.models.enums import LogLevel, RiskLevel, ToolCallStatus
from backend.tools import create_default_registry
from backend.tools.base import BaseTool, ToolContext
from backend.tools.registry import (
    ToolAlreadyRegisteredError,
    ToolDisabledError,
    ToolExecutionError,
    ToolInputValidationError,
    ToolNotFoundError,
    ToolOutputValidationError,
    ToolRegistry,
)


class ExampleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int = Field(ge=1)


class ExampleOutput(BaseModel):
    doubled: int


class ExampleTool(BaseTool[ExampleInput, ExampleOutput]):
    name = "example_tool"
    description = "Double a positive integer."
    input_schema = ExampleInput
    output_schema = ExampleOutput
    risk_level = RiskLevel.LOW

    def execute(self, context: ToolContext, tool_input: ExampleInput) -> ExampleOutput:
        return ExampleOutput(doubled=tool_input.value * 2)


class DisabledTool(ExampleTool):
    name = "disabled_tool"
    enabled = False


class InvalidOutputTool(ExampleTool):
    name = "invalid_output_tool"

    def execute(self, context: ToolContext, tool_input: ExampleInput):
        return {"wrong": tool_input.value}


class FailingTool(ExampleTool):
    name = "failing_tool"

    def execute(self, context: ToolContext, tool_input: ExampleInput) -> ExampleOutput:
        raise RuntimeError("private failure detail")


def test_register_get_list_and_duplicate_rejection():
    registry = ToolRegistry([ExampleTool(), DisabledTool()])
    assert [tool.name for tool in registry.list_tools()] == ["disabled_tool", "example_tool"]
    definition = registry.get_tool("example_tool")
    assert definition.input_schema is ExampleInput
    assert definition.output_schema is ExampleOutput
    assert definition.risk_level == RiskLevel.LOW
    assert definition.enabled is True
    with pytest.raises(ToolAlreadyRegisteredError):
        registry.register_tool(ExampleTool())
    with pytest.raises(ToolNotFoundError):
        registry.get_tool("missing_tool")


def test_disabled_and_invalid_input_are_rejected_without_exposing_extra_values():
    session = MagicMock(spec=Session)
    context = ToolContext(session=session, agent_run_id=1)
    registry = ToolRegistry([ExampleTool(), DisabledTool()])
    with pytest.raises(ToolDisabledError):
        registry.call_tool("disabled_tool", {"value": 2}, context)
    with pytest.raises(ToolInputValidationError) as exc_info:
        registry.call_tool("example_tool", {"value": 0, "secret": "not echoed"}, context)
    assert exc_info.value.errors
    assert "not echoed" not in str(exc_info.value.errors)
    rejected_logs = [call.args[0] for call in session.add.call_args_list]
    assert [log.status for log in rejected_logs] == [
        ToolCallStatus.REJECTED, ToolCallStatus.REJECTED,
    ]
    assert rejected_logs[0].input_json == {"value": 2}
    assert rejected_logs[1].input_json == {"value": 0}
    assert "secret" not in rejected_logs[1].input_json
    session.flush.assert_not_called()
    assert session.commit.call_count == 2


def test_non_mapping_input_is_rejected_and_audited_without_crashing():
    session = MagicMock(spec=Session)
    registry = ToolRegistry([ExampleTool()])
    with pytest.raises(ToolInputValidationError):
        registry.call_tool(
            "example_tool", None, ToolContext(session=session, agent_run_id=1),
        )
    rejected_log = session.add.call_args.args[0]
    assert rejected_log.input_json == {}
    assert rejected_log.status == ToolCallStatus.REJECTED
    session.commit.assert_called_once()


def test_start_log_failure_prevents_handler_execution_and_restores_session():
    session = MagicMock(spec=Session)
    session.flush.side_effect = OperationalError("private SQL", {}, Exception("private"))
    tool = ExampleTool()
    tool.execute = MagicMock(return_value=ExampleOutput(doubled=4))
    registry = ToolRegistry([tool])
    with pytest.raises(ToolExecutionError, match="Could not start"):
        registry.call_tool(
            "example_tool", {"value": 2}, ToolContext(session=session, agent_run_id=1),
        )
    tool.execute.assert_not_called()
    session.rollback.assert_called_once()
    session.commit.assert_not_called()


def test_valid_call_validates_output_and_commits_completed_log():
    session = MagicMock(spec=Session)
    registry = ToolRegistry([ExampleTool()])
    output = registry.call_tool(
        "example_tool", {"value": 3}, ToolContext(session=session, agent_run_id=10),
    )
    assert output == ExampleOutput(doubled=6)
    call_log = session.add.call_args.args[0]
    assert call_log.agent_run_id == 10
    assert call_log.input_json == {"value": 3}
    assert call_log.output_json == {"doubled": 6}
    assert call_log.status == ToolCallStatus.COMPLETED
    assert call_log.latency_ms >= 0
    session.flush.assert_called_once()
    session.commit.assert_called_once()
    session.rollback.assert_not_called()


def test_result_commit_failure_rolls_back_and_records_failed_call():
    session = MagicMock(spec=Session)
    session.commit.side_effect = [
        OperationalError("private SQL", {}, Exception("private")),
        None,
    ]
    registry = ToolRegistry([ExampleTool()])
    with pytest.raises(ToolExecutionError, match="Could not commit"):
        registry.call_tool(
            "example_tool", {"value": 2}, ToolContext(session=session, agent_run_id=1),
        )
    assert session.rollback.call_count == 1
    assert session.commit.call_count == 2
    failed_log = session.add.call_args.args[0]
    assert failed_log.status == ToolCallStatus.FAILED
    assert failed_log.output_json is None
    assert failed_log.error_message == "Tool failed: OperationalError"


@pytest.mark.parametrize("tool,error_type", [
    (InvalidOutputTool(), ToolOutputValidationError),
    (FailingTool(), ToolExecutionError),
])
def test_tool_failure_rolls_back_and_writes_sanitized_failed_log(tool, error_type):
    session = MagicMock(spec=Session)
    registry = ToolRegistry([tool])
    with pytest.raises(error_type):
        registry.call_tool(
            tool.name, {"value": 2}, ToolContext(session=session, agent_run_id=20),
        )
    assert session.rollback.call_count == 1
    assert session.commit.call_count == 1
    failed_log = session.add.call_args.args[0]
    assert failed_log.status == ToolCallStatus.FAILED
    assert failed_log.latency_ms >= 0
    assert "private failure detail" not in failed_log.error_message


mysql = pytest.mark.skipif(
    os.getenv("RUN_MYSQL_TESTS") != "1", reason="Set RUN_MYSQL_TESTS=1 for real MySQL",
)


@pytest.fixture
def mysql_registry():
    engine = build_engine(Settings())
    try:
        assert set(Base.metadata.tables) <= set(inspect(engine).get_table_names())
        with engine.connect() as connection:
            outer = connection.begin()
            try:
                with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
                    yield create_default_registry(), session, connection
            finally:
                outer.rollback()
    finally:
        engine.dispose()


def create_run(session: Session) -> AgentRun:
    run = AgentRun(request_id=uuid4().hex, user_query="registry test")
    session.add(run)
    session.commit()
    return run


@pytest.mark.mysql
@mysql
def test_real_tools_reuse_services_and_persist_completed_audit_logs(mysql_registry):
    registry, session, _ = mysql_registry
    run = create_run(session)
    service = f"test_{uuid4().hex}"
    recent = datetime.utcnow()
    tickets = [
        Ticket(title="DB_TIMEOUT investigation", content="test", service_name=service),
        Ticket(title="other incident", content="test", service_name=service),
        Ticket(title="DB_TIMEOUT elsewhere", content="test", service_name=service + "_other"),
    ]
    logs = [
        ErrorLog(service_name=service, level=LogLevel.ERROR, error_type="DB_TIMEOUT",
                 message="recent", created_at=recent - timedelta(minutes=10)),
        ErrorLog(service_name=service, level=LogLevel.ERROR, error_type="OTHER",
                 message="other type", created_at=recent - timedelta(minutes=10)),
        ErrorLog(service_name=service, level=LogLevel.ERROR, error_type="DB_TIMEOUT",
                 message="old", created_at=recent - timedelta(minutes=120)),
    ]
    session.add_all(tickets + logs)
    session.commit()
    context = ToolContext(session=session, agent_run_id=run.id)

    search = registry.call_tool(
        "search_tickets",
        {"keyword": "DB_TIMEOUT", "service_name": service, "limit": 5},
        context,
    )
    assert search.total == 1 and [item.title for item in search.items] == ["DB_TIMEOUT investigation"]

    detail = registry.call_tool("get_ticket_detail", {"ticket_id": tickets[0].id}, context)
    assert detail.ticket is not None and detail.ticket.id == tickets[0].id
    missing = registry.call_tool("get_ticket_detail", {"ticket_id": 2_147_483_647}, context)
    assert missing.ticket is None

    log_result = registry.call_tool(
        "query_error_logs",
        {"service_name": service, "error_type": "DB_TIMEOUT", "minutes": 60, "limit": 10},
        context,
    )
    assert log_result.total == 1 and [item.message for item in log_result.items] == ["recent"]

    with pytest.raises(ToolInputValidationError):
        registry.call_tool(
            "search_tickets", {"keyword": "DB_TIMEOUT", "limit": 0}, context,
        )

    audit_logs = list(session.scalars(
        select(ToolCallLog)
        .where(ToolCallLog.agent_run_id == run.id)
        .order_by(ToolCallLog.id)
    ))
    assert [log.tool_name for log in audit_logs] == [
        "search_tickets", "get_ticket_detail", "get_ticket_detail", "query_error_logs",
        "search_tickets",
    ]
    assert [log.status for log in audit_logs] == [
        ToolCallStatus.COMPLETED,
        ToolCallStatus.COMPLETED,
        ToolCallStatus.COMPLETED,
        ToolCallStatus.COMPLETED,
        ToolCallStatus.REJECTED,
    ]
    assert all(log.latency_ms is not None and log.latency_ms >= 0 for log in audit_logs)
    assert audit_logs[0].input_json["keyword"] == "DB_TIMEOUT"
    assert audit_logs[0].output_json["total"] == 1
    assert audit_logs[-1].error_message == "Tool rejected: TOOL_INPUT_VALIDATION_ERROR"


@pytest.mark.mysql
@mysql
def test_real_output_failure_replaces_running_log_with_failed_audit(mysql_registry):
    _, session, _ = mysql_registry
    run = create_run(session)
    registry = ToolRegistry([InvalidOutputTool()])
    with pytest.raises(ToolOutputValidationError):
        registry.call_tool(
            "invalid_output_tool", {"value": 2},
            ToolContext(session=session, agent_run_id=run.id),
        )
    audit_logs = list(session.scalars(
        select(ToolCallLog).where(ToolCallLog.agent_run_id == run.id)
    ))
    assert len(audit_logs) == 1
    assert audit_logs[0].status == ToolCallStatus.FAILED
    assert audit_logs[0].output_json is None
    assert audit_logs[0].error_message == "Tool failed: TOOL_OUTPUT_VALIDATION_ERROR"
    assert session.scalar(
        select(func.count()).select_from(ToolCallLog).where(ToolCallLog.agent_run_id == run.id)
    ) == 1
