"""HTTP contracts for one synchronous basic Agent run."""

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field


AgentIntent: TypeAlias = Literal[
    "ticket_search",
    "log_analysis",
    "knowledge_search",
    "data_query",
    "incident_analysis",
    "general",
]


class LLMRoutingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: AgentIntent
    confidence: float = Field(ge=0, le=1)


class RoutingDecision(LLMRoutingDecision):
    source: Literal["rule", "llm", "fallback"]


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=2_000)


class AgentResult(BaseModel):
    run_id: int
    request_id: str
    intent: AgentIntent
    routing_source: Literal["rule", "llm", "fallback"]
    routing_confidence: float
    selected_tool: str | None
    final_answer: str
    tool_results: list[dict[str, Any]]
    error: str | None
