"""HTTP contracts for one synchronous basic Agent run."""

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from backend.models.enums import AgentRunStatus, RiskLevel


AgentIntent: TypeAlias = Literal[
    "operation",
    "ticket_search",
    "log_analysis",
    "knowledge_search",
    "data_query",
    "incident_analysis",
    "general",
]
AgentToolName: TypeAlias = Literal[
    "simulate_high_risk_operation",
    "search_tickets",
    "get_ticket_detail",
    "query_error_logs",
    "query_database",
    "search_knowledge",
]


class LLMRoutingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: AgentIntent
    confidence: float = Field(ge=0, le=1)


class RoutingDecision(LLMRoutingDecision):
    source: Literal["rule", "llm", "fallback"]


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: AgentToolName
    inputs: dict[str, Any]


class GeneratedPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: list[PlanStep] = Field(min_length=1, max_length=5)


class PlannerDecision(BaseModel):
    steps: list[PlanStep] = Field(max_length=5)
    source: Literal["rule", "llm", "fallback"]


class IncidentAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=2_000)
    confirmed_facts: list[str] = Field(min_length=1, max_length=10)
    hypotheses: list[str] = Field(max_length=5)
    recommended_actions: list[str] = Field(min_length=1, max_length=8)
    evidence_steps: list[int] = Field(max_length=5)


class IncidentAnalysisDecision(BaseModel):
    analysis: IncidentAnalysis
    source: Literal["llm", "fallback"]
    evidence_links: list[str] = Field(default_factory=list, max_length=3)


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=2_000)


ApprovalAction: TypeAlias = Literal["approve", "reject", "cancel"]
ApprovalStatus: TypeAlias = Literal["pending", "approved", "rejected", "cancelled"]


class AgentApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ApprovalAction


class AgentResult(BaseModel):
    run_id: int
    request_id: str
    intent: AgentIntent
    routing_source: Literal["rule", "llm", "fallback"]
    routing_confidence: float
    plan: list[PlanStep]
    planner_source: Literal["rule", "llm", "fallback"]
    steps_executed: int
    selected_tool: str | None
    final_answer: str
    tool_results: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    analysis: IncidentAnalysis | None
    analysis_source: Literal["llm", "fallback"] | None
    risk_level: RiskLevel
    requires_approval: bool
    risk_reasons: list[str]
    status: AgentRunStatus
    approval_status: ApprovalStatus | None
    error: str | None
