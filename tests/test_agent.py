from datetime import timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles

from backend.agent.analyzer import IncidentAnalyzer
from backend.agent.graph import build_agent_graph
from backend.agent.planner import Planner
from backend.agent.risk import RiskChecker
from backend.agent.router import IntentRouter
from backend.database import Base
from backend.models.agent_run import AgentRun
from backend.models.approval_checkpoint import AgentApprovalCheckpoint
from backend.models.common import utc_now
from backend.models.enums import AgentRunStatus
from backend.models.tool_call import ToolCallLog
from backend.services.agent_service import AgentRunStateError, prepare_agent_run
from backend.services.approval_recovery_service import reconcile_approval_checkpoints
from backend.services.specialist_agent_service import execute_specialist_run, resume_specialist_run
from backend.tools import create_default_registry


@compiles(LONGTEXT, "sqlite")
def compile_test_longtext(element, compiler, **kwargs):
    return "TEXT"


@pytest.fixture
def session(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "approval.db"))
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        yield db
    engine.dispose()


def waiting_run(session):
    query = "模拟重启 order-service"
    run_id, request_id = prepare_agent_run(session, query)
    registry = create_default_registry()
    args = (registry, IntentRouter(None), Planner(None), IncidentAnalyzer(None), RiskChecker())
    result = execute_specialist_run(session, build_agent_graph(), *args, run_id, request_id, query)
    assert result.status == AgentRunStatus.WAITING_APPROVAL
    assert session.scalar(select(func.count()).select_from(ToolCallLog)) == 0
    return run_id, args


@pytest.mark.parametrize("action,expected_calls", [("approve", 1), ("reject", 0), ("cancel", 0)])
def test_approval_after_graph_restart_executes_at_most_once(session, action, expected_calls):
    run_id, args = waiting_run(session)
    assert reconcile_approval_checkpoints(session)["waiting"] == 1
    graph = build_agent_graph()  # No in-memory checkpoint from the original graph.
    result = resume_specialist_run(session, graph, *args, run_id, action)
    assert result.approval_status == {"approve": "approved", "reject": "rejected", "cancel": "cancelled"}[action]
    assert session.scalar(select(func.count()).select_from(ToolCallLog)) == expected_calls
    with pytest.raises(AgentRunStateError):
        resume_specialist_run(session, graph, *args, run_id, "approve")
    assert session.scalar(select(func.count()).select_from(ToolCallLog)) == expected_calls


@pytest.mark.parametrize("state,error", [("expired", "APPROVAL_CHECKPOINT_EXPIRED"),
                                        ("claimed", "APPROVAL_EXECUTION_OUTCOME_UNKNOWN")])
def test_recovery_does_not_replay_expired_or_ambiguous_approval(session, state, error):
    run_id, _ = waiting_run(session)
    checkpoint = session.get(AgentApprovalCheckpoint, run_id)
    if state == "expired":
        checkpoint.expires_at = utc_now() - timedelta(seconds=1)
    else:
        checkpoint.decision = "approve"
        session.get(AgentRun, run_id).status = AgentRunStatus.RUNNING
    session.commit()
    assert reconcile_approval_checkpoints(session)["failed"] == 1
    run = session.get(AgentRun, run_id)
    assert run.status == AgentRunStatus.FAILED and run.final_answer == error
    assert session.scalar(select(func.count()).select_from(ToolCallLog)) == 0
