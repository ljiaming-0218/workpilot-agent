"""Grounded incident analysis over bounded tool evidence."""

import json
import logging
from datetime import datetime, timedelta
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
        investigations: list[dict[str, Any]] | None = None,
    ) -> IncidentAnalysisDecision:
        investigations = investigations or []
        evidence_links = _correlate_evidence(evidence)
        if self._llm is None or not evidence:
            return IncidentAnalysisDecision(
                analysis=_fallback_analysis(evidence, investigations),
                source="fallback",
                evidence_links=evidence_links,
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
                            "service_investigations": _compact_value(investigations),
                            "evidence_links": evidence_links,
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
                service_citations = [
                    item["citations"][0]
                    for item in investigations
                    if item.get("citations")
                ]
                analysis.evidence_steps = list(dict.fromkeys(
                    [*service_citations, *analysis.evidence_steps]
                ))[:5]
                if _needs_uncertainty(evidence, investigations):
                    analysis.summary = (
                        "证据存在冲突或不足，当前结论具有不确定性。"
                        + analysis.summary
                    )
            except LLMServiceError as exc:
                last_error = exc
                continue
            return IncidentAnalysisDecision(
                analysis=analysis,
                source="llm",
                evidence_links=evidence_links,
            )

        logger.warning(
            "INCIDENT_ANALYSIS_LLM_ERROR: using grounded fallback (%s)",
            type(last_error.__cause__).__name__
            if last_error is not None and last_error.__cause__
            else type(last_error).__name__,
        )
        return IncidentAnalysisDecision(
            analysis=_fallback_analysis(evidence, investigations),
            source="fallback",
            evidence_links=evidence_links,
        )


def _correlate_evidence(evidence: list[dict[str, Any]]) -> list[str]:
    services: list[str] = []
    for item in evidence:
        service = item.get("service")
        if isinstance(service, str) and service not in services:
            services.append(service)
    if not services:
        return _correlate_evidence_group(evidence)
    links: list[str] = []
    for service in services[:3]:
        group = [item for item in evidence if item.get("service") == service]
        group_links = _correlate_evidence_group(group)
        if group_links:
            links.append(group_links[0])
    return links[:3]


def _correlate_evidence_group(evidence: list[dict[str, Any]]) -> list[str]:
    """Link bounded records by service and event time; never infer causality."""
    outputs = {
        item.get("tool"): item
        for item in evidence
        if isinstance(item, dict) and isinstance(item.get("output"), dict)
    }
    log_result = outputs.get("query_error_logs", {})
    ticket_result = outputs.get("search_tickets", {})
    knowledge_result = outputs.get("search_knowledge", {})
    logs = log_result.get("output", {}).get("items", [])[:10]
    tickets = ticket_result.get("output", {}).get("items", [])[:10]
    docs = knowledge_result.get("output", {}).get("items", [])[:3]
    valid_logs = [row for row in logs if isinstance(row, dict)]
    times = [_parse_time(row.get("created_at")) for row in valid_logs]
    times = [value for value in times if value is not None]
    services = {row.get("service_name") for row in valid_logs}
    services.discard(None)
    links: list[str] = []

    if valid_logs:
        log_ids = [row["id"] for row in valid_logs if isinstance(row.get("id"), int)]
        if log_ids:
            links.append(
                f"日志步骤 {log_result['step']}：服务 {', '.join(sorted(services))} 的"
                f"日志 ID {', '.join(map(str, log_ids))}；仅展示前 10 条。"
            )
    if times and len(services) == 1:
        earliest, latest = min(times), max(times)
        matched = []
        for row in tickets:
            if not isinstance(row, dict) or row.get("service_name") not in services:
                continue
            created = _parse_time(row.get("created_at"))
            if created is not None and earliest - timedelta(hours=1) <= created <= latest + timedelta(hours=1):
                matched.append(row)
        ticket_ids = [row["id"] for row in matched if isinstance(row.get("id"), int)]
        if ticket_ids:
            links.append(
                f"工单步骤 {ticket_result['step']}：同服务且创建时间位于日志时间范围前后 1 小时的"
                f"工单 ID {', '.join(map(str, ticket_ids))}；时间接近不代表因果关系。"
            )
    doc_ids = [row["document_id"] for row in docs if isinstance(row, dict) and isinstance(row.get("document_id"), int)]
    if doc_ids:
        links.append(
            f"知识步骤 {knowledge_result['step']}：BM25 检索命中文档 ID "
            f"{', '.join(map(str, doc_ids))}；可作排查参考，不能证明当前根因。"
        )
    return links


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


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


def _fallback_analysis(
    evidence: list[dict[str, Any]],
    investigations: list[dict[str, Any]],
) -> IncidentAnalysis:
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
    for item in investigations:
        if item.get("error"):
            facts.append(
                f"服务 {item.get('service')} 调查状态为 {item.get('status')}，"
                f"错误为 {item.get('error')}。"
            )
    uncertain = _needs_uncertainty(evidence, investigations)
    return IncidentAnalysis(
        summary=(
            "证据存在冲突或不足，当前结论具有不确定性。"
            if uncertain else "已完成有界证据收集，但缺少可用的在线 LLM 分析结果。"
        ),
        confirmed_facts=facts[:10],
        hypotheses=[],
        recommended_actions=["检查各工具返回的原始证据后再确定故障原因。"],
        evidence_steps=list(dict.fromkeys(evidence_steps))[:5],
    )


def _needs_uncertainty(
    evidence: list[dict[str, Any]],
    investigations: list[dict[str, Any]],
) -> bool:
    if any(item.get("status") in {"partial", "failed"} for item in investigations):
        return True
    serialized = json.dumps(evidence, ensure_ascii=False).casefold()
    return any(
        marker in serialized
        for marker in ("conflict", "does not establish", "冲突", "证据不足")
    )
