"""Build and compile the Phase 5 basic LangGraph state machine."""

from langgraph.graph import END, START, StateGraph

from backend.agent.nodes import (
    analyze_incident,
    answer_generator,
    intent_router,
    plan_task,
    route_after_execution,
    route_after_planning,
    tool_executor,
)
from backend.agent.state import AgentContext, AgentState


def build_agent_graph():
    """Compile a reusable graph; request resources arrive through AgentContext."""
    builder = StateGraph(AgentState, context_schema=AgentContext)
    builder.add_node("intent_router", intent_router)
    builder.add_node("planner", plan_task)
    builder.add_node("tool_executor", tool_executor)
    builder.add_node("incident_analyzer", analyze_incident)
    builder.add_node("answer_generator", answer_generator)

    builder.add_edge(START, "intent_router")
    builder.add_edge("intent_router", "planner")
    builder.add_conditional_edges(
        "planner",
        route_after_planning,
        {
            "tool_executor": "tool_executor",
            "answer_generator": "answer_generator",
        },
    )
    builder.add_edge("incident_analyzer", "answer_generator")
    builder.add_conditional_edges(
        "tool_executor",
        route_after_execution,
        {
            "tool_executor": "tool_executor",
            "incident_analyzer": "incident_analyzer",
            "answer_generator": "answer_generator",
        },
    )
    builder.add_edge("answer_generator", END)
    return builder.compile()
