"""Node functions for the bounded LangGraph workflow and incident analysis."""

import json
from typing import Literal

from langgraph.runtime import Runtime

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
) -> Literal["tool_executor", "answer_generator"]:
    """Skip execution when no valid step was planned."""
    return "tool_executor" if state.get("plan") else "answer_generator"


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
        return {"final_answer": _format_incident_analysis(analysis)}
    return {
        "final_answer": (
            f"计划执行完成，共完成 {len(results)} 个步骤："
            + json.dumps(results, ensure_ascii=False)
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
