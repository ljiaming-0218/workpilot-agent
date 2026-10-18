"""Supervisor for bounded concurrent read-only service investigations."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import json
from threading import Event
from queue import Empty, Queue
from time import monotonic, perf_counter_ns
from typing import Any

from sqlalchemy.orm import Session

from backend.agent.analyzer import IncidentAnalyzer
from backend.agent.diagnostic_agents import DiagnosticAgents
from backend.agent.evidence_verifier import EvidenceVerifier
from backend.agent.planner import Planner, _service_names
from backend.agent.risk import RiskChecker
from backend.agent.router import IntentRouter
from backend.agent.trace import TraceRecorder
from backend.models.agent_run import AgentRun
from backend.models.common import utc_now
from backend.models.enums import AgentRunStatus, RiskLevel
from backend.schemas.agent import (
    AgentResult, AgentTaskResult, IncidentAnalysis, PlannerDecision, RoutingDecision,
    ServiceInvestigation, VerifierReport,
)
from backend.services.agent_service import AgentExecutionError
from backend.services.llm_service import LLMRequestTimeoutError
from backend.services.multi_agent_runtime import (
    MAX_AGENT_ATTEMPTS, MAX_TOOL_ATTEMPTS, InvestigationOutcome,
    RUN_DEADLINE_SECONDS, SessionFactory, ToolBudget, investigate_service,
)
from backend.tools.registry import ToolRegistry


ProgressSink = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True, slots=True)
class MultiAgentOptions:
    max_workers: int = 2
    enable_followup: bool = True
    enable_retry: bool = True
    enable_verifier: bool = True

    def __post_init__(self) -> None:
        if self.max_workers not in (1, 2):
            raise ValueError("max_workers must be 1 or 2.")


class MultiAgentScopeError(ValueError):
    """Multi mode supports only scoped read-only fault investigations."""


def prepare_multi_scope(
    query: str, router: IntentRouter, planner: Planner,
) -> tuple[RoutingDecision, PlannerDecision]:
    services = _service_names(query)
    if not services:
        raise MultiAgentScopeError("Multi mode requires a named service.")
    routing = router.route(query)
    plan = planner.create_plan(query, routing.intent)
    if routing.intent not in {"incident_analysis", "log_analysis"}:
        raise MultiAgentScopeError("Multi mode supports only read-only fault investigations.")
    if not plan.investigations:
        if len(services) == 1:
            plan.investigations = [ServiceInvestigation(service=services[0])]
    if not 1 <= len(plan.investigations) <= 3:
        raise MultiAgentScopeError("Multi mode requires one to three named services.")
    return routing, plan


def execute_multi_agent_run(
    session: Session,
    registry: ToolRegistry,
    risk_checker: RiskChecker,
    run_id: int,
    request_id: str,
    query: str,
    routing: RoutingDecision,
    planned: PlannerDecision,
    progress_sink: ProgressSink | None = None,
    session_factory: SessionFactory | None = None,
    options: MultiAgentOptions | None = None,
    agents: DiagnosticAgents | None = None,
) -> AgentResult:
    """Run at most two branch-owned Sessions concurrently, then merge on parent."""
    if session_factory is None:
        raise AgentExecutionError("Multi-Agent requires a branch Session factory.")
    options = options or MultiAgentOptions()
    started_ns = perf_counter_ns()
    trace = TraceRecorder(session, run_id, progress_sink)
    tasks: list[AgentTaskResult] = []
    evidence: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    budget = ToolBudget(MAX_TOOL_ATTEMPTS)
    stop_event = Event()
    run_deadline = monotonic() + RUN_DEADLINE_SECONDS
    services = [item.service for item in planned.investigations]
    model_budget = ToolBudget(MAX_AGENT_ATTEMPTS * len(services) + 1)
    try:
        # Check every tool that an investigator can select before the first call.
        candidate_steps = [
            {"tool": tool, "inputs": inputs}
            for service in services
            for tool, inputs in (
                ("query_error_logs", {"service_name": service, "minutes": 60, "limit": 20}),
                ("search_tickets", {"service_name": service, "limit": 10}),
                ("search_knowledge", {"query": f"{service} 故障排查", "limit": 5}),
            )
        ]
        assessment = risk_checker.assess(routing.intent, candidate_steps, registry)
        if assessment.requires_approval or any(
            registry.get_tool(step["tool"]).risk_level != RiskLevel.LOW
            for step in candidate_steps
        ):
            raise MultiAgentScopeError("Investigators may use only LOW read-only tools.")
        trace.record_multi_event(
            node="intent_router", event_type="node_completed",
            output_json={"intent": routing.intent, "routing_source": routing.source,
                         "routing_confidence": routing.confidence},
        )
        trace.record_multi_event(
            node="planner", event_type="node_completed",
            output_json={"planner_source": "rule", "plan": candidate_steps,
                         "services": services, "mode": "multi", "version": "w3"},
        )
        trace.record_multi_event(
            node="risk_checker", event_type="node_completed",
            output_json={"risk_level": assessment.risk_level.value,
                         "risk_reasons": assessment.reasons},
        )
        trace.record_multi_event(
            node="supervisor", event_type="tasks_dispatched",
            output_json={"services": services, "budget": MAX_TOOL_ATTEMPTS,
                         "concurrency": options.max_workers,
                         "followup_enabled": options.enable_followup,
                         "retry_enabled": options.enable_retry,
                         "verifier_enabled": options.enable_verifier,
                         "mcp_transport": "single_queue",
                         "mode": "multi", "version": "w3"},
        )
        results: dict[int, InvestigationOutcome] = {}
        branch_events: list[dict[str, Any]] = []
        progress_events: Queue = Queue()
        trace.persist(session)
        session.commit()
        fatal: Exception | None = None
        with ThreadPoolExecutor(max_workers=options.max_workers, thread_name_prefix="workpilot-investigator") as pool:
            futures = {
                pool.submit(
                    investigate_service, session_factory, registry, run_id,
                    service, index, budget, stop_event, run_deadline=run_deadline,
                    retry_enabled=options.enable_retry,
                    agents=agents, query=query, model_budget=model_budget,
                    event_sink=progress_events.put,
                ): index
                for index, service in enumerate(services, start=1)
            }
            pending = set(futures)
            while pending:
                done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                batch = []
                while True:
                    try:
                        batch.append(progress_events.get_nowait())
                    except Empty:
                        break
                if batch:
                    _flush_branch_events(batch, trace)
                    trace.persist(session)
                    session.commit()
                for future in done:
                    try:
                        results[futures[future]] = future.result()
                    except Exception as exc:
                        stop_event.set()
                        fatal = fatal or exc
        if fatal is not None:
            raise fatal
        for index in sorted(results):
            _merge_outcome(results[index], tasks, evidence, steps, branch_events)
        # Primary branch events were already streamed and persisted by the parent.

        followup = _followup_candidate(tasks, evidence, budget)
        if options.enable_followup and followup is not None and monotonic() < run_deadline:
            index, service, gap = followup
            followup_step = {"tool": "query_error_logs", "inputs": {
                "service_name": service, "minutes": 1_440, "limit": 20,
            }}
            new_risk = risk_checker.assess(routing.intent, [followup_step], registry)
            if new_risk.requires_approval or registry.get_tool("query_error_logs").risk_level != RiskLevel.LOW:
                raise MultiAgentScopeError("Follow-up failed its read-only risk check.")
            trace.record_multi_event(
                node="supervisor", event_type="followup_dispatched",
                output_json={"service": service, "gap": gap,
                             "tool": followup_step["tool"],
                             "inputs": followup_step["inputs"]},
            )
            remaining_local = MAX_AGENT_ATTEMPTS - tasks[index - 1].attempts_used
            outcome = investigate_service(
                session_factory, registry, run_id, service, index, budget,
                stop_event, followup=True, local_limit=remaining_local,
                run_deadline=run_deadline,
                retry_enabled=options.enable_retry,
                agents=agents, query=query, model_budget=model_budget,
                prior_evidence=[item for item in evidence if item["service"] == service],
            )
            branch_events = []
            _merge_outcome(outcome, tasks, evidence, steps, branch_events)
            _flush_branch_events(branch_events, trace)
        investigations = _service_investigations(services, tasks, evidence)
        # No configured gateway keeps the explicit rule-only evidence path.
        analysis = IncidentAnalyzer(None).analyze(query, evidence, investigations).analysis
        analysis.confirmed_facts = _candidate_facts(evidence)
        if len({item["service"] for item in evidence
                if item["tool"] == "query_error_logs" and item["output"].get("items")}) >= 2:
            analysis.hypotheses = [
                "多个服务的错误可能相关，需核对同一请求的调用链；现有记录未证明共同根因。"
            ]
        if options.enable_verifier:
            try:
                if agents is not None:
                    review_events: list[dict[str, Any]] = []
                    try:
                        verifier = agents.verify(tasks, evidence, model_budget, review_events)
                    finally:
                        _flush_branch_events(review_events, trace)
                else:
                    verifier = EvidenceVerifier().verify(analysis, tuple(deepcopy(evidence)))
            except (TimeoutError, LLMRequestTimeoutError):
                verifier = VerifierReport(status="timeout", error="VERIFIER_TIMEOUT")
            except Exception:
                verifier = VerifierReport(status="failed", error="VERIFIER_FAILED")
        else:
            verifier = VerifierReport(status="skipped", error="VERIFIER_DISABLED")
        trace.record_multi_event(
            node="evidence_verifier",
            event_type="review_completed" if verifier.status == "completed" else "review_failed",
            output_json=verifier.model_dump(mode="json"),
            error=verifier.error,
        )
        supported = [
            claim for claim in verifier.claims if claim.support == "supported"
        ] if verifier.status == "completed" else []
        if agents is not None:
            analysis = IncidentAnalysis(
                summary="已合并服务调查；以下结论经过独立模型核验，仍需结合原始记录复核。"
                if verifier.status == "completed" else "诊断核验未完成，当前不能给出已核验的故障原因。",
                confirmed_facts=["尚无通过核验的事实。"],
                hypotheses=[claim.text for claim in verifier.claims
                            if claim.claim_type == "hypothesis" and claim.support != "unsupported"][:5],
                recommended_actions=[claim.text for claim in supported if claim.claim_type == "recommendation"][:8]
                or ["先核对引用记录与未解决缺口，再决定处理措施。"],
                evidence_steps=[],
            )
        analysis.confirmed_facts = (
            [claim.text for claim in supported if claim.claim_type == "fact"][:10]
            or ["本次没有通过逐条核验的事实；请核对原始工具记录。"]
        )
        cited_ids = {evidence_id for claim in supported for evidence_id in claim.evidence_ids}
        analysis.evidence_steps = [
            item["step"] for item in evidence if item["evidence_id"] in cited_ids
        ][:5]
        answer = _answer(tasks, analysis.summary, verifier, semantic=agents is not None, evidence=evidence)
        status = AgentRunStatus.COMPLETED if evidence else AgentRunStatus.FAILED
        error = None if evidence else "NO_USABLE_EVIDENCE"
        investigation_status = (
            "failed" if not evidence else
            "partial" if any(task.status != "completed" for task in tasks) else
            "completed"
        )
        trace.record_multi_event(
            node="supervisor", event_type="results_merged",
            output_json={"mode": "multi", "tasks": [task.model_dump(mode="json") for task in tasks],
                         "evidence_ids": [item["evidence_id"] for item in evidence],
                         "status": status.value,
                         "investigation_status": investigation_status,
                         "tool_budget": {"used": budget.used, "limit": budget.limit},
                         "model_budget": {"used": model_budget.used, "limit": model_budget.limit} if agents else None,
                         "verifier": verifier.model_dump(mode="json")},
        )
        _finish(session, run_id, routing.intent, status, answer, started_ns, trace)
        return AgentResult(
            run_id=run_id, request_id=request_id, intent=routing.intent,
            routing_source=routing.source, routing_confidence=routing.confidence,
            plan=steps, planner_source="rule", steps_executed=budget.used,
            selected_tool=steps[-1]["tool"] if steps else None,
            final_answer=answer, tool_results=evidence, evidence=evidence,
            service_investigations=investigations, analysis=analysis,
            analysis_source="llm" if any(task.model_source == "llm" for task in tasks) else "fallback",
            risk_level=assessment.risk_level,
            requires_approval=False, risk_reasons=assessment.reasons,
            status=status, approval_status=None, error=error, mode="multi",
            agent_tasks=tasks,
            investigation_status=investigation_status,
            tool_budget={"used": budget.used, "limit": budget.limit},
            verifier=verifier,
            model_budget={"used": model_budget.used, "limit": model_budget.limit} if agents else None,
        )
    except Exception as exc:
        session.rollback()
        trace.record_multi_event(
            node="supervisor", event_type="run_failed", error=type(exc).__name__,
        )
        _finish(session, run_id, routing.intent, AgentRunStatus.FAILED,
                "Multi-Agent 调查因运行边界错误停止。", started_ns, trace)
        raise AgentExecutionError("Multi-Agent execution failed.") from exc


def _merge_outcome(
    outcome: InvestigationOutcome,
    tasks: list[AgentTaskResult],
    evidence: list[dict[str, Any]],
    steps: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> None:
    tasks.append(outcome.task)
    for observation in outcome.observations:
        observation["step"] = len(evidence) + 1
        evidence.append(observation)
    steps.extend(outcome.steps)
    events.extend(outcome.events)


def _flush_branch_events(
    events: list[dict[str, Any]], trace: TraceRecorder,
) -> None:
    for event in sorted(events, key=lambda item: item["occurred_ns"]):
        trace.record_multi_event(**{
            key: value for key, value in event.items() if key != "occurred_ns"
        })


def _followup_candidate(
    tasks: list[AgentTaskResult],
    evidence: list[dict[str, Any]],
    budget: ToolBudget,
) -> tuple[int, str, str] | None:
    if budget.used >= budget.limit:
        return None
    for index, task in enumerate(tasks, start=1):
        if task.attempts_used >= MAX_AGENT_ATTEMPTS:
            continue
        if "logs_empty_in_initial_window" not in task.gaps:
            continue
        inputs = {"service_name": task.service, "minutes": 1_440, "limit": 20}
        if any(item["tool"] == "query_error_logs" and item["inputs"] == inputs
               for item in task.decisions):
            continue
        if any(item["tool"] == "query_error_logs" and item["inputs"] == inputs
               for item in evidence):
            continue
        return index, task.service, "logs_empty_in_initial_window"
    return None


def _service_investigations(
    services: list[str], tasks: list[AgentTaskResult],
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    investigations: list[dict[str, Any]] = []
    for service in services:
        service_tasks = [task for task in tasks if task.service == service]
        service_evidence = [item for item in evidence if item["service"] == service]
        errors = [error for task in service_tasks for error in task.errors]
        incomplete = any(task.status != "completed" for task in service_tasks)
        status = "partial" if incomplete and service_evidence else (
            "completed" if service_evidence else "failed"
        )
        investigations.append({
            "service": service, "status": status,
            "error": ", ".join(errors) if errors else None,
            "evidence": service_evidence,
            "citations": [item["step"] for item in service_evidence],
        })
    return investigations


def _answer(tasks: list[AgentTaskResult], summary: str, verifier: VerifierReport,
            *, semantic: bool = False, evidence: list[dict[str, Any]] | None = None) -> str:
    lines = ["服务调查与诊断：", summary, "服务状态："]
    lines.extend(
        f"- {task.service}: {task.status}，证据 {len(task.evidence_ids)} 条"
        + (f"；失败 {', '.join(task.errors)}" if task.errors else "")
        for task in tasks
    )
    gaps = list(dict.fromkeys(
        f"{task.service}：" + ("初始日志窗口未检索到相关记录。" if gap == "logs_empty_in_initial_window"
                          else gap)
        for task in tasks for gap in task.gaps
    ))
    if gaps:
        lines.append("待核实的证据缺口：")
        lines.extend(f"- {gap}" for gap in gaps)
    if verifier.status != "completed":
        lines.append(f"核验未完成（{verifier.error}）；本次不输出已核验事实。")
        if evidence:
            lines.append("已检索的原始记录（未完成语义核验）：")
            for item in evidence[:9]:
                rows = item["output"].get("items", [])
                if not rows:
                    detail = "未检索到相关记录；不能据此判断服务正常。"
                else:
                    detail = json.dumps([{key: row[key] for key in
                        ("id", "service_name", "request_id", "created_at", "error_type", "message", "title", "content", "resolution")
                        if key in row} for row in rows[:2]], ensure_ascii=False, default=str)[:1600]
                lines.append(f"- {item['service']} / {item['tool']}：{detail} [证据 {item['evidence_id']}]")
        lines.extend(_next_checks(tasks, evidence or []))
        return "\n".join(lines)
    supported = [claim for claim in verifier.claims if claim.support == "supported" and claim.claim_type == "fact"]
    issues = [claim for claim in verifier.claims if claim.support == "unsupported"]
    hypotheses = [claim for claim in verifier.claims
                  if claim.claim_type == "hypothesis" and claim.support != "unsupported"]
    lines.append("已核验事实：")
    lines.extend(
        f"- {claim.text} [证据 {', '.join(claim.evidence_ids)}]"
        for claim in supported
    )
    if not supported:
        lines.append("- 当前没有通过逐条核验的事实。")
    for support, heading in (("supported", "证据支持的原因分析："),
                             ("unverified", "待验证假设：")):
        group = [claim for claim in hypotheses if claim.support == support]
        if group:
            lines.append(heading)
            lines.extend(f"- {claim.text} [证据 {', '.join(claim.evidence_ids)}]；{claim.reason}" for claim in group)
    recommendations = [claim for claim in verifier.claims
                       if claim.claim_type == "recommendation" and claim.support == "supported"]
    if recommendations:
        lines.append("建议的修复或验证步骤：")
        lines.extend(f"- {claim.text} [证据 {', '.join(claim.evidence_ids)}]" for claim in recommendations)
    else:
        lines.extend(_next_checks(tasks, evidence or []))
    uncertain_facts = [claim for claim in verifier.claims
                       if claim.claim_type == "fact" and claim.support == "unverified"]
    if uncertain_facts:
        lines.append("尚待核实的结论：")
        lines.extend(f"- {claim.text}；{claim.reason}" for claim in uncertain_facts)
    if issues:
        lines.append(f"另有 {len(issues)} 条结论缺乏逐条支持，详见核验意见。")
    lines.append("模型核验仍可能有误，请按证据 ID 核对原始记录。" if semantic else
                 "工具返回的数量不能证明故障根因；请按证据 ID 检查原始记录。")
    return "\n".join(lines)


def _next_checks(tasks: list[AgentTaskResult], evidence: list[dict[str, Any]]) -> list[str]:
    lines = ["建议的后续检查（不代表根因已确定）："]
    for service in dict.fromkeys(task.service for task in tasks):
        records = [item for item in evidence if item.get("service") == service]
        logs = [item for item in records if item.get("tool") == "query_error_logs"]
        action = ("按日志中的请求 ID 核对调用链、事件顺序及当前配置/指标，并补齐失败的查询。"
                  if any(item["output"].get("items") for item in logs) else
                  "检查日志采集与服务/时间过滤条件，补充故障发生时段的日志及当前指标。")
        references = ", ".join(item["evidence_id"] for item in (logs or records)[:2])
        lines.append(f"- {service}：{action}" + (f" [证据 {references}]" if references else ""))
    return lines if len(lines) > 1 else []


def _candidate_facts(evidence: list[dict[str, Any]]) -> list[str]:
    """Propose bounded record facts for independent verification."""
    facts: list[str] = []
    for item in evidence:
        step, tool, output = item["step"], item["tool"], item["output"]
        records = output.get("items", [])
        if tool == "query_error_logs" and records:
            for row in records[:2]:
                if isinstance(row, dict) and isinstance(row.get("id"), int) and isinstance(row.get("error_type"), str):
                    facts.append(f"步骤 {step} 的日志 ID {row['id']} 记录错误类型 {row['error_type']}。")
        elif tool == "search_tickets" and records:
            for row in records[:2]:
                if isinstance(row, dict) and isinstance(row.get("id"), int) and isinstance(row.get("status"), str):
                    facts.append(f"步骤 {step} 的工单 ID {row['id']} 状态为 {row['status']}。")
        if not records and isinstance(output.get("total"), int):
            facts.append(f"步骤 {step} 的 {tool} 返回 {output['total']} 条匹配记录。")
        if len(facts) >= 10:
            break
    return facts[:10] or ["本次没有可逐条核验的工具记录。"]


def _finish(
    session: Session, run_id: int, intent: str, status: AgentRunStatus,
    answer: str, started_ns: int, trace: TraceRecorder,
) -> None:
    run = session.get(AgentRun, run_id)
    if run is None:
        raise AgentExecutionError("Agent run disappeared.")
    run.intent = intent
    run.status = status
    run.final_answer = answer
    run.finished_at = utc_now()
    run.latency_ms = max(0, (perf_counter_ns() - started_ns) // 1_000_000)
    trace.persist(session)
    session.commit()
