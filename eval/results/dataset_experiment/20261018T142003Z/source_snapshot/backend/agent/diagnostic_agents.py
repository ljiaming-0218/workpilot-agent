"""Independent service decisions and semantic review through the existing LLM gateway."""

from copy import deepcopy
import json
import re
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from backend.schemas.agent import (
    AgentTaskResult, DiagnosticClaim, InvestigatorDecision, SemanticReview,
    VerifiedClaim, VerifierReport,
)
from backend.services.llm_service import LLMServiceError, StructuredLLM
from backend.utils.trace_context import bind_trace_sink


PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
INVESTIGATOR_PROMPT = (PROMPTS / "service_investigator.txt").read_text(encoding="utf-8")
VERIFIER_PROMPT = (PROMPTS / "semantic_verifier.txt").read_text(encoding="utf-8")
NUMERIC_BOUNDS = (
    re.compile(r"(?:不低于|至少|大于等于|>=|at least|minimum(?: of)?)\s*(\d+(?:\.\d+)?)", re.I),
    re.compile(r"(?:不高于|至多|不超过|<=|at most|maximum(?: of)?)\s*(\d+(?:\.\d+)?)", re.I),
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:或更高|或更多|及以上|or higher|or more|or above|and above)", re.I),
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:或更低|及以下|or lower|or less|or below)", re.I),
)


def review_claim(claim: DiagnosticClaim, verdict, ledger: dict[str, Any]) -> VerifiedClaim:
    support, reason = verdict.support, verdict.reason
    if not set(claim.evidence_ids) <= ledger.keys():
        support, reason = "unsupported", "引用证据不存在。"
    elif support == "supported":
        cited = json.dumps([ledger[key]["output"] for key in claim.evidence_ids], ensure_ascii=False)
        for pattern in NUMERIC_BOUNDS:
            if set(pattern.findall(claim.text)) - set(pattern.findall(cited)):
                support, reason = "unsupported", "引用记录没有支持该数值上下限；已验证的单个工作值不能推出阈值。"
                break
    return VerifiedClaim(claim_id=claim.claim_id, claim_type=claim.claim_type,
                         text=claim.text, evidence_ids=claim.evidence_ids,
                         support=support, reason=reason)


def _snapshot(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Preserve provenance and bound only record text/list sizes before model input.
    def compact(value):
        if isinstance(value, str):
            return value[:2000]
        if isinstance(value, list):
            return [compact(item) for item in value[:20]]
        if isinstance(value, dict):
            return {key: compact(item) for key, item in value.items()}
        return value
    return compact(deepcopy(evidence))


class _ModelTrace:
    def __init__(self, events, node, agent_id, task_id):
        self.events, self.node, self.agent_id, self.task_id = events, node, agent_id, task_id
        events.append({"node": node, "event_type": "llm_started",
                       "input_json": {"agent_id": agent_id, "task_id": task_id},
                       "occurred_ns": perf_counter_ns()})

    def record_llm(self, *, model, response_schema, latency_ms, status, error, tokens):
        self.events.append({
            "node": self.node, "event_type": "llm_call", "model": model,
            "latency_ms": latency_ms, "tokens_json": tokens,
            "input_json": {"agent_id": self.agent_id, "task_id": self.task_id,
                           "response_schema": response_schema},
            "error": error if status != "success" else None, "occurred_ns": perf_counter_ns(),
        })


class DiagnosticAgents:
    """Roles share a stateless gateway, never a writable conversation or task state."""

    def __init__(self, llm: StructuredLLM):
        self.llm = llm

    def decide(self, query: str, task: AgentTaskResult, evidence: list[dict[str, Any]],
               remaining: int, allowed: list[str], model_budget, events) -> InvestigatorDecision:
        if not model_budget.claim():
            raise LLMServiceError("Diagnostic model budget exhausted.")
        payload = {
            "query": query, "service": task.service, "task_id": task.task_id,
            "observations": _snapshot(evidence), "failed_attempts": task.errors,
            "available_evidence_ids": [item["evidence_id"] for item in evidence],
            "remaining_tool_attempts": remaining,
            "allowed_next_tools": [*allowed, "stop"] if remaining > 0 else ["stop"],
        }
        sink = _ModelTrace(events, "service_investigator", task.agent_id, task.task_id)
        with bind_trace_sink(sink):
            decision = self.llm.generate_structured(
                INVESTIGATOR_PROMPT, json.dumps(payload, ensure_ascii=False), InvestigatorDecision,
            )
        known = {item["evidence_id"] for item in evidence}
        for index, claim in enumerate(decision.claims, start=1):
            if not set(claim.evidence_ids) <= known:
                raise LLMServiceError("Investigator cited evidence outside its task.")
            claim.claim_id = f"{task.agent_id}:{task.task_id}:claim-{index}"
        if decision.next_tool != "stop" and (remaining <= 0 or decision.next_tool not in allowed):
            raise LLMServiceError("Investigator selected an unavailable next tool.")
        return decision

    def verify(self, tasks: list[AgentTaskResult], evidence: list[dict[str, Any]],
               model_budget, events) -> VerifierReport:
        latest = {task.service: task for task in tasks if task.findings}
        proposed: list[DiagnosticClaim] = [claim for task in latest.values() for claim in task.findings]
        if not proposed:
            return VerifierReport(status="failed", error="NO_DIAGNOSTIC_CLAIMS")
        if not model_budget.claim():
            return VerifierReport(status="failed", error="MODEL_BUDGET_EXHAUSTED")
        ledger = {item["evidence_id"]: item for item in evidence}
        used_ids = {key for claim in proposed for key in claim.evidence_ids if key in ledger}
        payload = {"evidence_ledger": _snapshot([item for key, item in ledger.items() if key in used_ids]),
            "claims": [
            {**claim.model_dump(), "record_identity_checks": [
                {"evidence_id": key, "record_id": record.get("id"),
                 "title": record["title"],
                 "exact_record_title_present_in_claim": record["title"] in claim.text}
                for key in claim.evidence_ids if key in ledger
                for record in ledger[key]["output"].get("items", [])
                if isinstance(record, dict) and isinstance(record.get("title"), str)
            ]} for claim in proposed
        ]}
        sink = _ModelTrace(events, "semantic_verifier", "semantic-verifier", "review-1")
        with bind_trace_sink(sink):
            review = self.llm.generate_structured(
                VERIFIER_PROMPT, json.dumps(payload, ensure_ascii=False), SemanticReview,
            )
        by_id = {claim.claim_id: claim for claim in proposed}
        reviewed = {item.claim_id: item for item in review.claims}
        if len(reviewed) != len(review.claims) or set(reviewed) != set(by_id):
            raise LLMServiceError("Verifier did not review the proposed claim IDs exactly once.")
        return VerifierReport(status="completed", claims=[
            review_claim(claim, reviewed[claim.claim_id], ledger) for claim in proposed
        ])
