"""Default Tool Registry assembly."""

from typing import TYPE_CHECKING

from backend.tools.log_tools import QueryErrorLogsTool
from backend.tools.knowledge_tools import SearchKnowledgeTool
from backend.tools.registry import ToolRegistry
from backend.tools.simulation_tools import SimulateHighRiskOperationTool
from backend.tools.sql_tools import QueryDatabaseTool
from backend.tools.ticket_tools import GetTicketDetailTool, SearchTicketsTool

if TYPE_CHECKING:
    from backend.services.text2sql_service import Text2SQLService


def create_default_registry(
    text2sql_service: "Text2SQLService | None" = None,
) -> ToolRegistry:
    tools = [
        SearchTicketsTool(),
        GetTicketDetailTool(),
        QueryErrorLogsTool(),
        SearchKnowledgeTool(),
        SimulateHighRiskOperationTool(),
    ]
    if text2sql_service is not None:
        tools.append(QueryDatabaseTool(text2sql_service))
    return ToolRegistry(tools)


__all__ = ["create_default_registry"]
