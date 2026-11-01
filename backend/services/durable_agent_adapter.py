"""Execute an isolated P3 attempt through the existing Agent components."""

from backend.services.access_service import resolve_run_scope
from backend.services.durable_attempt import AttemptExecution
from backend.services.multi_agent_service import (
    MultiAgentScopeError, execute_multi_agent_run, prepare_multi_scope,
)
from backend.services.specialist_agent_service import execute_specialist_run


def execute_attempt(state, execution: AttemptExecution) -> None:
    """Use only the private run ID; enforce read-only mode for retriable work."""
    with state.session_factory() as session:
        resolve_run_scope(session, execution.run_id)
        try:
            routing, planned = prepare_multi_scope(
                execution.query, state.intent_router, state.planner,
            )
        except MultiAgentScopeError:
            if execution.retry_safe:
                # A retriable claim may never fall back to a potentially
                # side-effecting specialist after re-routing.
                raise ValueError("Retriable task is not a read-only multi-agent investigation")
            execute_specialist_run(
                session, state.agent_graph, state.tool_registry, state.intent_router,
                state.planner, state.incident_analyzer, state.risk_checker,
                execution.run_id, execution.request_id, execution.query,
            )
        else:
            execute_multi_agent_run(
                session, state.tool_registry, state.risk_checker,
                execution.run_id, execution.request_id, execution.query, routing, planned,
                session_factory=state.session_factory,
                agents=getattr(state, "diagnostic_agents", None),
            )
