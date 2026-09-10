"""Node functions for the bounded LangGraph workflow and incident analysis."""

import json
from typing import Literal

from langgraph.runtime import Runtime
from langgraph.types import interrupt

from backend.agent.state import AgentContext, AgentState
from backend.schemas.agent import IncidentAnalysis
from backend.tools.base import ToolContext
from backend.tools.registry import ToolRegistryError


def intent_router(
    state: AgentState, runtime: Runtime[AgentContext],
) -> dict[str, object]:
    """Route by a high-confidence rule, then use the configured LLM as fallback."""
    decision = runtime.context.intent_router.route(state["query"])
    return {
        "intent": decision.intent,
        "routing_source": decision.source,
        "routing_confidence": decision.confidence,
    }


def plan_task(
    state: AgentState, runtime: Runtime[AgentContext],
) -> dict[str, object]:
    """Create one bounded ordered plan after intent classification."""
    decision = runtime.context.planner.create_plan(state["query"], state["intent"])
    return {
        "plan": [step.model_dump(mode="json") for step in decision.steps],
        "current_step": 0,
        "max_steps": runtime.context.planner.max_steps,
        "planner_source": decision.source,
    }


def route_after_planning(
    state: AgentState,
) -> Literal["risk_checker", "answer_generator"]:
    """Skip execution when no valid step was planned."""
    return "risk_checker" if state.get("plan") else "answer_generator"


def check_risk(
    state: AgentState, runtime: Runtime[AgentContext],
) -> dict[str, object]:
    """Classify the complete plan before any tool is allowed to execute."""
    assessment = runtime.context.risk_checker.assess(
        state["intent"],
        state["plan"],
        runtime.context.registry,
    )
    return {
        "risk_level": assessment.risk_level.value,
        "requires_approval": assessment.requires_approval,
        "risk_reasons": assessment.reasons,
        "approval_status": "pending" if assessment.requires_approval else None,
    }


def route_after_risk(
    state: AgentState,
) -> Literal["approval_gate", "tool_executor"]:
    """Pause HIGH-risk plans and allow other plans to execute."""
    return "approval_gate" if state.get("requires_approval") else "tool_executor"


def approval_gate(state: AgentState) -> dict[str, object]:
    """Interrupt once and convert the resumed human decision into graph state."""
    decision = interrupt(
        {
            "question": (
                "\u662f\u5426\u6279\u51c6\u6267\u884c\u8fd9\u4e2a"
                "\u6a21\u62df\u9ad8\u98ce\u9669\u8ba1\u5212\uff1f"
            ),
            "risk_level": state["risk_level"],
            "risk_reasons": state.get("risk_reasons", []),
            "plan": state["plan"],
        }
    )
    action = decision.get("action") if isinstance(decision, dict) else None
    if action == "approve":
        return {
            "requires_approval": False,
            "approval_status": "approved",
            "error": None,
        }
    if action in {"reject", "cancel"}:
        return {
            "requires_approval": False,
            "approval_status": "rejected" if action == "reject" else "cancelled",
            "error": "APPROVAL_REJECTED" if action == "reject" else "APPROVAL_CANCELLED",
        }
    raise ValueError("Unsupported approval action.")


def route_after_approval(
    state: AgentState,
) -> Literal["tool_executor", "answer_generator"]:
    """Resume execution only after explicit approval."""
    return "answer_generator" if state.get("error") else "tool_executor"


