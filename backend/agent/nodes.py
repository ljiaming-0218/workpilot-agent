"""Node functions for the Phase 5 single-tool LangGraph workflow."""

import json
from typing import Literal

from langgraph.graph import END
from langgraph.runtime import Runtime

from backend.agent.router import select_tool
from backend.agent.state import AgentContext, AgentState
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


def tool_selector(state: AgentState) -> dict[str, object]:
    """Select at most one tool; planning and multi-tool execution are later phases."""
    tool_name, tool_inputs = select_tool(state["query"], state["intent"])
    return {
        "selected_tool": tool_name,
        "tool_inputs": tool_inputs,
        "plan": ([{"tool": tool_name, "inputs": tool_inputs}] if tool_name else []),
    }


def route_after_selection(state: AgentState) -> Literal["tool_executor", "answer_generator"]:
    """Skip tool execution when the basic router classifies a general request."""
    return "tool_executor" if state.get("selected_tool") else "answer_generator"


def tool_executor(
    state: AgentState, runtime: Runtime[AgentContext],
) -> dict[str, object]:
    """Execute the selected tool through ToolRegistry so validation and audit remain active."""
    try:
        output = runtime.context.registry.call_tool(
            state["selected_tool"],
            state["tool_inputs"],
            ToolContext(
                session=runtime.context.session,
                agent_run_id=runtime.context.agent_run_id,
            ),
        )
    except ToolRegistryError as exc:
        return {
            "current_step": 1,
            "tool_results": [],
            "error": exc.code,
        }

    result = {
        "tool": state["selected_tool"],
        "output": output.model_dump(mode="json"),
    }
    return {
        "current_step": 1,
        "tool_results": [result],
        "evidence": [result],
        "error": None,
    }


def answer_generator(state: AgentState) -> dict[str, object]:
    """Build a deterministic Phase 5 answer from the tool result."""
    if state.get("error"):
        return {"final_answer": "Agent 执行失败，请稍后重试。"}
    if not state.get("selected_tool"):
        return {
            "final_answer": "当前基础 Agent 只能处理工单、日志、知识库和数据查询。",
        }

    results = state.get("tool_results", [])
    if not results:
        return {"final_answer": "查询完成，但没有获得可用结果。"}
    output = results[-1]["output"]
    return {
        "final_answer": "查询完成：" + json.dumps(output, ensure_ascii=False),
    }
