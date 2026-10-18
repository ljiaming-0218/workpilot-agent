"""One bounded, read-only review of claims against the evidence ledger."""

import re
from collections.abc import Mapping
from time import monotonic
from typing import Any

from backend.schemas.agent import IncidentAnalysis, VerifiedClaim, VerifierReport


COUNT_FACT = re.compile(r"^步骤 (\d+) 的 (\w+) 返回 (\d+) 条匹配记录。$")
LOG_FACT = re.compile(r"^步骤 (\d+) 的日志 ID (\d+) 记录错误类型 (.+)。$")
TICKET_FACT = re.compile(r"^步骤 (\d+) 的工单 ID (\d+) 状态为 (.+)。$")
MAX_CLAIMS = 15
VERIFIER_DEADLINE_SECONDS = 2


class EvidenceVerifier:
    """Certify only exact tool-count facts; leave causal claims unverified."""

    def verify(
        self,
        analysis: IncidentAnalysis,
        evidence: tuple[Mapping[str, Any], ...],
    ) -> VerifierReport:
        deadline = monotonic() + VERIFIER_DEADLINE_SECONDS
        by_step = {item.get("step"): item for item in evidence}
        claims: list[VerifiedClaim] = []
        for kind, texts in (
            ("fact", analysis.confirmed_facts),
            ("hypothesis", analysis.hypotheses),
        ):
            for text in texts:
                if monotonic() >= deadline:
                    raise TimeoutError("Evidence verification exceeded its deadline.")
                if len(claims) >= MAX_CLAIMS:
                    raise ValueError("Evidence verification exceeded its claim limit.")
                claim_id = f"claim-{len(claims) + 1}"
                if kind == "hypothesis":
                    claims.append(VerifiedClaim(
                        claim_id=claim_id, claim_type="hypothesis", text=text,
                        support="unverified",
                        reason="假设可作为排查方向，当前记录尚未证明因果关系。",
                    ))
                    continue
                count_match = COUNT_FACT.fullmatch(text)
                log_match = LOG_FACT.fullmatch(text)
                ticket_match = TICKET_FACT.fullmatch(text)
                match = count_match or log_match or ticket_match
                if match is None:
                    claims.append(VerifiedClaim(
                        claim_id=claim_id, claim_type="fact", text=text,
                        support="unsupported",
                        reason="无法将该事实逐条对应到工具返回的记录与数量。",
                    ))
                    continue
                step = int(match[1])
                item = by_step.get(step)
                evidence_ids = [item["evidence_id"]] if item and item.get("evidence_id") else []
                output = item.get("output", {}) if item else {}
                valid = False
                if item is not None and item.get("result_status") in {"success_with_data", "empty_success"} and isinstance(output, dict):
                    if count_match:
                        valid = item.get("tool") == match[2] and output.get("total") == int(match[3])
                    elif log_match and item.get("tool") == "query_error_logs":
                        valid = any(
                            isinstance(row, dict) and row.get("id") == int(match[2])
                            and row.get("error_type") == match[3]
                            and row.get("service_name") == item.get("service")
                            for row in output.get("items", [])
                        )
                    elif ticket_match and item.get("tool") == "search_tickets":
                        valid = any(
                            isinstance(row, dict) and row.get("id") == int(match[2])
                            and row.get("status") == match[3]
                            and row.get("service_name") == item.get("service")
                            for row in output.get("items", [])
                        )
                claims.append(VerifiedClaim(
                    claim_id=claim_id, claim_type="fact", text=text,
                    evidence_ids=evidence_ids,
                    support="supported" if valid else "unsupported",
                    reason=(
                        "工具成功返回，该断言与对应记录字段一致。"
                        if valid else "对应步骤缺失、工具失败或断言与记录字段不一致。"
                    ),
                ))
        return VerifierReport(status="completed", claims=claims)
