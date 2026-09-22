"""CLI runner for deterministic component evaluations."""

import argparse
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

import httpx
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from backend.agent.router import IntentRouter, select_tool
from backend.agent.analyzer import IncidentAnalyzer
from backend.main import create_app
from backend.models.approval_checkpoint import AgentApprovalCheckpoint
from backend.retrieval.retriever import BM25Retriever, RetrievalDocument
from backend.schemas.agent import AgentResult
from backend.schemas.common import SuccessResponse
from backend.security.sql_guard import SQLGuard, SQLGuardError
from backend.services.llm_service import LLMServiceError
from backend.services.text2sql_service import Text2SQLService
from backend.tools import create_default_registry
from backend.utils.trace_context import emit_llm_trace
from eval.cases import (
    LiveAgentCase,
    RepresentativeAgentCase,
    RetrievalSuite,
    RoutingCase,
    SQLGuardCase,
    ToolCase,
    load_case_list,
    load_retrieval_suite,
)
from eval.metrics import accuracy, hit_at_k, percentile, reciprocal_rank


EVAL_DIR = Path(__file__).resolve().parent
SuiteRunner = Callable[[], dict[str, Any]]


def run_routing() -> dict[str, Any]:
    cases = load_case_list(EVAL_DIR / "routing_cases.json", RoutingCase)
    router = IntentRouter(llm=None)
    details: list[dict[str, Any]] = []
    outcomes: list[bool] = []
    for case in cases:
        actual = router.route(case.query)
        passed = (
            actual.intent == case.expected_intent
            and actual.source == case.expected_source
        )
        outcomes.append(passed)
        details.append(
            {
                "id": case.id,
                "passed": passed,
                "expected": {
                    "intent": case.expected_intent,
                    "source": case.expected_source,
                },
                "actual": {
                    "intent": actual.intent,
                    "source": actual.source,
                    "confidence": actual.confidence,
                },
            }
        )
    return _suite_result("routing", outcomes, details, {"accuracy": accuracy(outcomes)})


def run_tools() -> dict[str, Any]:
    cases = load_case_list(EVAL_DIR / "tool_cases.json", ToolCase)
    details: list[dict[str, Any]] = []
    outcomes: list[bool] = []
    for case in cases:
        actual_tool, actual_inputs = select_tool(case.query, case.intent)
        passed = (
            actual_tool == case.expected_tool
            and actual_inputs == case.expected_inputs
        )
        outcomes.append(passed)
        details.append(
            {
                "id": case.id,
                "passed": passed,
                "expected": {
                    "tool": case.expected_tool,
                    "inputs": case.expected_inputs,
                },
                "actual": {"tool": actual_tool, "inputs": actual_inputs},
            }
        )
    return _suite_result(
        "tools",
        outcomes,
        details,
        {"exact_match_accuracy": accuracy(outcomes)},
    )


def run_sql_guard() -> dict[str, Any]:
    cases = load_case_list(EVAL_DIR / "sql_cases.json", SQLGuardCase)
    guard = SQLGuard()
    details: list[dict[str, Any]] = []
    outcomes: list[bool] = []
    safe_outcomes: list[bool] = []
    unsafe_outcomes: list[bool] = []
    for case in cases:
        try:
            guarded = guard.validate(case.sql)
            actual = {
                "allowed": True,
                "error_code": None,
                "tables": list(guarded.tables),
                "limit": guarded.limit,
            }
        except SQLGuardError as exc:
            actual = {
                "allowed": False,
                "error_code": exc.code,
                "tables": [],
                "limit": None,
            }
        passed = actual["allowed"] == case.expected_allowed
        if case.expected_allowed:
            passed = passed and actual["tables"] == sorted(case.expected_tables)
            passed = passed and actual["limit"] == case.expected_limit
        else:
            passed = passed and actual["error_code"] == case.expected_error_code
        outcomes.append(passed)
        if case.expected_allowed:
            safe_outcomes.append(passed)
        else:
            unsafe_outcomes.append(passed)
        details.append(
            {
                "id": case.id,
                "passed": passed,
                "expected": {
                    "allowed": case.expected_allowed,
                    "error_code": case.expected_error_code,
                    "tables": sorted(case.expected_tables),
                    "limit": case.expected_limit,
                },
                "actual": actual,
            }
        )
    return _suite_result(
        "sql_guard",
        outcomes,
        details,
        {
            "accuracy": accuracy(outcomes),
            "safe_acceptance_rate": accuracy(safe_outcomes),
            "unsafe_rejection_rate": accuracy(unsafe_outcomes),
        },
    )


