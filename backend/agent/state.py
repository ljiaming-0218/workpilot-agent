"""Serializable graph state and per-invocation runtime dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict

from sqlalchemy.orm import Session

from backend.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from backend.agent.analyzer import IncidentAnalyzer
    from backend.agent.planner import Planner
    from backend.agent.router import IntentRouter


class AgentState(TypedDict, total=False):
    """Mutable data passed from one LangGraph node to the next."""

    query: str
    intent: str
    routing_source: str
    routing_confidence: float
    plan: list[dict[str, Any]]
    current_step: int
    max_steps: int
    planner_source: str
    selected_tool: str
    tool_inputs: dict[str, Any]
    tool_results: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    analysis: dict[str, Any]
    analysis_source: str
    risk_level: str
    requires_approval: bool
    final_answer: str
    error: str | None


@dataclass(frozen=True, slots=True)
class AgentContext:
    """Non-serializable resources available only during one graph invocation."""

    session: Session
    registry: ToolRegistry
    agent_run_id: int
    intent_router: IntentRouter
    planner: Planner
    incident_analyzer: IncidentAnalyzer
