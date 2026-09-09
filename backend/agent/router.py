"""High-confidence rules with structured LLM fallback for intent routing."""

import json
import logging
from pathlib import Path
import re
from typing import Any

from backend.schemas.agent import LLMRoutingDecision, RoutingDecision
from backend.services.llm_service import LLMServiceError, StructuredLLM


logger = logging.getLogger(__name__)


INTENT_GENERAL = "general"
INTENT_TICKET_SEARCH = "ticket_search"
INTENT_LOG_ANALYSIS = "log_analysis"
INTENT_KNOWLEDGE_SEARCH = "knowledge_search"
INTENT_DATA_QUERY = "data_query"
INTENT_INCIDENT_ANALYSIS = "incident_analysis"

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "router.txt"
ROUTER_SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8").strip()

_INCIDENT_WORDS = ("故障", "异常", "报错", "错误", "失败", "超时", "500")
_ANALYSIS_WORDS = ("分析", "原因", "排查", "诊断")
_LOG_WORDS = ("日志", "error log", "错误记录", "报错记录")
_KNOWLEDGE_WORDS = ("知识库", "文档", "操作手册", "解决方案")
_TICKET_WORDS = ("工单", "ticket")
_DATA_WORDS = ("统计", "数量", "多少", "总数", "趋势", "分布")
_TICKET_ID_PATTERN = re.compile(
    r"(?:工单|ticket)\s*(?:id|编号|#|：|:)?\s*(\d+)", re.IGNORECASE,
)


class IntentRouter:
    """Return a constrained routing decision without letting free text become an intent."""

    def __init__(self, llm: StructuredLLM | None) -> None:
        self._llm = llm

    def route(self, query: str) -> RoutingDecision:
        rule_decision = match_rule(query)
        if rule_decision is not None:
            return rule_decision
        if self._llm is None:
            return RoutingDecision(intent=INTENT_GENERAL, confidence=0, source="fallback")

        try:
            decision = self._llm.generate_structured(
                ROUTER_SYSTEM_PROMPT,
                "USER_QUERY_JSON: " + json.dumps(query, ensure_ascii=False),
                LLMRoutingDecision,
            )
        except LLMServiceError as exc:
            logger.warning(
                "ROUTER_LLM_ERROR: using general fallback (%s)",
                type(exc.__cause__).__name__,
            )
            return RoutingDecision(intent=INTENT_GENERAL, confidence=0, source="fallback")
        return RoutingDecision(
            intent=decision.intent,
            confidence=decision.confidence,
            source="llm",
        )


def match_rule(query: str) -> RoutingDecision | None:
    """Return only high-confidence matches so ambiguous queries can reach the LLM."""
    normalized = query.casefold()
    if any(word in normalized for word in _INCIDENT_WORDS) and any(
        word in normalized for word in _ANALYSIS_WORDS
    ):
        return RoutingDecision(
            intent=INTENT_INCIDENT_ANALYSIS, confidence=0.98, source="rule",
        )
    if any(word in normalized for word in _LOG_WORDS):
        return RoutingDecision(intent=INTENT_LOG_ANALYSIS, confidence=0.95, source="rule")
    if any(word in normalized for word in _KNOWLEDGE_WORDS):
        return RoutingDecision(
            intent=INTENT_KNOWLEDGE_SEARCH, confidence=0.95, source="rule",
        )
    if any(word in normalized for word in _TICKET_WORDS):
        return RoutingDecision(intent=INTENT_TICKET_SEARCH, confidence=0.95, source="rule")
    if any(word in normalized for word in _DATA_WORDS):
        return RoutingDecision(intent=INTENT_DATA_QUERY, confidence=0.9, source="rule")
    return None


def select_tool(query: str, intent: str) -> tuple[str, dict[str, Any]]:
    """Map one intent to at most one existing read-only tool."""
    if intent == INTENT_KNOWLEDGE_SEARCH:
        return "search_knowledge", {"query": query, "limit": 5}
    if intent == INTENT_TICKET_SEARCH:
        match = _TICKET_ID_PATTERN.search(query)
        if match is not None:
            return "get_ticket_detail", {"ticket_id": int(match.group(1))}

    if intent in {
        INTENT_TICKET_SEARCH,
        INTENT_LOG_ANALYSIS,
        INTENT_KNOWLEDGE_SEARCH,
        INTENT_DATA_QUERY,
        INTENT_INCIDENT_ANALYSIS,
    }:
        return "query_database", {"question": query}
    return "", {}
