"""FastAPI application entry point and database resource lifecycle."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from backend.agent import build_agent_graph
from backend.agent.router import IntentRouter
from backend.config import Settings
from backend.database import build_engine
from backend.errors import register_error_handlers
from backend.routers.agent_router import router as agent_router
from backend.routers.health_router import router as health_router
from backend.routers.log_router import router as log_router
from backend.routers.ticket_router import router as ticket_router
from backend.services.llm_service import LLMService
from backend.services.text2sql_service import Text2SQLService
from backend.tools import create_default_registry


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime_settings = settings if settings is not None else Settings()
        engine = build_engine(runtime_settings)
        llm_service = None
        if runtime_settings.llm_api_key is not None and runtime_settings.llm_model is not None:
            llm_service = LLMService(runtime_settings)
        text2sql_service = Text2SQLService(llm_service) if llm_service is not None else None
        app.state.session_factory = sessionmaker(bind=engine, autoflush=False)
        app.state.tool_registry = create_default_registry(text2sql_service)
        app.state.intent_router = IntentRouter(llm_service)
        app.state.agent_graph = build_agent_graph()
        try:
            yield
        finally:
            if llm_service is not None:
                llm_service.close()
            engine.dispose()

    application = FastAPI(title="WorkPilot Agent", version="0.1.0", lifespan=lifespan)
    register_error_handlers(application)
    application.include_router(health_router)
    application.include_router(ticket_router)
    application.include_router(log_router)
    application.include_router(agent_router)
    return application


app = create_app()
