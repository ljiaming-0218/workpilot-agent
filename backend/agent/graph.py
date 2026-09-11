"""Build the bounded Agent graph with Phase 11 approval interrupts."""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from backend.agent.nodes import (
    analyze_incident,
    approval_gate,
    answer_generator,
    check_risk,
    intent_router,
    plan_task,
    route_after_approval,
    route_after_execution,
    route_after_planning,
    route_after_risk,
    tool_executor,
)
from backend.agent.state import AgentContext, AgentState
from backend.agent.trace import trace_node


def build_agent_graph(checkpointer: InMemorySaver | None = None):
    """Compile a reusable graph; request resources arrive through AgentContext."""
    builder = StateGraph(AgentState, context_schema=AgentContext)
    builder.add_node("intent_router", trace_node("intent_router", intent_router))
    builder.add_node("planner", trace_node("planner", plan_task))
    builder.add_node("risk_checker", trace_node("risk_checker", check_risk))
    builder.add_node("approval_gate", trace_node("approval_gate", approval_gate))
    builder.add_node("tool_executor", trace_node("tool_executor", tool_executor))
    builder.add_node(
        "incident_analyzer",
        trace_node("incident_analyzer", analyze_incident),
    )
    builder.add_node(
        "answer_generator",
        trace_node("answer_generator", answer_generator),
    )

    builder.add_edge(START, "intent_router")
    builder.add_edge("intent_router", "planner")
    builder.add_conditional_edges(
        "planner",
        route_after_planning,
        {
            "risk_checker": "risk_checker",
            "answer_generator": "answer_generator",
        },
    )
    builder.add_conditional_edges(
        "risk_checker",
        route_after_risk,
        {
            "approval_gate": "approval_gate",
            "tool_executor": "tool_executor",
        },
    )
    builder.add_conditional_edges(
        "approval_gate",
        route_after_approval,
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
    active_checkpointer = checkpointer if checkpointer is not None else InMemorySaver()
    return builder.compile(checkpointer=active_checkpointer)