def run_retrieval() -> dict[str, Any]:
    suite: RetrievalSuite = load_retrieval_suite(EVAL_DIR / "retrieval_cases.json")
    retriever = BM25Retriever(
        [
            RetrievalDocument(
                id=document.id,
                title=document.title,
                content=document.content,
                category=document.category,
                source=document.source,
            )
            for document in suite.documents
        ]
    )
    details: list[dict[str, Any]] = []
    hits: list[bool] = []
    reciprocal_ranks: list[float] = []
    for case in suite.cases:
        result = retriever.search(case.query, limit=len(suite.documents))
        ranked_ids = [hit.document.id for hit in result.hits]
        relevant_ids = set(case.relevant_document_ids)
        hit = hit_at_k(ranked_ids, relevant_ids, case.k)
        rank_score = reciprocal_rank(ranked_ids, relevant_ids)
        hits.append(hit)
        reciprocal_ranks.append(rank_score)
        details.append(
            {
                "id": case.id,
                "passed": hit,
                "expected": {"relevant_document_ids": sorted(relevant_ids), "k": case.k},
                "actual": {
                    "ranked_document_ids": ranked_ids,
                    "hit_at_k": int(hit),
                    "reciprocal_rank": rank_score,
                },
            }
        )
    return _suite_result(
        "retrieval",
        hits,
        details,
        {
            "hit_rate_at_k": accuracy(hits),
            "mrr": sum(reciprocal_ranks) / len(reciprocal_ranks),
        },
    )


def run_live_agent(
    base_url: str,
    *,
    case_id: str | None = None,
    timeout_seconds: float = 180,
) -> dict[str, Any]:
    """Evaluate the deployed HTTP Agent boundary with real external dependencies."""
    cases = [
        *load_case_list(EVAL_DIR / "incident_cases.json", LiveAgentCase),
        *load_case_list(EVAL_DIR / "agent_cases.json", LiveAgentCase),
    ]
    if case_id is not None:
        cases = [case for case in cases if case.id == case_id]
        if not cases:
            raise ValueError(f"Unknown live Agent case ID: {case_id}.")

    details: list[dict[str, Any]] = []
    outcomes: list[bool] = []
    latencies_ms: list[int] = []
    with httpx.Client(
        base_url=base_url.rstrip("/"),
        timeout=httpx.Timeout(timeout_seconds),
        follow_redirects=True,
    ) as client:
        for case in cases:
            started = perf_counter()
            try:
                response = client.post("/agent/runs", json={"query": case.query})
                response.raise_for_status()
                envelope = SuccessResponse[AgentResult].model_validate(response.json())
                latency_ms = max(0, int((perf_counter() - started) * 1_000))
                result = envelope.data
                checks = _live_agent_checks(case, result)
                passed = all(checks.values())
                detail: dict[str, Any] = {
                    "id": case.id,
                    "passed": passed,
                    "latency_ms": latency_ms,
                    "checks": checks,
                    "actual": {
                        "run_id": result.run_id,
                        "intent": result.intent,
                        "status": result.status,
                        "plan_tools": [step.tool for step in result.plan],
                        "executed_tools": [
                            item.get("tool") for item in result.evidence
                        ],
                        "evidence_count": len(result.evidence),
                        "analysis_source": result.analysis_source,
                        "risk_level": result.risk_level,
                        "requires_approval": result.requires_approval,
                        "error": result.error,
                    },
                }
            except (httpx.HTTPError, ValueError) as exc:
                latency_ms = max(0, int((perf_counter() - started) * 1_000))
                passed = False
                detail = {
                    "id": case.id,
                    "passed": False,
                    "latency_ms": latency_ms,
                    "error": type(exc).__name__,
                }
            outcomes.append(passed)
            latencies_ms.append(latency_ms)
            details.append(detail)

    return _suite_result(
        "live_agent",
        outcomes,
        details,
        {
            "task_success_rate": accuracy(outcomes),
            "average_latency_ms": sum(latencies_ms) / len(latencies_ms),
        },
    )


