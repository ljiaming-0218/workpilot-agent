"""Auditable Phase 22 A/B runs for baseline and enhanced Single Agent."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import select

from backend.main import create_app
from backend.models.enums import ToolCallStatus
from backend.models.tool_call import ToolCallLog
from backend.schemas.common import SuccessResponse
from backend.tools.base import BaseTool, ToolContext, ToolDefinition
from backend.tools.mcp_proxy import create_mcp_proxy_tools
from backend.tools.registry import ToolRegistry
from eval.cases import RepresentativeAgentCase, load_case_list


EVAL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EVAL_DIR.parent
BASELINE_SHA = "00227767277306b9831f233423262442425dd732"
FIXTURE_VERSION = "cross-service-fixture-v1"
GOLD_FILE = EVAL_DIR / "representative_cases.json"
CASE_IDS = (
    "cross-service-normal",
    "cross-service-conflict",
    "cross-service-tool-failure",
)
SAFE_TOOLS = {"query_error_logs", "search_tickets", "search_knowledge", "query_database"}
FINGERPRINT_FILES = (
    "backend/agent/planner.py",
    "backend/agent/nodes.py",
    "backend/agent/analyzer.py",
    "backend/agent/state.py",
    "backend/schemas/agent.py",
    "backend/services/agent_service.py",
    "eval/cases.py",
    "eval/representative_cases.json",
    "eval/phase22_ab.py",
)


class _CrossServiceFixture:
    _SIGNALS = {
        "order-service": (81001, "ORDER_TIMEOUT", "Order timed out while waiting for inventory reservation."),
        "inventory-service": (82001, "504", "Inventory returned HTTP 504 from warehouse gateway."),
        "payment-service": (83001, "DB_POOL_EXHAUSTED", "Payment database connection pool checkout timed out."),
    }

    def __init__(self, *, conflict: bool = False, fail_tool: str | None = None) -> None:
        self.conflict = conflict
        self.fail_tool = fail_tool

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        service = str(arguments.get("service_name", "order-service"))
        if name == self.fail_tool and service == "order-service":
            raise RuntimeError("Injected Phase 22 tool failure.")
        if name == "query_error_logs":
            record_id, error_type, message = self._SIGNALS.get(
                service, (89999, "UNKNOWN", "No matching fixture signal."),
            )
            return {"items": [{
                "id": record_id,
                "service_name": service,
                "level": "ERROR",
                "error_type": error_type,
                "message": message,
                "stack_trace": None,
                "request_id": f"phase22-{service}",
                "created_at": "2026-09-22T02:30:00Z",
            }], "total": 1}
        if name == "search_tickets":
            note = (
                "Health checks recovered; this ticket does not establish a shared root cause."
                if self.conflict else "Ticket matches the fixed incident window."
            )
            return {"items": [{
                "id": self._SIGNALS.get(service, (89999, "", ""))[0] + 100,
                "title": f"Phase 22 incident for {service}",
                "content": note,
                "category": "incident",
                "priority": "high",
                "status": "open",
                "service_name": service,
                "resolution": None,
                "created_at": "2026-09-22T02:32:00Z",
                "updated_at": "2026-09-22T02:35:00Z",
            }], "total": 1}
        if name == "search_knowledge":
            excerpt = (
                "Signals conflict; temporal proximity does not prove causality."
                if self.conflict else
                "Correlate order timeout, inventory 504, and payment pool exhaustion by trace and time."
            )
            return {"items": [{
                "document_id": 84001,
                "title": "Phase 22 cross-service incident runbook",
                "excerpt": excerpt,
                "category": "runbook",
                "source": "eval://phase22/cross-service",
                "score": 10.0,
            }], "total": 1}
        raise RuntimeError(f"Unexpected Phase 22 fixture tool: {name}")


class _DefinitionTool(BaseTool[BaseModel, BaseModel]):
    def __init__(self, definition: ToolDefinition) -> None:
        self.name = definition.name
        self.description = definition.description
        self.input_schema = definition.input_schema
        self.output_schema = definition.output_schema
        self.risk_level = definition.risk_level
        self.enabled = definition.enabled
        self.handler = definition.handler

    def execute(self, context: ToolContext, tool_input: BaseModel) -> BaseModel:
        return self.handler(context, tool_input)


def _fixture_registry(original: ToolRegistry, fixture: _CrossServiceFixture) -> ToolRegistry:
    replacements = {tool.name: tool for tool in create_mcp_proxy_tools(fixture)}
    return ToolRegistry(
        replacements.get(definition.name, _DefinitionTool(definition))
        for definition in original.list_tools()
    )


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=PROJECT_ROOT, text=True, stderr=subprocess.DEVNULL,
    ).strip()


def _source_fingerprint() -> str:
    digest = hashlib.sha256()
    for relative in FINGERPRINT_FILES:
        path = PROJECT_ROOT / relative
        digest.update(relative.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _case_gold_hash(case: RepresentativeAgentCase) -> str:
    gold = {
        "required_evidence": [item.model_dump(mode="json") for item in case.required_evidence],
        "required_citation_services": case.required_citation_services,
        "required_analysis_terms_any": case.required_analysis_terms_any,
    }
    return hashlib.sha256(
        json.dumps(gold, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _safe_config(settings: Any) -> dict[str, Any]:
    host = urlparse(settings.llm_base_url or "").hostname
    return {
        "llm_model": settings.llm_model,
        "llm_host": host,
        "llm_timeout_seconds": settings.llm_timeout,
        "llm_max_retries": settings.llm_max_retries,
        "llm_thinking_enabled": settings.llm_enable_thinking,
        "llm_api_key_configured": settings.llm_api_key is not None,
        "database_host": settings.mysql_host,
        "database_port": settings.mysql_port,
        "database_name": settings.mysql_database,
    }


def _matches_gold(item: dict[str, Any], gold: Any) -> bool:
    serialized = json.dumps(item, ensure_ascii=False).casefold()
    return (
        item.get("tool") == gold.tool
        and (gold.service_name is None or gold.service_name.casefold() in serialized)
        and all(marker.casefold() in serialized for marker in gold.contains)
    )


def _run_case(
    client: TestClient,
    case: RepresentativeAgentCase,
    variant: str,
) -> dict[str, Any]:
    fixture = _CrossServiceFixture(
        conflict=case.id == "cross-service-conflict",
        fail_tool="search_tickets" if case.id == "cross-service-tool-failure" else None,
    )
    client.app.state.tool_registry = _fixture_registry(
        client.app.state.tool_registry,
        fixture,
    )
    started = perf_counter()
    response = client.post("/agent/runs", json={"query": case.query})
    latency_ms = max(0, int((perf_counter() - started) * 1000))
    raw_response = response.json()
    result = raw_response.get("data") if response.status_code == 200 else None
    run_id = result.get("run_id") if isinstance(result, dict) else None
    trace_events: list[dict[str, Any]] = []
    call_logs: list[ToolCallLog] = []
    if run_id is not None:
        trace_response = client.get(f"/agent/runs/{run_id}/trace")
        if trace_response.status_code == 200:
            trace_events = trace_response.json().get("data", {}).get("events", [])
        with client.app.state.session_factory() as session:
            call_logs = list(session.scalars(
                select(ToolCallLog).where(ToolCallLog.agent_run_id == run_id)
            ))

    evidence = result.get("evidence", []) if isinstance(result, dict) else []
    analysis = result.get("analysis") if isinstance(result, dict) else None
    matches = [
        [item for item in evidence if _matches_gold(item, gold)]
        for gold in case.required_evidence
    ]
    gold_hit_count = sum(bool(items) for items in matches)
    cited_steps = set(analysis.get("evidence_steps", [])) if isinstance(analysis, dict) else set()
    citation_by_service = {
        gold.service_name: any(
            item.get("step") in cited_steps
            for item in matched
        )
        for gold, matched in zip(case.required_evidence, matches)
    }
    required_services = list(case.required_citation_services)
    citation_correct = bool(required_services) and all(
        citation_by_service.get(service, False) for service in required_services
    )
    completed_services = {
        gold.service_name for gold, matched in zip(case.required_evidence, matches)
        if gold.service_name and matched
    }
    failed_calls = [
        log for log in call_logs
        if log.status in {ToolCallStatus.FAILED, ToolCallStatus.REJECTED}
    ]
    fault_expected = case.id == "cross-service-tool-failure"
    degradation_ok = (
        len(completed_services - {"order-service"}) >= 2 and bool(failed_calls)
        if fault_expected else not failed_calls
    )
    plan = result.get("plan", []) if isinstance(result, dict) else []
    sequence = [step.get("tool") for step in plan]
    executed = [item.get("tool") for item in evidence if isinstance(item, dict)]
    risk = result.get("risk_level") if isinstance(result, dict) else None
    requires_approval = result.get("requires_approval") if isinstance(result, dict) else None
    safe_tools = all(log.tool_name in SAFE_TOOLS for log in call_logs)
    safety_ok = safe_tools and risk != "HIGH" and requires_approval is not True
    analysis_text = json.dumps(analysis, ensure_ascii=False).casefold()
    uncertainty_ok = (
        not case.required_analysis_terms_any
        or any(term.casefold() in analysis_text for term in case.required_analysis_terms_any)
    )
    business = {
        "gold_coverage": gold_hit_count == len(case.required_evidence),
        "citation_correctness": citation_correct,
        "cross_service_completion": len(completed_services) == len(required_services),
        "local_failure_degradation": degradation_ok,
        "uncertainty_handling": uncertainty_ok,
        "safety_boundary": safety_ok,
    }
    settings = client.app.state.settings
    config = _safe_config(settings)
    config_hash = hashlib.sha256(
        json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    gold_hash = _case_gold_hash(case)
    fingerprint = _source_fingerprint()
    sha = _git("rev-parse", "HEAD")
    permissions = [
        {"tool": definition.name, "risk": definition.risk_level.value,
         "enabled": definition.enabled}
        for definition in client.app.state.tool_registry.list_tools()
    ]
    permissions_hash = hashlib.sha256(
        json.dumps(permissions, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "metadata": {
            "variant": variant,
            "git_sha": sha,
            "source_fingerprint": fingerprint,
            "working_tree_dirty": bool(_git("status", "--porcelain")),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "case_id": case.id,
            "model_config": config,
            "model_config_sha256": config_hash,
            "max_steps": client.app.state.planner.max_steps,
            "tool_budget": client.app.state.planner.max_steps,
            "fixture_version": FIXTURE_VERSION,
            "gold_sha256": gold_hash,
            "permissions": permissions,
            "permissions_sha256": permissions_hash,
        },
        "business_metrics": {
            **business,
            "business_success": all(business.values()),
            "gold_coverage_count": gold_hit_count,
            "gold_coverage_total": len(case.required_evidence),
            "gold_coverage_rate": gold_hit_count / len(case.required_evidence)
            if case.required_evidence else 1.0,
            "citation_services": citation_by_service,
            "completed_services": sorted(completed_services),
            "service_completion_rate": len(completed_services) / len(required_services)
            if required_services else 1.0,
            "partial_failures": [
                {"tool": log.tool_name, "status": log.status.value}
                for log in failed_calls
            ],
        },
        "implementation_metrics": {
            "intent": result.get("intent") if isinstance(result, dict) else None,
            "expected_intent": case.expected_intent,
            "intent_correct": isinstance(result, dict)
            and result.get("intent") == case.expected_intent,
            "plan_tool_sequence": sequence,
            "executed_tool_sequence": executed,
            "expected_plan_tool_sequence": case.expected_plan_tools,
            "expected_executed_tool_sequence": case.expected_executed_tools,
            "run_status": result.get("status") if isinstance(result, dict) else None,
            "run_error": result.get("error") if isinstance(result, dict) else raw_response.get("error"),
            "http_status": response.status_code,
        },
        "performance": {
            "latency_ms": latency_ms,
            "llm_calls": sum(event.get("event_type") == "llm_call" for event in trace_events),
            "tokens": sum(
                (event.get("tokens_json") or {}).get("total", 0)
                for event in trace_events if event.get("event_type") == "llm_call"
            ),
            "tool_calls": len(call_logs),
        },
        "raw": {
            "response": raw_response,
            "tool_calls": [
                {"tool": log.tool_name, "status": log.status.value}
                for log in call_logs
            ],
        },
    }


def _save_result(root: Path, variant: str, case_id: str, payload: dict[str, Any]) -> Path:
    timestamp = datetime.fromisoformat(payload["metadata"]["timestamp"])
    slug = timestamp.strftime("%Y%m%dT%H%M%S%fZ")
    target_dir = root / variant / case_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{slug}-{payload['metadata']['source_fingerprint'][:8]}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def _compare(root: Path) -> dict[str, Any]:
    comparison: dict[str, Any] = {"suite": "phase22_ab", "cases": {}}
    for case_id in CASE_IDS:
        variants: dict[str, Any] = {}
        for variant in ("baseline", "enhanced"):
            files = sorted((root / variant / case_id).glob("*.json"))
            if not files:
                raise ValueError(f"Missing {variant} result for {case_id}.")
            variants[variant] = [
                json.loads(path.read_text(encoding="utf-8")) for path in files
            ]
        if len(variants["baseline"]) != len(variants["enhanced"]):
            raise ValueError(f"A/B run count differs for {case_id}.")
        for baseline, enhanced in zip(variants["baseline"], variants["enhanced"]):
            for key in ("gold_sha256", "fixture_version", "permissions_sha256"):
                if baseline["metadata"][key] != enhanced["metadata"][key]:
                    raise ValueError(f"A/B mismatch for {case_id}: {key} differs.")
            if baseline["metadata"]["model_config_sha256"] != enhanced["metadata"]["model_config_sha256"]:
                raise ValueError(f"A/B mismatch for {case_id}: model/config differs.")

        def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
            business = [record["business_metrics"] for record in records]
            performance = [record["performance"] for record in records]
            intent = [record["implementation_metrics"]["intent_correct"] for record in records]
            latencies = sorted(item["latency_ms"] for item in performance)
            p95_index = max(0, int(len(latencies) * 0.95 + 0.999999) - 1)
            mean = lambda values: sum(values) / len(values) if values else 0.0
            return {
                "runs": len(records),
                "business_success_rate": mean([float(item["business_success"]) for item in business]),
                "gold_coverage_rate": mean([item["gold_coverage_rate"] for item in business]),
                "citation_correctness_rate": mean([float(item["citation_correctness"]) for item in business]),
                "service_completion_rate": mean([item["service_completion_rate"] for item in business]),
                "degradation_success_rate": mean([float(item["local_failure_degradation"]) for item in business]),
                "safety_boundary_pass_rate": mean([float(item["safety_boundary"]) for item in business]),
                "intent_accuracy_diagnostic": mean([float(value) for value in intent]),
                "average_latency_ms": mean(latencies),
                "p95_latency_ms": latencies[p95_index] if latencies else 0,
                "average_tokens": mean([item["tokens"] for item in performance]),
                "average_tool_calls": mean([item["tool_calls"] for item in performance]),
            }
        baseline, enhanced = variants["baseline"], variants["enhanced"]
        comparison["cases"][case_id] = {
            "baseline": summarize(baseline),
            "enhanced": summarize(enhanced),
        }
    return comparison


def main() -> int:
    parser = argparse.ArgumentParser(description="Run or compare Phase 22 Single Agent A/B cases.")
    parser.add_argument("--variant", choices=("baseline", "enhanced"))
    parser.add_argument("--case-id", choices=CASE_IDS)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output-root", type=Path, default=EVAL_DIR / "results" / "phase22_ab" / "runs")
    parser.add_argument("--compare", action="store_true")
    args = parser.parse_args()
    if args.compare:
        report = _compare(args.output_root)
        report["created_at"] = datetime.now(timezone.utc).isoformat()
        comparison_dir = args.output_root / "comparisons"
        comparison_dir.mkdir(parents=True, exist_ok=True)
        comparison_path = comparison_dir / (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            + ".json"
        )
        comparison_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({"comparison_path": str(comparison_path), "report": report}, ensure_ascii=False, indent=2))
        return 0
    if args.variant is None:
        parser.error("--variant is required unless --compare is used.")
    if not 1 <= args.repeat <= 20:
        parser.error("--repeat must be between 1 and 20.")
    if args.variant == "baseline" and _git("rev-parse", "HEAD") != BASELINE_SHA:
        raise SystemExit(f"Baseline must run at {BASELINE_SHA}.")
    cases = load_case_list(GOLD_FILE, RepresentativeAgentCase)
    selected = [case for case in cases if case.id in CASE_IDS]
    if args.case_id:
        selected = [case for case in selected if case.id == args.case_id]
    if not selected:
        raise SystemExit("No Phase 22 cases were registered.")

    with TestClient(create_app()) as client:
        original_registry = client.app.state.tool_registry
        results = []
        for repeat_index in range(args.repeat):
            for case in selected:
                client.app.state.tool_registry = original_registry
                payload = _run_case(client, case, args.variant)
                path = _save_result(args.output_root, args.variant, case.id, payload)
                results.append({"case_id": case.id, "repeat": repeat_index + 1, "business_success": payload["business_metrics"]["business_success"], "result_path": str(path)})
    print(json.dumps({"variant": args.variant, "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
