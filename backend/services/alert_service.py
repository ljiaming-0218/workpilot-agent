"""Translate validated alerts into the existing controlled Agent workflow."""

from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.orm import Session

from backend.agent.analyzer import IncidentAnalyzer
from backend.agent.planner import Planner
from backend.agent.risk import RiskChecker
from backend.agent.router import IntentRouter
from backend.schemas.alert import IncidentAlertRequest, IncidentAlertResult
from backend.services.agent_service import run_agent
from backend.tools.registry import ToolRegistry


def process_incident_alert(
    session: Session,
    graph: CompiledStateGraph,
    registry: ToolRegistry,
    intent_router: IntentRouter,
    planner: Planner,
    incident_analyzer: IncidentAnalyzer,
    risk_checker: RiskChecker,
    payload: IncidentAlertRequest,
) -> IncidentAlertResult:
    """Run one alert through the same safety, audit, and trace boundaries as UI tasks."""
    query = _build_incident_query(payload)
    result = run_agent(
        session,
        graph,
        registry,
        intent_router,
        planner,
        incident_analyzer,
        risk_checker,
        query,
    )
    return IncidentAlertResult(
        source=payload.source,
        event_id=payload.event_id,
        agent_run=result,
    )


def _build_incident_query(payload: IncidentAlertRequest) -> str:
    error_clause = f"，错误类型 {payload.error_type}" if payload.error_type else ""
    return (
        f"分析 service: {payload.service_name} 最近 {payload.window_minutes} 分钟的故障"
        f"{error_clause}，告警名称：{payload.alert_name}，严重级别：{payload.severity}；"
        f"结合日志、工单和知识手册给出证据与只读排查方案。"
        f"告警引用：{payload.source}/{payload.event_id}。"
    )
