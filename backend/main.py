"""FastAPI application entry point and database resource lifecycle."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.orm import sessionmaker

from backend.agent import build_agent_graph
from backend.agent.analyzer import IncidentAnalyzer
from backend.agent.router import IntentRouter
from backend.agent.planner import Planner
from backend.agent.risk import RiskChecker
from backend.config import Settings
from backend.database import build_engine
from backend.errors import register_error_handlers
from backend.mcp import WorkPilotMCPClient
from backend.routers.alert_router import router as alert_router
from backend.routers.agent_router import router as agent_router
from backend.routers.health_router import router as health_router
from backend.routers.ingestion_router import router as ingestion_router
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


def create_app(settings: Settings | None = None) -> FastAPI:
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
    application.include_router(ticket_router)
    application.include_router(log_router)
    application.include_router(knowledge_router)
    application.include_router(ingestion_router)
    application.include_router(alert_router)
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