def _live_agent_checks(case: LiveAgentCase, result: AgentResult) -> dict[str, bool]:
    plan_tools = [step.tool for step in result.plan]
    executed_tools = [item.get("tool") for item in result.evidence]
    output_keys_valid = True
    for tool_name, required_keys in case.required_output_keys.items():
        observations = [
            item for item in result.evidence if item.get("tool") == tool_name
        ]
        if not observations or not all(
            set(required_keys).issubset((item.get("output") or {}).keys())
            for item in observations
        ):
            output_keys_valid = False
            break
    return {
        "intent": result.intent == case.expected_intent,
        "status": result.status == case.expected_status,
        "plan_tools": plan_tools == case.expected_plan_tools,
        "executed_tools": executed_tools == case.expected_executed_tools,
        "minimum_evidence": len(result.evidence) >= case.minimum_evidence,
        "analysis_presence": (result.analysis is not None) == case.analysis_required,
        "risk_level": result.risk_level == case.expected_risk_level,
        "requires_approval": (
            result.requires_approval == case.expected_requires_approval
        ),
        "answer_terms": all(
            term in result.final_answer for term in case.required_answer_terms
        ),
        "tool_output_contract": output_keys_valid,
        "no_agent_error": result.error is None,
    }


class _EmptyMCPClient:
    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"items": [], "total": 0}


class _FailingMCPClient:
    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("Injected MCP failure.")


class _FailingStructuredLLM:
    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
    ) -> BaseModel:
        emit_llm_trace(
            model="eval-invalid-json",
            response_schema=response_model.__name__,
            latency_ms=0,
            status="failed",
            error="JSON_PARSE_ERROR",
            tokens=None,
        )
        raise LLMServiceError("Injected invalid structured output.")


class _UnsafeSQLLLM:
    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
    ) -> BaseModel:
        return response_model.model_validate({"sql": "DELETE FROM tickets WHERE id = 1"})


