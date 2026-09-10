"""Adapt existing read-only WorkPilot tools to MCP calls."""

from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.tools.base import BaseTool, ToolContext
from backend.tools.knowledge_tools import SearchKnowledgeTool
from backend.tools.log_tools import QueryErrorLogsTool
from backend.tools.ticket_tools import SearchTicketsTool


MCP_TOOLS: dict[str, BaseTool[Any, Any]] = {
    tool.name: tool
    for tool in (
        SearchTicketsTool(),
        QueryErrorLogsTool(),
        SearchKnowledgeTool(),
    )
}
MCP_TOOL_NAMES = frozenset(MCP_TOOLS)


def execute_mcp_tool(
    session: Session,
    name: str,
    arguments: dict[str, Any],
) -> BaseModel:
    """Validate and execute one exposed read-only tool without Agent audit state."""
    try:
        tool = MCP_TOOLS[name]
    except KeyError as exc:
        raise ValueError(f"MCP tool is not exposed: {name}") from exc

    definition = tool.definition
    tool_input = definition.input_schema.model_validate(arguments)
    raw_output = definition.handler(
        ToolContext(session=session, agent_run_id=None),
        tool_input,
    )
    return definition.output_schema.model_validate(raw_output)
