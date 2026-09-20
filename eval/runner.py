"""CLI runner for deterministic component evaluations."""

import argparse
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

import httpx

from backend.agent.router import IntentRouter, select_tool
from backend.retrieval.retriever import BM25Retriever, RetrievalDocument
from backend.schemas.agent import AgentResult
from backend.schemas.common import SuccessResponse
from backend.security.sql_guard import SQLGuard, SQLGuardError
from eval.cases import (
    LiveAgentCase,
    RetrievalSuite,
    RoutingCase,
    SQLGuardCase,
    ToolCase,
    load_case_list,
    load_retrieval_suite,
)
from eval.metrics import accuracy, hit_at_k, reciprocal_rank


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
        choices=("routing", "tools", "sql_guard", "retrieval", "live_agent", "all"),
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
    else:
        selected = runners if args.suite == "all" else {args.suite: runners[args.suite]}
        results = [runner() for runner in selected.values()]
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