def run_representative_agent(
    *,
    case_id: str | None = None,
) -> dict[str, Any]:
    """Run controlled end-to-end scenarios through the local FastAPI boundary."""
    cases = load_case_list(
        EVAL_DIR / "representative_cases.json",
        RepresentativeAgentCase,
    )
    if case_id is not None:
        cases = [case for case in cases if case.id == case_id]
        if not cases:
            raise ValueError(f"Unknown representative Agent case ID: {case_id}.")

    details: list[dict[str, Any]] = []
    outcomes: list[bool] = []
    tool_checks: list[bool] = []
    evidence_checks: list[bool] = []
    citation_checks: list[bool] = []
    safety_checks: list[bool] = []
    online_llm_checks: list[bool] = []
    latencies: list[int] = []
    llm_calls = 0
    total_tokens = 0

    with TestClient(create_app()) as client:
        original_registry = client.app.state.tool_registry
        original_analyzer = client.app.state.incident_analyzer
        for case in cases:
            client.app.state.tool_registry = original_registry
            client.app.state.incident_analyzer = original_analyzer
            if case.fault == "empty_mcp":
                client.app.state.tool_registry = create_default_registry(
                    mcp_client=_EmptyMCPClient()
                )
            elif case.fault == "mcp_failure":
                client.app.state.tool_registry = create_default_registry(
                    mcp_client=_FailingMCPClient()
                )
            elif case.fault == "llm_failure":
                client.app.state.incident_analyzer = IncidentAnalyzer(
                    _FailingStructuredLLM()
                )
            elif case.fault == "sql_guard_rejection":
                client.app.state.tool_registry = create_default_registry(
                    Text2SQLService(_UnsafeSQLLLM()),
                    client.app.state.mcp_client,
                )

            started = perf_counter()
            response = client.post("/agent/runs", json={"query": case.query})
            result: AgentResult | None = None
            run_id: int | None = None
            if response.status_code == 200:
                result = SuccessResponse[AgentResult].model_validate(response.json()).data
                run_id = result.run_id

            if case.fault == "missing_approval_checkpoint" and result is not None:
                with client.app.state.session_factory() as session:
                    checkpoint = session.get(AgentApprovalCheckpoint, result.run_id)
                    if checkpoint is not None:
                        session.delete(checkpoint)
                        session.commit()
                response = client.post(
                    f"/agent/runs/{result.run_id}/approval",
                    json={"action": "approve"},
                )
                detail_response = client.get(f"/agent/runs/{result.run_id}")
                detail_data = detail_response.json()["data"]
                actual_status = detail_data["status"]
                actual_error = detail_data["final_answer"]
                actual_intent = detail_data["intent"]
                plan_tools = [item["tool"] for item in detail_data["plan"]]
                executed_tools = [item["tool"] for item in detail_data["evidence"]]
                evidence = detail_data["evidence"]
                analysis = None
                analysis_source = None
            elif result is not None:
                actual_status = result.status.value
                actual_error = result.error
                actual_intent = result.intent
                plan_tools = [step.tool for step in result.plan]
                executed_tools = [item.get("tool") for item in result.evidence]
                evidence = result.evidence
                analysis = result.analysis
                analysis_source = result.analysis_source
            else:
                actual_status = None
                actual_error = response.json().get("error", {}).get("code")
                actual_intent = None
                plan_tools = []
                executed_tools = []
                evidence = []
                analysis = None
                analysis_source = None

            latency_ms = max(0, int((perf_counter() - started) * 1_000))
            trace_events: list[dict[str, Any]] = []
            if run_id is not None:
                trace_response = client.get(f"/agent/runs/{run_id}/trace")
                if trace_response.status_code == 200:
                    trace_events = trace_response.json()["data"]["events"]

            expected_tools_ok = (
                plan_tools == case.expected_plan_tools
                and executed_tools == case.expected_executed_tools
            )
            zero_results_ok = all(
                any(
                    item.get("tool") == tool
                    and (item.get("output") or {}).get("total") == 0
                    for item in evidence
                )
                for tool in case.zero_result_tools
            )
            expected_evidence = set(case.expected_executed_tools)
            tool_execution_coverage_ok = expected_evidence.issubset(set(executed_tools))
            substantive_evidence_count = sum(
                _is_substantive_evidence(item) for item in evidence
            )
            substantive_evidence_ok = (
                substantive_evidence_count >= case.minimum_substantive_evidence
                and (
                    case.maximum_substantive_evidence is None
                    or substantive_evidence_count <= case.maximum_substantive_evidence
                )
            )
            evidence_steps = {
                item.get("step") for item in evidence if isinstance(item.get("step"), int)
            }
            cited_steps = set(analysis.evidence_steps) if analysis is not None else set()
            citation_ok = analysis is None or (
                bool(cited_steps) and cited_steps.issubset(evidence_steps)
            )
            safety_ok = not case.safety_case or not executed_tools
            checks = {
                "http_status": response.status_code == case.expected_http_status,
                "intent": actual_intent == case.expected_intent,
                "run_status": actual_status == case.expected_status.value,
                "error": actual_error == case.expected_error,
                "tool_selection": expected_tools_ok,
                "zero_results": zero_results_ok,
                "tool_execution_coverage": tool_execution_coverage_ok,
                "substantive_evidence": substantive_evidence_ok,
                "citation_correctness": citation_ok,
                "analysis_source": (
                    case.expected_analysis_source is None
                    or analysis_source == case.expected_analysis_source
                ),
                "safety_rejection": safety_ok,
            }
            passed = all(checks.values())
            case_llm_events = [
                event for event in trace_events if event.get("event_type") == "llm_call"
            ]
            case_tokens = sum(
                (event.get("tokens_json") or {}).get("total", 0)
                for event in case_llm_events
            )
            llm_calls += len(case_llm_events)
            total_tokens += case_tokens
            outcomes.append(passed)
            tool_checks.append(expected_tools_ok)
            evidence_checks.append(substantive_evidence_ok)
            citation_checks.append(citation_ok)
            if case.safety_case:
                safety_checks.append(safety_ok)
            if case.fault == "none" and case.expected_analysis_source == "llm":
                online_llm_checks.append(analysis_source == "llm")
            latencies.append(latency_ms)
            details.append({
                "id": case.id,
                "fault": case.fault,
                "passed": passed,
                "latency_ms": latency_ms,
                "checks": checks,
                "actual": {
                    "run_id": run_id,
                    "http_status": response.status_code,
                    "intent": actual_intent,
                    "status": actual_status,
                    "error": actual_error,
                    "plan_tools": plan_tools,
                    "executed_tools": executed_tools,
                    "tool_observation_count": len(evidence),
                    "substantive_evidence_count": substantive_evidence_count,
                    "llm_calls": len(case_llm_events),
                    "tokens": case_tokens,
                },
            })

    return _suite_result(
        "representative_agent",
        outcomes,
        details,
        {
            "task_success_rate": accuracy(outcomes),
            "tool_selection_accuracy": accuracy(tool_checks),
            "evidence_coverage_rate": accuracy(evidence_checks),
            "citation_correctness_rate": accuracy(citation_checks),
            "safety_rejection_rate": accuracy(safety_checks),
            "online_llm_success_rate": accuracy(online_llm_checks),
            "average_latency_ms": sum(latencies) / len(latencies),
            "p95_latency_ms": percentile(latencies, 0.95),
            "llm_calls": float(llm_calls),
            "total_tokens": float(total_tokens),
        },
    )


