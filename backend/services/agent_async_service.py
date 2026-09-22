"""Background execution wrapper with request-independent database sessions."""

import logging

from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.orm import Session, sessionmaker

from backend.agent.analyzer import IncidentAnalyzer
from backend.agent.planner import Planner
from backend.agent.risk import RiskChecker
from backend.agent.router import IntentRouter
from backend.services.agent_service import AgentExecutionError, execute_agent_run
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
) -> None:
    """Finish a prepared run after its HTTP creation request has returned."""
    def publish(event_type: str, data: dict[str, object]) -> None:
        broker.publish(run_id, event_type, data)

    with session_factory() as session:
        try:
            result = execute_agent_run(
                session,
                graph,
                registry,
                intent_router,
                planner,
                incident_analyzer,
                risk_checker,
                run_id,
                request_id,
                query,
                publish,
            )
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
