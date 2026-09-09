"""Grounded incident analysis over bounded tool evidence."""

import json
import logging
from pathlib import Path
from typing import Any

from backend.schemas.agent import IncidentAnalysis, IncidentAnalysisDecision
from backend.services.llm_service import LLMServiceError, StructuredLLM


logger = logging.getLogger(__name__)
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "incident_analysis.txt"
INCIDENT_ANALYSIS_SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8").strip()


class IncidentAnalyzer:
    """Generate a structured analysis without treating model inference as fact."""

    def __init__(self, llm: StructuredLLM | None) -> None:
        self._llm = llm

    def analyze(
        self,
        query: str,
        evidence: list[dict[str, Any]],
    ) -> IncidentAnalysisDecision:
        if self._llm is None or not evidence:
            return IncidentAnalysisDecision(
                analysis=_fallback_analysis(evidence),
                source="fallback",
            )

        last_error: LLMServiceError | None = None
        for _attempt in range(2):
            try:
                analysis = self._llm.generate_structured(
                    INCIDENT_ANALYSIS_SYSTEM_PROMPT,
                    "INCIDENT_JSON: "
                    + json.dumps(
                        {
                            "query": query,
                            "evidence": _compact_evidence(evidence),
                        },
                        ensure_ascii=False,
                    ),
                    IncidentAnalysis,
                )
                valid_steps = {item.get("step") for item in evidence}
                if not analysis.evidence_steps or any(
                    step not in valid_steps for step in analysis.evidence_steps
                ):
                    raise LLMServiceError(
                        "Incident analysis cited invalid evidence steps."
                    )
            except LLMServiceError as exc:
                last_error = exc
                continue
            return IncidentAnalysisDecision(analysis=analysis, source="llm")

        logger.warning(
            "INCIDENT_ANALYSIS_LLM_ERROR: using grounded fallback (%s)",
            type(last_error.__cause__).__name__
            if last_error is not None and last_error.__cause__
            else type(last_error).__name__,
        )
        return IncidentAnalysisDecision(
            analysis=_fallback_analysis(evidence),
            source="fallback",
        )


def _compact_evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bound untrusted database text before placing it in an LLM prompt."""
    return [_compact_value(item) for item in evidence]


def _compact_value(value: Any) -> Any:
    if isinstance(value, str):
        return value[:1_000]
    if isinstance(value, list):
        return [_compact_value(item) for item in value[:10]]
    if isinstance(value, dict):
        return {str(key): _compact_value(item) for key, item in value.items()}
    return value


def _fallback_analysis(evidence: list[dict[str, Any]]) -> IncidentAnalysis:
    facts: list[str] = []
    evidence_steps: list[int] = []
    for item in evidence:
        step = item.get("step")
        tool = item.get("tool", "unknown")
        output = item.get("output", {})
        total = output.get("total") if isinstance(output, dict) else None
        if isinstance(step, int):
            evidence_steps.append(step)
        if isinstance(total, int):
            facts.append(f"步骤 {step} 的 {tool} 返回 {total} 条匹配记录。")
        else:
            facts.append(f"步骤 {step} 已完成工具 {tool}。")
    if not facts:
        facts.append("本次运行没有获得可用于故障判断的工具证据。")
    return IncidentAnalysis(
        summary="已完成证据收集；当前没有可用的 LLM 分析结果。",
        confirmed_facts=facts[:10],
        hypotheses=[],
        recommended_actions=["检查各工具返回的原始证据后再确定故障原因。"],
        evidence_steps=evidence_steps,
    )
