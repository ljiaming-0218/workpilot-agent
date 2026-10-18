"""Live MCP/TiDB controlled replays; synthetic truth is never passed to agents."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from statistics import mean, median
from threading import Lock
from time import perf_counter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.agent.planner import Planner
from backend.agent.risk import RiskChecker
from backend.agent.router import IntentRouter
from backend.agent.trace import TraceRecorder
from backend.config import Settings
from backend.database import build_engine
from backend.main import _mcp_database_environment
from backend.mcp.client import WorkPilotMCPClient
from backend.models import ErrorLog, KnowledgeDoc, Ticket
from backend.models.agent_run import AgentRun
from backend.models.enums import AgentRunStatus, LogLevel, TicketPriority, TicketStatus, TicketCategory
from backend.models.tool_call import ToolCallLog
from backend.schemas.agent import VerifierReport
from backend.services import multi_agent_service as multi
from backend.services.agent_service import prepare_agent_run
from backend.services.multi_agent_runtime import records_match_scope
from backend.tools import create_default_registry
from eval.w4_fixture import _run_enhanced_single


ROOT = Path(__file__).resolve().parents[1]
CASES = Path(__file__).with_name("replay_cases.json")


def digest(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class RecordingClient:
    """Record actual MCP outputs; inject only the explicitly declared local failure."""

    def __init__(self, client: WorkPilotMCPClient, case: dict[str, Any]):
        self.client, self.case = client, case
        self.calls: list[dict[str, Any]] = []
        self.responses: dict[str, str] = {}
        self._lock = Lock()

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        service = arguments.get("service_name") or str(arguments.get("query", "")).split(" ")[0]
        key = json.dumps({"tool": name, "inputs": arguments}, sort_keys=True, ensure_ascii=False)
        started = perf_counter()
        try:
            if (self.case.get("fault") == "ticket_failure" and name == "search_tickets"
                    and service == self.case["services"][0]):
                raise RuntimeError("Controlled replay ticket failure")
            result = self.client.call_tool(name, arguments)
        except Exception as exc:
            with self._lock:
                self.responses[key] = digest({"error": type(exc).__name__})
                self.calls.append({"tool": name, "service": service, "inputs": arguments,
                                   "error": type(exc).__name__, "latency_ms": (perf_counter()-started)*1000})
            raise
        with self._lock:
            self.responses[key] = digest(result)
            self.calls.append({"tool": name, "service": service, "inputs": arguments,
                               "error": None, "latency_ms": (perf_counter()-started)*1000})
        return result


def seed(session: Session, cases: list[dict[str, Any]], anchor: datetime) -> dict[str, list[int]]:
    """Refresh only named replay records in the explicitly selected acceptance schema."""
    gold: dict[str, list[int]] = {}
    for case in cases:
        gold[case["id"]] = []
        for item in case["logs"]:
            row = session.scalar(select(ErrorLog).where(
                ErrorLog.service_name == item["service"], ErrorLog.request_id == item["request_id"],
            ))
            if row is None:
                row = ErrorLog(service_name=item["service"], request_id=item["request_id"])
                session.add(row)
            row.level, row.error_type, row.message = LogLevel.ERROR, item["error_type"], item["message"]
            row.created_at = anchor - timedelta(minutes=5)
            session.flush()
            gold[case["id"]].append(row.id)
        for service in case["services"]:
            title = f"[REPLAY] {case['id']} {service}"
            ticket = session.scalar(select(Ticket).where(Ticket.title == title))
            if ticket is None:
                ticket = Ticket(title=title, service_name=service)
                session.add(ticket)
            ticket.content, ticket.category = case["ticket"], TicketCategory.INCIDENT
            ticket.priority = TicketPriority.HIGH
            ticket.status = TicketStatus.RESOLVED if case["truth"]["cause_known"] else TicketStatus.OPEN
            ticket.resolution = case["ticket"] if case["truth"]["cause_known"] else None
            ticket.created_at = ticket.updated_at = anchor - timedelta(minutes=4)
        source = f"eval://incident-replay/{case['id']}"
        doc = session.scalar(select(KnowledgeDoc).where(KnowledgeDoc.source == source))
        if doc is None:
            doc = KnowledgeDoc(source=source)
            session.add(doc)
        doc.title = " ".join(case["services"]) + " 故障排查"
        doc.content = "Check original log messages and correlate request IDs. A timeout alone does not prove its cause."
        doc.category = "runbook"
        doc.created_at = doc.updated_at = anchor
    session.commit()
    return gold


def score(case: dict[str, Any], result: dict[str, Any], gold_ids: list[int]) -> dict[str, Any]:
    evidence = result["evidence"]
    answer = result["final_answer"]
    truth = case["truth"]
    retrieved = {row["id"] for item in evidence if item["tool"] == "query_error_logs"
                 for row in item["output"].get("items", [])}
    scope_ok = all(records_match_scope(item["tool"], item["service"], item["output"]) for item in evidence)
    errors = result.get("errors", [])
    known = truth["cause_known"]
    cause_in_evidence = truth.get("cause_marker", "") in json.dumps(evidence) if known else None
    cause_in_answer = truth["cause_marker"] in answer if known else None
    action_in_answer = truth["action_marker"] in answer if known else None
    isolated = None
    if truth.get("failed_tool"):
        isolated = bool(errors) and any(
            item["service"] == truth["unaffected_service"] and item["output"].get("items")
            for item in evidence
        ) and not any(item["tool"] == truth["failed_tool"] and item["service"] == case["services"][0]
                      for item in evidence)
    empty_safe = None
    if truth.get("expect_empty_logs"):
        empty_safe = any(item["tool"] == "query_error_logs" and item["output"].get("total") == 0
                         for item in evidence) and not re.search(r"(?:^|[。！？\n])(?:该)?服务正常[。！]", answer)
    return {
        "gold_log_recall": len(retrieved.intersection(gold_ids))/len(gold_ids) if gold_ids else None,
        "scope_ok": scope_ok,
        "cause_available_in_evidence": cause_in_evidence,
        "specific_cause_terms_present": cause_in_answer,
        "specific_action_terms_present": action_in_answer,
        "diagnostic_delivery": ("terms_present_needs_review" if cause_in_answer and action_in_answer
                                else "missing_specific_diagnosis") if known else "not_applicable",
        "uncertainty_in_summary": bool(re.search("不确定|不足|冲突", result["analysis"]["summary"]))
        if truth.get("require_uncertainty") else None,
        "empty_logs_handled_as_absence": empty_safe,
        "failure_isolated": isolated,
        "causal_correctness_human_review": "pending",
    }


def run_one(factory, client, case, variant, gold_ids):
    query = "调查 " + "、".join(case["services"]) + " 的故障，核对日志与工单，说明原因和证据限制。"
    recorded = RecordingClient(client, case)
    with factory() as parent:
        run_id, request_id = prepare_agent_run(parent, query)
        started = perf_counter()
        if variant == "enhanced_single_equal":
            result = _run_enhanced_single({**case, "query": query}, run_id, recorded, session_factory=factory)
            report = VerifierReport.model_validate(result["verifier"])
            result["final_answer"] = multi._answer([], result["analysis"]["summary"], report)
            run = parent.get(AgentRun, run_id)
            run.status = AgentRunStatus(result["status"])
            run.intent, run.final_answer = result["intent"], result["final_answer"]
            run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            run.latency_ms = int((perf_counter()-started)*1000)
            trace = TraceRecorder(parent, run_id)
            trace.record_multi_event(node="eval_single", event_type="replay_completed",
                                     output_json={"variant": variant, "budget": result["tool_budget"]})
            trace.persist(parent)
            parent.commit()
        else:
            routing, planned = multi.prepare_multi_scope(query, IntentRouter(None), Planner(None))
            response = multi.execute_multi_agent_run(
                parent, create_default_registry(mcp_client=recorded), RiskChecker(),
                run_id, request_id, query, routing, planned, session_factory=factory,
            )
            result = response.model_dump(mode="json")
            result["errors"] = [error for task in result["agent_tasks"] for error in task["errors"]]
        elapsed = (perf_counter()-started)*1000
        audit_count = parent.scalar(select(func.count()).select_from(ToolCallLog).where(ToolCallLog.agent_run_id == run_id))
    return {
        "case_id": case["id"], "variant": variant, "run_id": run_id, "request_id": request_id,
        "latency_ms": round(elapsed, 2), "tool_attempts": len(recorded.calls),
        "audit_count": audit_count, "budget_ok": len(recorded.calls) <= 9,
        "audit_complete": audit_count == len(recorded.calls), "model_calls": 0, "tokens": 0,
        "llm_cost_usd": 0, "database_cost_usd": None,
        "calls": recorded.calls, "response_hashes": recorded.responses,
        "scores": score(case, result, gold_ids), "result": result,
    }


def compare_pairs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, int], dict[str, Any]] = defaultdict(dict)
    for record in records:
        groups[(record["case_id"], record["repeat"])][record["variant"]] = record
    return [{"case_id": key[0], "repeat": key[1],
             "same_mcp_responses": pair["enhanced_single_equal"]["response_hashes"] == pair["multi"]["response_hashes"],
             "same_tool_attempts": pair["enhanced_single_equal"]["tool_attempts"] == pair["multi"]["tool_attempts"]}
            for key, pair in groups.items()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="workpilot_acceptance_20261016")
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    if not args.database.startswith("workpilot_acceptance_") or not 1 <= args.repeats <= 5:
        parser.error("Use an explicit workpilot_acceptance_* schema and 1-5 repeats.")
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    settings = Settings().model_copy(update={"mysql_database": args.database})
    engine = build_engine(settings)
    factory = sessionmaker(bind=engine, autoflush=False)
    anchor = datetime.now(timezone.utc).replace(tzinfo=None)
    output = ROOT / "eval" / "results" / "incident_replay" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True)
    files = ["eval/incident_replay.py", "eval/replay_cases.json", "eval/w4_fixture.py",
             "backend/services/multi_agent_service.py", "backend/services/multi_agent_runtime.py",
             "backend/agent/evidence_verifier.py", "backend/agent/analyzer.py", "backend/mcp/client.py"]
    metadata = {
        "suite": "controlled-live-replay-v1", "database": args.database, "anchor_utc": anchor.isoformat(),
        "cases_sha256": sha256(CASES.read_bytes()).hexdigest(),
        "source_sha256": {name: sha256((ROOT/name).read_bytes()).hexdigest() for name in files},
        "tool_limit": 9, "per_service_limit": 3, "global_deadline_s": 180, "task_deadline_s": 90,
        "transport": "real MCP stdio single queue", "model_calls_per_variant": 0,
        "analyzer": "shared rule fallback and exact-field verifier", "synthetic_data": True,
        "diagnosis_metric": "term presence only; never reported as causal accuracy",
        "latency_scope": "orchestration + real MCP + TiDB tool audit + final run persistence",
        "latency_excludes": "run creation and final audit-count probe",
        "p95_interpretation": "not estimated: small per-scenario sample",
    }
    records=[]
    client=WorkPilotMCPClient(server_environment=_mcp_database_environment(settings))
    try:
        with factory() as session:
            gold=seed(session,cases,anchor)
        metadata["gold_log_ids"] = gold
        (output/"metadata.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding="utf-8")
        client.connect()
        for repeat in range(1,args.repeats+1):
            for case in cases:
                # Alternate the pair order to reduce warmup/order bias.
                variants=("enhanced_single_equal","multi") if repeat%2 else ("multi","enhanced_single_equal")
                for variant in variants:
                    record=run_one(factory,client,case,variant,gold[case["id"]])
                    record["repeat"]=repeat
                    records.append(record)
                    with (output/"raw.jsonl").open("a",encoding="utf-8") as stream:
                        stream.write(json.dumps(record,ensure_ascii=False)+"\n")
                    print(json.dumps({"case":case["id"],"repeat":repeat,"variant":variant,
                                      "run_id":record["run_id"],"latency_ms":record["latency_ms"],
                                      "scores":record["scores"]},ensure_ascii=True),flush=True)
    finally:
        client.close()
        engine.dispose()
    pairs=compare_pairs(records)
    summary={"metadata":metadata,"pairs":pairs,"variants":{}}
    for variant in ("enhanced_single_equal","multi"):
        items=[r for r in records if r["variant"]==variant]
        latencies=[r["latency_ms"] for r in items]
        summary["variants"][variant]={
            "runs":len(items),"mean_latency_ms":round(mean(latencies),2),
            "median_latency_ms":round(median(latencies),2),"p95_latency_ms":None,
            "tool_attempts":sum(r["tool_attempts"] for r in items),"model_calls":0,"tokens":0,
            "scope_violations":sum(not r["scores"]["scope_ok"] for r in items),
            "audit_failures":sum(not r["audit_complete"] for r in items),
            "specific_diagnosis_delivered":sum(r["scores"]["diagnostic_delivery"]=="terms_present_needs_review" for r in items),
            "diagnosable_runs":sum(r["scores"]["cause_available_in_evidence"] is True for r in items),
            "cases":{case["id"]:[{"run_id":r["run_id"],"latency_ms":r["latency_ms"],"scores":r["scores"]}
                                  for r in items if r["case_id"]==case["id"]] for case in cases},
        }
    summary["all_pairs_evidence_equal"]=all(p["same_mcp_responses"] for p in pairs)
    summary["all_runs_within_budget"]=all(r["budget_ok"] for r in records)
    (output/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"output":str(output),"variants":summary["variants"],
                      "all_pairs_evidence_equal":summary["all_pairs_evidence_equal"]},ensure_ascii=True),flush=True)
    return 0 if summary["all_pairs_evidence_equal"] and summary["all_runs_within_budget"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
