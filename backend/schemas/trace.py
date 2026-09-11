"""Public response contracts for ordered Agent trace events."""

from typing import Any

from pydantic import BaseModel, ConfigDict

from backend.models.enums import AgentRunStatus, TraceStatus
from backend.schemas.common import UtcDatetime


class AgentTraceEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    step: int
    node: str
    event_type: str
    intent: str | None
    tool_name: str | None
    input_json: dict[str, Any] | None
    output_json: dict[str, Any] | None
    tool_input: dict[str, Any] | None
    tool_output_summary: dict[str, Any] | None
    sql: str | None
    sql_guard_result: dict[str, Any] | None
    retrieval_result: dict[str, Any] | None
    model: str | None
    tokens_json: dict[str, int] | None
    latency_ms: int
    status: TraceStatus
    error: str | None
    created_at: UtcDatetime


class AgentTraceRead(BaseModel):
    run_id: int
    request_id: str
    run_status: AgentRunStatus
    events: list[AgentTraceEventRead]
