"""FastAPI application entry point and database resource lifecycle."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.orm import sessionmaker
from sqlalchemy import inspect

from backend.agent import build_agent_graph
from backend.agent.analyzer import IncidentAnalyzer
from backend.agent.diagnostic_agents import DiagnosticAgents
from backend.agent.router import IntentRouter
from backend.agent.planner import Planner
from backend.agent.risk import RiskChecker
from backend.config import Settings
from backend.database import build_engine
from backend.errors import register_error_handlers
from backend.mcp import WorkPilotMCPClient
from backend.routers.alert_router import router as alert_router
from backend.routers.auth_router import router as auth_router
from backend.routers.agent_router import router as agent_router
from backend.routers.health_router import router as health_router
from backend.routers.ingestion_router import router as ingestion_router
from backend.routers.investigation_router import router as investigation_router
from backend.routers.knowledge_router import router as knowledge_router
from backend.routers.log_router import router as log_router
from backend.routers.ticket_router import router as ticket_router
from backend.routers.trace_router import router as trace_router
from backend.services.llm_service import LLMService
from backend.services.approval_recovery_service import reconcile_approval_checkpoints
from backend.services.run_event_service import RunEventBroker
from backend.services.text2sql_service import Text2SQLService
from backend.tools import create_default_registry


FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"


def _mcp_database_environment(settings: Settings) -> dict[str, str]:
    """Pass only database configuration to the isolated MCP subprocess."""
    environment = {
        "MYSQL_HOST": settings.mysql_host,
        "MYSQL_PORT": str(settings.mysql_port),
        "MYSQL_USER": settings.mysql_user,
        "MYSQL_PASSWORD": settings.mysql_password.get_secret_value(),
        "MYSQL_DATABASE": settings.mysql_database,
        "MYSQL_CONNECT_TIMEOUT": str(settings.mysql_connect_timeout),
    }
    if settings.mysql_ssl_ca is not None:
        environment["MYSQL_SSL_CA"] = settings.mysql_ssl_ca
    return environment


def create_app(settings: Settings | None = None, *, reconcile_approvals: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime_settings = settings if settings is not None else Settings()
        engine = build_engine(runtime_settings)
        llm_service = None
        mcp_client = WorkPilotMCPClient(
            server_environment=_mcp_database_environment(runtime_settings)
        )
        try:
            app.state.settings = runtime_settings
            inspector = inspect(engine)
            job_table = inspector.has_table("durable_jobs")
            attempt_table = inspector.has_table("durable_job_attempts")
            if job_table != attempt_table or (runtime_settings.p3_durable_enabled and not job_table):
                raise RuntimeError("P3 tables are missing or incomplete; run scripts.migrate_p3 first")
            app.state.p3_schema_present = job_table and attempt_table
            app.state.p4_schema_present = app.state.p3_schema_present and all(inspector.has_table(name) for name in (
                "incident_submissions", "investigation_reviews", "investigation_notes",
            ))
            if (
                runtime_settings.llm_api_key is not None
                and runtime_settings.llm_model is not None
            ):
                llm_service = LLMService(runtime_settings)
            text2sql_service = (
                Text2SQLService(llm_service) if llm_service is not None else None
            )
            mcp_client.connect()
            app.state.session_factory = sessionmaker(bind=engine, autoflush=False)
            if reconcile_approvals:
                with app.state.session_factory() as recovery_session:
                    reconcile_approval_checkpoints(recovery_session)
            app.state.mcp_client = mcp_client
            app.state.tool_registry = create_default_registry(
                text2sql_service,
                mcp_client,
            )
            app.state.intent_router = IntentRouter(llm_service)
            app.state.planner = Planner(llm_service, max_steps=5)
            app.state.incident_analyzer = IncidentAnalyzer(llm_service)
            app.state.diagnostic_agents = (
                DiagnosticAgents(llm_service.with_request_limits(timeout=30, max_retries=0))
                if llm_service is not None else None
            )
            app.state.risk_checker = RiskChecker()
            app.state.run_event_broker = RunEventBroker()
            app.state.agent_checkpointer = InMemorySaver()
            app.state.agent_graph = build_agent_graph(app.state.agent_checkpointer)
            yield
        finally:
            try:
                mcp_client.close()
            finally:
                try:
                    if llm_service is not None:
                        llm_service.close()
                finally:
                    engine.dispose()

    application = FastAPI(title="WorkPilot Agent", version="0.1.0", lifespan=lifespan)
    register_error_handlers(application)
    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(ticket_router)
    application.include_router(log_router)
    application.include_router(knowledge_router)
    application.include_router(ingestion_router)
    application.include_router(alert_router)
    application.include_router(investigation_router)
    application.include_router(agent_router)
    application.include_router(trace_router)

    @application.get("/", include_in_schema=False)
    def console_redirect() -> RedirectResponse:
        return RedirectResponse(url="/console/", status_code=307)

    application.mount(
        "/console",
        StaticFiles(directory=FRONTEND_DIR, html=True),
        name="agent-console",
    )
    return application


app = create_app()
