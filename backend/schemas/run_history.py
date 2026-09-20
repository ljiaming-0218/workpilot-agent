"""Read contracts for persisted Agent runs and their audited history."""

from typing import Any

from pydantic import BaseModel, ConfigDict

from backend.models.enums import AgentRunStatus, RiskLevel
from backend.schemas.common import UtcDatetime


class AgentRunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_id: str
    user_query: str
    intent: str | None
    status: AgentRunStatus
    started_at: UtcDatetime
    finished_at: UtcDatetime | None
    latency_ms: int | None


class AgentRunDetail(AgentRunSummary):
    final_answer: str | None
    plan: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    routing_source: str | None
    routing_confidence: float | None
    planner_source: str | None
    risk_level: RiskLevel | None
    risk_reasons: list[str]
    steps_executed: int
