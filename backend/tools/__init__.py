"""Default Tool Registry assembly."""

from typing import TYPE_CHECKING

from backend.tools.log_tools import QueryErrorLogsTool
from backend.tools.knowledge_tools import SearchKnowledgeTool
from backend.tools.mcp_proxy import MCPToolCaller, create_mcp_proxy_tools
from backend.tools.registry import ToolRegistry
from backend.tools.simulation_tools import SimulateHighRiskOperationTool
from backend.tools.sql_tools import QueryDatabaseTool
from backend.tools.ticket_tools import GetTicketDetailTool, SearchTicketsTool

if TYPE_CHECKING:
    from backend.services.text2sql_service import Text2SQLService


def create_default_registry(
    text2sql_service: "Text2SQLService | None" = None,
    mcp_client: MCPToolCaller | None = None,
) -> ToolRegistry:
    read_tools = (
        create_mcp_proxy_tools(mcp_client)
        if mcp_client is not None
        else [SearchTicketsTool(), QueryErrorLogsTool(), SearchKnowledgeTool()]
    )
    tools = [
        *read_tools,
        GetTicketDetailTool(),
        SimulateHighRiskOperationTool(),
    ]
    if text2sql_service is not None:
        tools.append(QueryDatabaseTool(text2sql_service))
    return ToolRegistry(tools)


__all__ = ["create_default_registry"]
