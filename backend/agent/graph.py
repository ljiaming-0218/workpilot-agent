"""Build and compile the Phase 5 basic LangGraph state machine."""

from langgraph.graph import END, START, StateGraph

from backend.agent.nodes import (
    answer_generator,
    intent_router,
    route_after_selection,
    tool_executor,
    tool_selector,
)
from backend.agent.state import AgentContext, AgentState


def build_agent_graph():
    """Compile a reusable graph; request resources arrive through AgentContext."""
    builder = StateGraph(AgentState, context_schema=AgentContext)
    builder.add_node("intent_router", intent_router)
    builder.add_node("tool_selector", tool_selector)
    builder.add_node("tool_executor", tool_executor)
    builder.add_node("answer_generator", answer_generator)

    builder.add_edge(START, "intent_router")
    builder.add_edge("intent_router", "tool_selector")
    builder.add_conditional_edges(
        "tool_selector",
        route_after_selection,
        {
            "tool_executor": "tool_executor",
            "answer_generator": "answer_generator",
        },
    )
    builder.add_edge("tool_executor", "answer_generator")
    builder.add_edge("answer_generator", END)
    return builder.compile()
