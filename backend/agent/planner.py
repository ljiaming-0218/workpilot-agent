"""Bounded plan generation for the Phase 7 plan-then-execute workflow."""

import json
import logging
from pathlib import Path
import re

from backend.agent.router import INTENT_GENERAL, INTENT_INCIDENT_ANALYSIS, select_tool
from backend.schemas.agent import GeneratedPlan, PlanStep, PlannerDecision
from backend.services.llm_service import LLMServiceError, StructuredLLM


logger = logging.getLogger(__name__)
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "planner.txt"
PLANNER_SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8").strip()
SERVICE_NAME_PATTERN = re.compile(
    r"(?:服务|service)\s*[:：]?\s*([a-z0-9][a-z0-9._-]{0,127})",
    re.IGNORECASE,
)
BARE_SERVICE_NAME_PATTERN = re.compile(
    r"\b([a-z0-9][a-z0-9._-]*-service)\b",
    re.IGNORECASE,
)
ERROR_TYPE_PATTERN = re.compile(r"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\b")
TIME_WINDOW_PATTERN = re.compile(
    r"(?:最近|近)\s*(\d+)\s*(分钟|小时|天)",
)


class Planner:
    """Create an ordered plan capped by max_steps."""

    def __init__(self, llm: StructuredLLM | None, max_steps: int = 5) -> None:
        if not 1 <= max_steps <= 5:
            raise ValueError("max_steps must be between 1 and 5.")
        self._llm = llm
        self.max_steps = max_steps

    def create_plan(self, query: str, intent: str) -> PlannerDecision:
        if intent == INTENT_GENERAL:
            return PlannerDecision(steps=[], source="rule")
        if intent == INTENT_INCIDENT_ANALYSIS:
            incident_steps = _incident_evidence_steps(query)
            if incident_steps:
                return PlannerDecision(
                    steps=incident_steps[: self.max_steps],
                    source="rule",
                )
        if intent != INTENT_INCIDENT_ANALYSIS:
            return PlannerDecision(steps=_single_step(query, intent), source="rule")
        if self._llm is None:
            return PlannerDecision(
                steps=_single_step(query, intent), source="fallback",
            )

        try:
            generated = self._llm.generate_structured(
                PLANNER_SYSTEM_PROMPT,
                "TASK_JSON: " + json.dumps(
                    {"query": query, "intent": intent}, ensure_ascii=False,
                ),
                GeneratedPlan,
            )
        except LLMServiceError as exc:
            logger.warning(
                "PLANNER_LLM_ERROR: using single-step fallback (%s)",
                type(exc.__cause__).__name__,
            )
            return PlannerDecision(
                steps=_single_step(query, intent), source="fallback",
            )
        return PlannerDecision(
            steps=generated.steps[: self.max_steps],
            source="llm",
        )


def _single_step(query: str, intent: str) -> list[PlanStep]:
    tool_name, tool_inputs = select_tool(query, intent)
    if not tool_name:
        return []
    return [PlanStep(tool=tool_name, inputs=tool_inputs)]


def _incident_evidence_steps(query: str) -> list[PlanStep]:
    match = SERVICE_NAME_PATTERN.search(query)
    if match is None:
        match = BARE_SERVICE_NAME_PATTERN.search(query)
    if match is None:
        return []
    service_name = match.group(1)
    log_inputs: dict[str, object] = {
        "service_name": service_name,
        "minutes": _log_window_minutes(query),
        "limit": 20,
    }
    error_type = ERROR_TYPE_PATTERN.search(query)
    if error_type is not None:
        log_inputs["error_type"] = error_type.group(1)
    return [
        PlanStep(
            tool="query_error_logs",
            inputs=log_inputs,
        ),
        PlanStep(
            tool="search_tickets",
            inputs={"service_name": service_name, "limit": 10},
        ),
        PlanStep(
            tool="search_knowledge",
            inputs={"query": query, "limit": 5},
        ),
    ]


def _log_window_minutes(query: str) -> int:
    match = TIME_WINDOW_PATTERN.search(query)
    if match is None:
        return 60 if "最近" in query else 10_080
    amount = int(match.group(1))
    multiplier = {"分钟": 1, "小时": 60, "天": 1_440}[match.group(2)]
    return max(1, min(amount * multiplier, 10_080))