def tool_executor(
    state: AgentState, runtime: Runtime[AgentContext],
) -> dict[str, object]:
    """Execute exactly the current step through ToolRegistry and store its observation."""
    step_index = state.get("current_step", 0)
    step = state["plan"][step_index]
    tool_name = step["tool"]
    tool_inputs = step["inputs"]
    next_step = step_index + 1
    previous_results = state.get("tool_results", [])
    previous_evidence = state.get("evidence", [])

    try:
        output = runtime.context.registry.call_tool(
            tool_name,
            tool_inputs,
            ToolContext(
                session=runtime.context.session,
                agent_run_id=runtime.context.agent_run_id,
            ),
        )
    except ToolRegistryError as exc:
        return {
            "selected_tool": tool_name,
            "tool_inputs": tool_inputs,
            "current_step": next_step,
            "tool_results": previous_results,
            "error": exc.code,
        }

    observation = {
        "step": next_step,
        "tool": tool_name,
        "output": output.model_dump(mode="json"),
    }
    return {
        "selected_tool": tool_name,
        "tool_inputs": tool_inputs,
        "current_step": next_step,
        "tool_results": [*previous_results, observation],
        "evidence": [*previous_evidence, observation],
        "error": None,
    }


def route_after_execution(
    state: AgentState,
) -> Literal["tool_executor", "incident_analyzer", "answer_generator"]:
    """Continue only while the plan has work, no error exists, and max_steps is not reached."""
    if state.get("error"):
        return "answer_generator"
    current_step = state.get("current_step", 0)
    if current_step >= min(len(state.get("plan", [])), state.get("max_steps", 5)):
        if state.get("intent") == "incident_analysis" and state.get("evidence"):
            return "incident_analyzer"
        return "answer_generator"
    return "tool_executor"


def analyze_incident(
    state: AgentState, runtime: Runtime[AgentContext],
) -> dict[str, object]:
    """Turn completed tool evidence into a grounded structured incident analysis."""
    decision = runtime.context.incident_analyzer.analyze(
        state["query"],
        state.get("evidence", []),
    )
    return {
        "analysis": decision.analysis.model_dump(mode="json"),
        "analysis_source": decision.source,
    }


def answer_generator(state: AgentState) -> dict[str, object]:
    """Build a deterministic answer from all completed plan observations."""
    results = state.get("tool_results", [])
    if state.get("error") == "APPROVAL_REJECTED":
        return {"final_answer": "高风险计划已被拒绝，未执行任何工具。"}
    if state.get("error") == "APPROVAL_CANCELLED":
        return {"final_answer": "高风险计划已取消，未执行任何工具。"}
    if state.get("error"):
        return {
            "final_answer": (
                f"Agent 在完成 {len(results)} 个步骤后停止，后续步骤未执行。"
            ),
        }
    if not state.get("plan"):
        return {
            "final_answer": "当前 Agent 无法为该问题生成可执行的工具计划。",
        }
    if not results:
        return {"final_answer": "计划执行完成，但没有获得可用结果。"}
    if state.get("analysis"):
        analysis = IncidentAnalysis.model_validate(state["analysis"])
        return {
            "final_answer": _with_risk_notice(
                _format_incident_analysis(analysis),
                state,
            ),
        }
    return {
        "final_answer": _with_risk_notice(
            (
                f"计划执行完成，共完成 {len(results)} 个步骤："
                + json.dumps(results, ensure_ascii=False)
            ),
            state,
        ),
    }


def _format_incident_analysis(analysis: IncidentAnalysis) -> str:
    sections = [f"故障分析：{analysis.summary}"]
    sections.append("已确认事实：\n" + "\n".join(
        f"- {item}" for item in analysis.confirmed_facts
    ))
    if analysis.hypotheses:
        sections.append("可能原因（推断）：\n" + "\n".join(
            f"- {item}" for item in analysis.hypotheses
        ))
    sections.append("建议操作：\n" + "\n".join(
        f"- {item}" for item in analysis.recommended_actions
    ))
    sections.append(
        "引用证据步骤：" + ", ".join(str(step) for step in analysis.evidence_steps)
    )
    return "\n\n".join(sections)


def _with_risk_notice(answer: str, state: AgentState) -> str:
    if state.get("risk_level") != "MEDIUM":
        return answer
    reasons = "；".join(state.get("risk_reasons", []))
    return f"风险提示：{reasons}\n\n{answer}"