def _is_substantive_evidence(item: dict[str, Any]) -> bool:
    """Distinguish a successful tool observation from a non-empty business result."""
    output = item.get("output")
    if not isinstance(output, dict):
        return False
    if isinstance(output.get("total"), int):
        return output["total"] > 0
    if isinstance(output.get("row_count"), int):
        return output["row_count"] > 0
    if "ticket" in output:
        return output["ticket"] is not None
    return bool(output)


def _suite_result(
    name: str,
    outcomes: list[bool],
    details: list[dict[str, Any]],
    metrics: dict[str, float],
) -> dict[str, Any]:
    return {
        "suite": name,
        "total": len(outcomes),
        "passed": sum(outcomes),
        "failed": len(outcomes) - sum(outcomes),
        "metrics": {key: round(value, 4) for key, value in metrics.items()},
        "cases": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run WorkPilot deterministic evaluations.")
    parser.add_argument(
        "--suite",
        choices=(
            "routing", "tools", "sql_guard", "retrieval", "live_agent",
            "representative_agent", "all",
        ),
        default="all",
    )
    parser.add_argument("--base-url", help="Deployed WorkPilot URL for live_agent.")
    parser.add_argument("--case-id", help="Run one live Agent case by ID.")
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    runners: dict[str, SuiteRunner] = {
        "routing": run_routing,
        "tools": run_tools,
        "sql_guard": run_sql_guard,
        "retrieval": run_retrieval,
    }
    try:
        if args.suite == "live_agent":
            if not args.base_url:
                parser.error("--base-url is required for the live_agent suite.")
            results = [
                run_live_agent(
                    args.base_url,
                    case_id=args.case_id,
                    timeout_seconds=args.timeout,
                )
            ]
        elif args.suite == "representative_agent":
            results = [run_representative_agent(case_id=args.case_id)]
        else:
            selected = runners if args.suite == "all" else {args.suite: runners[args.suite]}
            results = [runner() for runner in selected.values()]
    except SQLAlchemyError as exc:
        print(json.dumps({
            "results": [],
            "infrastructure_error": {
                "code": "DATABASE_UNAVAILABLE",
                "type": type(exc).__name__,
            },
        }, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 1 if any(result.get("failed", 0) for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
