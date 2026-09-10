"""Standalone WorkPilot MCP server over the stdio transport."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.config import Settings
from backend.database import build_engine
from backend.mcp.tools import execute_mcp_tool
from backend.schemas.tool import (
    QueryErrorLogsOutput,
    SearchKnowledgeOutput,
    SearchTicketsOutput,
)


@dataclass(frozen=True, slots=True)
class MCPRuntime:
    engine: Engine
    session_factory: sessionmaker[Session]


@asynccontextmanager
async def server_lifespan(server: MCPServer[MCPRuntime]) -> AsyncIterator[MCPRuntime]:
    """Share one Engine while keeping every MCP call on its own Session."""
    engine = build_engine(Settings())
    runtime = MCPRuntime(
        engine=engine,
        session_factory=sessionmaker(bind=engine, autoflush=False),
    )
    try:
        yield runtime
    finally:
        engine.dispose()


mcp = MCPServer[MCPRuntime](
    "workpilot-agent",
    version="0.1.0",
    instructions="Read-only access to WorkPilot tickets, logs, and knowledge.",
    lifespan=server_lifespan,
)


def _call_tool(
    context: Context[MCPRuntime],
    name: str,
    arguments: dict[str, Any],
) -> Any:
    runtime = context.request_context.lifespan_context
    with runtime.session_factory() as session:
        try:
            return execute_mcp_tool(session, name, arguments)
        except SQLAlchemyError as exc:
            session.rollback()
            raise ToolError("WorkPilot database operation failed.") from exc


@mcp.tool()
def search_tickets(
    context: Context[MCPRuntime],
    keyword: Annotated[str | None, Field(min_length=1, max_length=100)] = None,
    service_name: Annotated[str | None, Field(min_length=1, max_length=128)] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> SearchTicketsOutput:
    """Search WorkPilot support tickets by title keyword and service name."""
    return SearchTicketsOutput.model_validate(
        _call_tool(
            context,
            "search_tickets",
            {"keyword": keyword, "service_name": service_name, "limit": limit},
        )
    )


@mcp.tool()
def query_error_logs(
    context: Context[MCPRuntime],
    service_name: Annotated[str, Field(min_length=1, max_length=128)],
    minutes: Annotated[int, Field(ge=1, le=10_080)] = 60,
    error_type: Annotated[str | None, Field(min_length=1, max_length=128)] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> QueryErrorLogsOutput:
    """Query recent WorkPilot error logs for one service."""
    return QueryErrorLogsOutput.model_validate(
        _call_tool(
            context,
            "query_error_logs",
            {
                "service_name": service_name,
                "minutes": minutes,
                "error_type": error_type,
                "limit": limit,
            },
        )
    )


@mcp.tool()
def search_knowledge(
    context: Context[MCPRuntime],
    query: Annotated[str, Field(min_length=1, max_length=500)],
    category: Annotated[str | None, Field(min_length=1, max_length=128)] = None,
    limit: Annotated[int, Field(ge=1, le=20)] = 5,
) -> SearchKnowledgeOutput:
    """Search WorkPilot knowledge documents using BM25 ranking."""
    return SearchKnowledgeOutput.model_validate(
        _call_tool(
            context,
            "search_knowledge",
            {"query": query, "category": category, "limit": limit},
        )
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
