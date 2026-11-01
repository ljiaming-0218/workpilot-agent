"""Read contracts for persisted Agent runs and their audited history."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.models.enums import AgentRunStatus, RiskLevel
from backend.schemas.agent import VerifierReport
from backend.schemas.common import UtcDatetime


class AgentRunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_id: str
    creator_user_id: int | None = None
    team_id: int | None = None
    user_query: str
    intent: str | None
    status: AgentRunStatus
    started_at: UtcDatetime
    finished_at: UtcDatetime | None
    latency_ms: int | None
    queue_status: str | None = None


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
    mode: Literal["single", "multi"] = "single"
    agent_tasks: list[dict[str, Any]] = Field(default_factory=list)
    investigation_status: Literal["completed", "partial", "failed"] | None = None
    tool_budget: dict[str, int] | None = None
    verifier: VerifierReport | None = None
    model_budget: dict[str, int] | None = None
