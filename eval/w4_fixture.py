"""Offline W4 fixture comparison; no database, model, or remote MCP calls."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from threading import Event, Lock
from time import monotonic, perf_counter_ns
from typing import Any, Callable
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session

from backend.agent.analyzer import IncidentAnalyzer
from backend.agent.evidence_verifier import EvidenceVerifier
from backend.agent.planner import Planner
from backend.agent.risk import RiskChecker
from backend.agent.router import IntentRouter
from backend.mcp.client import MCPRequestTimeoutError, MCPTransientConnectionError
from backend.models.enums import RiskLevel
from backend.services import multi_agent_service as multi
from backend.services.multi_agent_runtime import (
    RUN_DEADLINE_SECONDS, TASK_DEADLINE_SECONDS, ToolBudget,
    _next_tool, _tool_inputs, records_match_scope,
)
from backend.tools import create_default_registry
from backend.tools.base import ToolContext
from backend.tools.registry import ToolExecutionError
from eval.phase22_ab import _CrossServiceFixture


ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "eval" / "w4_cases.json"
HISTORIC_PATH = ROOT / "eval" / "results" / "phase22-final-closeout-2026-09-25.json"
VARIANTS = ("enhanced_single_equal", "multi")
ABLATIONS = {
    "serial": multi.MultiAgentOptions(max_workers=1),
    "no_verifier": multi.MultiAgentOptions(enable_verifier=False),
    "no_followup": multi.MultiAgentOptions(enable_followup=False),
    "no_retry": multi.MultiAgentOptions(enable_retry=False),
}


class MemoryTrace:
    def __init__(self, session: Session, run_id: int, progress_sink: Any = None):
        self.events: list[dict[str, Any]] = []

    def record_multi_event(self, **event: Any) -> None:
        self.events.append(event)

    def persist(self, session: Session) -> None:
        pass


class FixtureClient:
    def __init__(self, case: dict[str, Any]):
        self.case = case
        self.base = _CrossServiceFixture(conflict=case.get("conflict", False))
        self.calls: list[dict[str, Any]] = []
        self.counts: dict[tuple[str, str], int] = defaultdict(int)
        self.lock = Lock()

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        service = str(arguments.get("service_name", "order-service"))
        key = (name, service)
        with self.lock:
            self.counts[key] += 1
            attempt = self.counts[key]
            self.calls.append({"tool": name, "inputs": arguments, "attempt": attempt})
        fault = self.case.get("fault")
        if service == "order-service" and name == "query_error_logs":
            if fault == "transient_once" and attempt == 1:
                raise MCPTransientConnectionError("fixture connection reset")
            if fault == "transient_persistent":
                raise MCPTransientConnectionError("fixture connection reset")
            if fault == "outcome_unknown":
                raise MCPRequestTimeoutError("fixture unknown outcome")
        if service == "order-service" and name == "search_tickets" and fault == "ticket_failure":
            raise RuntimeError("fixture ticket failure")
        if service == "order-service" and name == "search_knowledge" and fault == "knowledge_failure":
            raise RuntimeError("fixture knowledge failure")
        if name in self.case.get("empty_tools", []):
            return {"items": [], "total": 0}
        if name == "query_error_logs" and service == "order-service" and self.case.get("empty_initial_logs"):
            if arguments.get("minutes") == 60 or self.case.get("empty_wide_logs"):
                return {"items": [], "total": 0}
        output = self.base.call_tool(name, arguments)
        if fault == "wrong_service_record" and service == "order-service" and name == "query_error_logs":
            output["items"][0]["service_name"] = "inventory-service"
        return output


def _session_factory() -> Session:
    session = MagicMock(spec=Session)
    session.__enter__.return_value = session
    return session


def _registry(case: dict[str, Any], fixture: FixtureClient):
    registry = create_default_registry(mcp_client=fixture)
    if case.get("fault") == "non_low_tool":
        definition = registry.get_tool("query_error_logs")
        registry._tools["query_error_logs"] = replace(definition, risk_level=RiskLevel.HIGH)
    return registry


def _review(analysis: Any, evidence: list[dict[str, Any]], fault: str | None) -> dict[str, Any]:
    analysis.confirmed_facts = multi._candidate_facts(evidence)
    if fault == "overreach_claim":
        analysis.confirmed_facts.append("超时日志证明服务发生了内存泄漏。")
    if len({item["service"] for item in evidence
            if item["tool"] == "query_error_logs" and item["output"].get("items")}) >= 2:
        analysis.hypotheses = [
            "多个服务的错误可能相关，需核对同一请求的调用链；现有记录未证明共同根因。"
        ]
    if fault == "verifier_timeout":
        return {"status": "timeout", "claims": [], "error": "VERIFIER_TIMEOUT"}
    if fault == "verifier_failure":
        return {"status": "failed", "claims": [], "error": "VERIFIER_FAILED"}
    report = EvidenceVerifier().verify(analysis, tuple(evidence)).model_dump(mode="json")
    if fault == "verifier_false_reject":
        for claim in report["claims"]:
            if claim["support"] == "supported":
                claim["support"] = "unsupported"
                claim["reason"] = "fixture injected false rejection"
                break
    return report


def _run_enhanced_single(
    case: dict[str, Any], run_id: int, fixture: FixtureClient,
    *, session_factory: Callable[[], Session] | None = None,
) -> dict[str, Any]:
    """One decision state visits service scopes in order under the C tool policy."""
    registry = _registry(case, fixture)
    services = case["services"]
    query = case.get("query") or "调查 " + "、".join(services) + " 的故障"
    routing = IntentRouter(None).route(query)
    candidate = [
        {"tool": tool, "inputs": _tool_inputs(tool, service, False)}
        for service in services for tool in
        ("query_error_logs", "search_tickets", "search_knowledge")
    ]
    assessment = RiskChecker().assess(routing.intent, candidate, registry)
    if assessment.requires_approval or any(
        registry.get_tool(step["tool"]).risk_level != RiskLevel.LOW for step in candidate
    ):
        return {"status": "blocked", "intent": routing.intent,
                "evidence": [], "tool_budget": {"used": 0, "limit": 9},
                "verifier": {"status": "skipped", "claims": [], "error": "RISK_BLOCKED"},
                "service_status": {}, "errors": ["RISK_BLOCKED"], "followup_count": 0,
                "retry_count": 0}
    state: dict[str, Any] = {
        "service_cursor": 0, "step": 0, "evidence": [], "errors": [],
        "local_used": defaultdict(int), "gaps": [], "followup_count": 0,
        "retry_count": 0, "service_status": {},
    }
    budget = ToolBudget(9)
    deadline = monotonic() + RUN_DEADLINE_SECONDS
    with (session_factory or _session_factory)() as session:
        for service in services:
            task_deadline = min(deadline, monotonic() + TASK_DEADLINE_SECONDS)
            state["service_cursor"] += 1
            next_tool: str | None = "query_error_logs"
            while next_tool and state["local_used"][service] < 3 and monotonic() < task_deadline:
                tool = next_tool
                inputs = _tool_inputs(tool, service, False)
                state["step"] += 1
                step = state["step"]
                attempt = 0
                failed = False
                total: int | None = None
                while state["local_used"][service] < 3 and budget.claim():
                    state["local_used"][service] += 1
                    attempt += 1
                    try:
                        output = registry.call_tool(tool, inputs, ToolContext(session, run_id)).model_dump(mode="json")
                    except ToolExecutionError as exc:
                        code = getattr(exc.__cause__, "code", exc.code)
                        if isinstance(exc.__cause__, MCPTransientConnectionError) and attempt == 1 and state["local_used"][service] < 3:
                            state["retry_count"] += 1
                            continue
                        state["errors"].append({"service": service, "tool": tool, "code": code})
                        failed = True
                        break
                    total = output.get("total")
                    if not records_match_scope(tool, service, output):
                        state["errors"].append({"service": service, "tool": tool,
                                                "code": "SERVICE_SCOPE_MISMATCH"})
                        failed = True
                        total = None
                        break
                    state["evidence"].append({
                        "step": len(state["evidence"]) + 1,
                        "evidence_id": f"{run_id}:single:step-{step}:{attempt}",
                        "agent_id": "enhanced-single", "task_id": "single",
                        "service": service, "tool": tool, "inputs": inputs,
                        "attempt": attempt,
                        "result_status": "empty_success" if total == 0 else "success_with_data",
                        "output": output,
                    })
                    if tool == "query_error_logs" and total == 0:
                        state["gaps"].append(service)
                    break
                if attempt == 0:
                    state["errors"].append({"service": service, "tool": tool,
                                            "code": "BUDGET_EXHAUSTED"})
                    break
                next_tool = _next_tool(tool, failed=failed, total=total)
            state["service_status"][service] = (
                "partial" if next_tool or any(error["service"] == service for error in state["errors"])
                else "completed"
            )
        for service in state["gaps"]:
            if state["local_used"][service] >= 3 or budget.used >= budget.limit or monotonic() >= deadline:
                continue
            inputs = _tool_inputs("query_error_logs", service, True)
            if not budget.claim():
                break
            state["local_used"][service] += 1
            state["followup_count"] += 1
            try:
                output = registry.call_tool("query_error_logs", inputs, ToolContext(session, run_id)).model_dump(mode="json")
            except ToolExecutionError as exc:
                state["errors"].append({"service": service, "tool": "query_error_logs",
                                        "code": getattr(exc.__cause__, "code", exc.code)})
            else:
                if not records_match_scope("query_error_logs", service, output):
                    state["errors"].append({"service": service, "tool": "query_error_logs",
                                            "code": "SERVICE_SCOPE_MISMATCH"})
                    break
                state["evidence"].append({
                    "step": len(state["evidence"]) + 1,
                    "evidence_id": f"{run_id}:single:followup:1",
                    "agent_id": "enhanced-single", "task_id": "single",
                    "service": service, "tool": "query_error_logs", "inputs": inputs,
                    "attempt": 1,
                    "result_status": "empty_success" if output.get("total") == 0 else "success_with_data",
                    "output": output,
                })
            break
    investigations = [
        {"service": service, "status": state["service_status"][service],
         "evidence": [item for item in state["evidence"] if item["service"] == service],
         "citations": [item["step"] for item in state["evidence"] if item["service"] == service],
         "error": None}
        for service in services
    ]
    analysis = IncidentAnalyzer(None).analyze(query, state["evidence"], investigations).analysis
    verifier = _review(analysis, state["evidence"], case.get("fault"))
    return {"status": "completed" if state["evidence"] else "failed",
            "intent": routing.intent,
            "evidence": state["evidence"], "tool_budget": {"used": budget.used, "limit": budget.limit},
            "verifier": verifier, "service_status": state["service_status"],
            "analysis": analysis.model_dump(mode="json"),
            "errors": state["errors"], "followup_count": state["followup_count"],
            "retry_count": state["retry_count"]}


def _run_multi(case: dict[str, Any], run_id: int, fixture: FixtureClient,
               options: multi.MultiAgentOptions | None = None) -> dict[str, Any]:
    registry = _registry(case, fixture)
    query = case.get("query") or "调查 " + "、".join(case["services"]) + " 的故障"
    routing, planned = multi.prepare_multi_scope(query, IntentRouter(None), Planner(None))
    parent = _session_factory()
    parent.get.return_value = type("Run", (), {})()
    trace_holder: dict[str, MemoryTrace] = {}
    def trace_factory(session: Session, trace_run_id: int, progress_sink: Any = None) -> MemoryTrace:
        recorder = MemoryTrace(session, trace_run_id, progress_sink)
        trace_holder["recorder"] = recorder
        return recorder
    with patch.object(multi, "TraceRecorder", trace_factory):
        try:
            result = multi.execute_multi_agent_run(
                parent, registry, RiskChecker(), run_id, f"fixture-{run_id}",
                query, routing, planned, session_factory=_session_factory,
                options=options,
            )
        except Exception as exc:
            return {"status": "blocked", "intent": routing.intent,
                    "evidence": [], "tool_budget": {"used": len(fixture.calls), "limit": 9},
                    "verifier": {"status": "skipped", "claims": [], "error": type(exc).__name__},
                    "service_status": {}, "errors": [type(exc).__name__],
                    "followup_count": 0, "retry_count": 0,
                    "trace_events": trace_holder.get("recorder", MemoryTrace(parent, run_id)).events}
    payload = result.model_dump(mode="json")
    return {"status": payload["status"], "intent": payload["intent"],
            "evidence": payload["evidence"],
            "tool_budget": payload["tool_budget"], "verifier": payload["verifier"],
            "service_status": {item["service"]: item["status"] for item in payload["service_investigations"]},
            "errors": [error for task in payload["agent_tasks"] for error in task["errors"]],
            "followup_count": sum(task["task_id"].startswith("followup") for task in payload["agent_tasks"]),
            "retry_count": sum(
                len(task["attempts"]) - len({attempt["step_id"] for attempt in task["attempts"]})
                for task in payload["agent_tasks"]
            ),
            "trace_events": trace_holder["recorder"].events,
            "agent_tasks": payload["agent_tasks"]}


def _metrics(case: dict[str, Any], result: dict[str, Any], calls: list[dict[str, Any]],
             latency_ms: float) -> dict[str, Any]:
    evidence = result["evidence"]
    log_services = {
        item["service"] for item in evidence
        if item["tool"] == "query_error_logs" and any(
            isinstance(row, dict) and row.get("service_name") == item["service"]
            for row in item["output"].get("items", [])
        )
    }
    wrong_scope = [
        item["evidence_id"] for item in evidence
        if item["tool"] in {"query_error_logs", "search_tickets"}
        and any(isinstance(row, dict) and row.get("service_name") != item["service"]
                for row in item["output"].get("items", []))
    ]
    claims = result["verifier"].get("claims", [])
    return {
        "tool_attempts": result["tool_budget"]["used"],
        "tool_limit": result["tool_budget"]["limit"],
        "model_calls": 0, "tokens": 0,
        "intent_correct": result.get("intent") == case.get("expected_intent", "incident_analysis"),
        "fixture_latency_ms": round(latency_ms, 3),
        "log_service_coverage": len(log_services) / len(case["services"]),
        "supported_claims": sum(claim["support"] == "supported" for claim in claims),
        "unsupported_claims": sum(claim["support"] == "unsupported" for claim in claims),
        "injected_false_rejections": sum(
            claim["reason"] == "fixture injected false rejection" for claim in claims
        ),
        "hypotheses_unverified": sum(claim["claim_type"] == "hypothesis" and claim["support"] == "unverified" for claim in claims),
        "wrong_scope_evidence_ids": wrong_scope,
        "budget_ok": result["tool_budget"]["used"] <= 9,
        "read_only_ok": all(call["tool"] in {"query_error_logs", "search_tickets", "search_knowledge"} for call in calls),
        "retry_count": result["retry_count"],
        "followup_count": result["followup_count"],
        "verifier_status": result["verifier"]["status"],
        "human_claim_review": "pending",
    }


def run_one(case: dict[str, Any], variant: str, run_id: int,
            options: multi.MultiAgentOptions | None = None) -> dict[str, Any]:
    fixture = FixtureClient(case)
    started = perf_counter_ns()
    fault = case.get("fault")
    if fault in {"verifier_timeout", "verifier_failure"} and variant != "enhanced_single_equal":
        def injected(*args: Any, **kwargs: Any) -> Any:
            if fault == "verifier_timeout":
                raise TimeoutError("fixture")
            raise RuntimeError("fixture")
        with patch.object(EvidenceVerifier, "verify", injected):
            result = _run_multi(case, run_id, fixture, options)
    elif fault == "overreach_claim" and variant != "enhanced_single_equal":
        original = multi._candidate_facts
        def inject_overreach(evidence: list[dict[str, Any]]) -> list[str]:
            return [*original(evidence), "超时日志证明服务发生了内存泄漏。"]
        with patch.object(multi, "_candidate_facts", inject_overreach):
            result = _run_multi(case, run_id, fixture, options)
    elif fault == "verifier_false_reject" and variant != "enhanced_single_equal":
        original_verify = EvidenceVerifier.verify
        def inject_false_reject(self: EvidenceVerifier, analysis: Any, evidence: Any) -> Any:
            report = original_verify(self, analysis, evidence)
            for claim in report.claims:
                if claim.support == "supported":
                    claim.support = "unsupported"
                    claim.reason = "fixture injected false rejection"
                    break
            return report
        with patch.object(EvidenceVerifier, "verify", inject_false_reject):
            result = _run_multi(case, run_id, fixture, options)
    elif variant == "enhanced_single_equal":
        result = _run_enhanced_single(case, run_id, fixture)
    else:
        result = _run_multi(case, run_id, fixture, options)
    latency_ms = (perf_counter_ns() - started) / 1_000_000
    gold = {
        "required_services": case["services"],
        "expected_intent": case.get("expected_intent", "incident_analysis"),
        "allowed": ["exact tool record facts", "explicitly unverified hypotheses"],
        "forbidden": ["unsupported root cause as fact", "failed call as evidence",
                      "other service records as this service evidence"],
    }
    return {"case_id": case["id"], "variant": variant, "scenario": case,
            "gold": gold, "result": result,
            "fixture_calls": fixture.calls,
            "metrics": _metrics(case, result, fixture.calls, latency_ms)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run W4 offline, equal-budget fixture comparisons.")
    parser.add_argument("--case-id")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output-root", type=Path, default=ROOT / "eval" / "results" / "w4_fixture")
    args = parser.parse_args()
    if not 1 <= args.repeat <= 3:
        parser.error("--repeat must be between 1 and 3")
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    if args.case_id:
        cases = [case for case in cases if case["id"] == args.case_id]
        if not cases:
            parser.error("unknown case-id")
    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output_dir = args.output_root / run_stamp
    output_dir.mkdir(parents=True, exist_ok=False)
    provenance = {
        "suite": "w4_fixture_v1", "git_sha": git_sha, "working_tree_dirty": dirty,
        "cases_sha256": sha256(CASES_PATH.read_bytes()).hexdigest(),
        "source_sha256": sha256(b"".join(
            (ROOT / name).read_bytes() for name in (
                "eval/w4_fixture.py", "backend/services/multi_agent_service.py",
                "backend/services/multi_agent_runtime.py", "backend/agent/evidence_verifier.py",
                "backend/agent/router.py", "backend/agent/analyzer.py",
                "backend/tools/mcp_proxy.py", "eval/phase22_ab.py",
            )
        )).hexdigest(),
        "historic_a": str(HISTORIC_PATH.relative_to(ROOT)),
        "comparison_rule": "A historical 5-step Single is separate; B/C use 9 tool attempts and 0 model calls.",
        "fixture_only": True, "mcp_transport": "direct_fixture_not_production_queue",
        "latency_inference_allowed": False, "human_review": "pending",
    }
    raw: list[dict[str, Any]] = []
    run_id = 10_000
    for repeat in range(args.repeat):
        for case in cases:
            for variant in VARIANTS:
                run_id += 1
                item = run_one(case, variant, run_id)
                item["repeat"] = repeat + 1
                raw.append(item)
    for index in range(0, len(raw), 2):
        left, right = raw[index:index + 2]
        if left["case_id"] != right["case_id"] or left["repeat"] != right["repeat"]:
            raise RuntimeError("Paired fixture runs were misaligned.")
        for key in ("tool_limit", "model_calls", "tokens"):
            if left["metrics"][key] != right["metrics"][key]:
                raise RuntimeError(f"Unequal B/C budget: {key}.")
    ablations: list[dict[str, Any]] = []
    for case in cases:
        if case["id"] not in {"normal_two", "followup_gain", "transient_once", "ticket_failure"}:
            continue
        for name, options in ABLATIONS.items():
            run_id += 1
            item = run_one(case, f"multi_{name}", run_id, options)
            ablations.append(item)
    historic = json.loads(HISTORIC_PATH.read_text(encoding="utf-8"))
    summary = {"provenance": provenance,
               "historic_a": historic["ab_evaluation"]["aggregate"],
               "paired_runs": len(raw) // 2, "cases": len(cases),
               "variants": {}, "ablation_runs": len(ablations),
               "ablation_deltas": [],
               "human_review_queue": [
                   {"case_id": case["id"], "status": "pending",
                    "question": "逐条核对支持状态、服务归属和是否越界推断",
                    "claims": [
                        {"text": claim["text"], "type": claim["claim_type"],
                         "support": claim["support"], "evidence_ids": claim["evidence_ids"]}
                        for item in raw
                        if item["case_id"] == case["id"] and item["variant"] == "multi"
                        and item["repeat"] == 1
                        for claim in item["result"]["verifier"].get("claims", [])
                    ],
                    "records": [
                        {"evidence_id": evidence["evidence_id"],
                         "service": evidence["service"], "tool": evidence["tool"],
                         "items": evidence["output"].get("items", [])[:2]}
                        for item in raw
                        if item["case_id"] == case["id"] and item["variant"] == "multi"
                        and item["repeat"] == 1
                        for evidence in item["result"]["evidence"]
                    ]}
                   for case in cases
               ],
               "interpretation": "Fixture metrics check mechanics only; business success and causal support require human review."}
    full_multi = {item["case_id"]: item for item in raw
                  if item["variant"] == "multi" and item["repeat"] == 1}
    for item in ablations:
        full = full_multi[item["case_id"]]
        summary["ablation_deltas"].append({
            "case_id": item["case_id"], "ablation": item["variant"],
            "tool_attempt_delta": item["metrics"]["tool_attempts"] - full["metrics"]["tool_attempts"],
            "log_coverage_delta": item["metrics"]["log_service_coverage"] - full["metrics"]["log_service_coverage"],
            "verifier_status": item["metrics"]["verifier_status"],
            "followup_delta": item["metrics"]["followup_count"] - full["metrics"]["followup_count"],
            "retry_delta": item["metrics"]["retry_count"] - full["metrics"]["retry_count"],
        })
    for variant in VARIANTS:
        items = [item for item in raw if item["variant"] == variant]
        summary["variants"][variant] = {
            "runs": len(items),
            "budget_violations": sum(not item["metrics"]["budget_ok"] for item in items),
            "read_only_violations": sum(not item["metrics"]["read_only_ok"] for item in items),
            "scope_violations": sum(bool(item["metrics"]["wrong_scope_evidence_ids"]) for item in items),
            "intent_correct": sum(item["metrics"]["intent_correct"] for item in items),
            "mean_log_service_coverage": sum(item["metrics"]["log_service_coverage"] for item in items) / len(items),
            "mean_tool_attempts": sum(item["metrics"]["tool_attempts"] for item in items) / len(items),
            "supported_claims": sum(item["metrics"]["supported_claims"] for item in items),
            "unsupported_claims": sum(item["metrics"]["unsupported_claims"] for item in items),
            "injected_false_rejections": sum(
                item["metrics"]["injected_false_rejections"] for item in items
            ),
            "model_calls": 0, "tokens": 0,
        }
    summary["paired_differences"] = {
        "log_coverage_nonzero": sum(
            raw[index]["metrics"]["log_service_coverage"] != raw[index + 1]["metrics"]["log_service_coverage"]
            for index in range(0, len(raw), 2)
        ),
        "tool_attempt_nonzero": sum(
            raw[index]["metrics"]["tool_attempts"] != raw[index + 1]["metrics"]["tool_attempts"]
            for index in range(0, len(raw), 2)
        ),
    }
    (output_dir / "raw.json").write_text(json.dumps({"provenance": provenance, "runs": raw,
                                                      "ablations": ablations}, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "variants": summary["variants"],
                      "paired_runs": summary["paired_runs"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
