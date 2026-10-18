"""Background execution wrapper with request-independent database sessions."""

import logging
from typing import Literal

from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.orm import Session, sessionmaker

from backend.agent.analyzer import IncidentAnalyzer
from backend.agent.diagnostic_agents import DiagnosticAgents
from backend.agent.planner import Planner
from backend.agent.risk import RiskChecker
from backend.agent.router import IntentRouter
from backend.schemas.agent import PlannerDecision, RoutingDecision
from backend.services.agent_service import AgentExecutionError, execute_agent_run
from backend.services.multi_agent_service import execute_multi_agent_run
from backend.services.specialist_agent_service import execute_specialist_run
from backend.services.run_event_service import RunEventBroker
from backend.tools.registry import ToolRegistry


logger = logging.getLogger(__name__)


def execute_agent_run_background(
    session_factory: sessionmaker[Session],
    graph: CompiledStateGraph,
    registry: ToolRegistry,
    intent_router: IntentRouter,
    planner: Planner,
    incident_analyzer: IncidentAnalyzer,
    risk_checker: RiskChecker,
    broker: RunEventBroker,
    run_id: int,
    request_id: str,
    query: str,
    mode: Literal["multi", "specialist", "single"],
    routing: RoutingDecision | None = None,
    planned: PlannerDecision | None = None,
    agents: DiagnosticAgents | None = None,
) -> None:
    """Finish a prepared run after its HTTP creation request has returned."""
    def publish(event_type: str, data: dict[str, object]) -> None:
        broker.publish(run_id, event_type, data)

    with session_factory() as session:
        try:
            if mode == "multi":
                if routing is None or planned is None:
                    raise AgentExecutionError("Multi-Agent scope was not prepared.")
                result = execute_multi_agent_run(
                    session, registry, risk_checker, run_id, request_id,
                    query, routing, planned, publish,
                    session_factory=session_factory,
                    agents=agents,
                )
            elif mode == "specialist":
                result = execute_specialist_run(
                    session, graph, registry, intent_router, planner,
                    incident_analyzer, risk_checker, run_id, request_id,
                    query, publish,
                )
            elif mode == "single":
                result = execute_agent_run(
                    session, graph, registry, intent_router, planner,
                    incident_analyzer, risk_checker, run_id, request_id,
                    query, publish,
                )
            else:
                raise AgentExecutionError("Unknown Agent execution mode.")
        except AgentExecutionError as exc:
            logger.warning("ASYNC_AGENT_RUN_FAILED: run_id=%s", run_id)
            broker.publish(
                run_id,
                "terminal",
                {"run_id": run_id, "status": "failed", "error": exc.code},
                terminal=True,
            )
            return
        except Exception:
            logger.exception("ASYNC_AGENT_RUN_CRASHED: run_id=%s", run_id)
            broker.publish(
                run_id,
                "terminal",
                {"run_id": run_id, "status": "failed", "error": "INTERNAL_ERROR"},
                terminal=True,
            )
            return
        broker.publish(
            run_id,
            "terminal",
            {
                "run_id": run_id,
                "status": result.status.value,
                "error": result.error,
                "result": result.model_dump(mode="json"),
            },
            terminal=True,
        )
