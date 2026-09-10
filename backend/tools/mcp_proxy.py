"""Tool Registry proxies that delegate read-only execution through MCP."""

from typing import Any, Protocol

from pydantic import BaseModel

from backend.tools.base import BaseTool, ToolContext
from backend.tools.knowledge_tools import SearchKnowledgeTool
from backend.tools.log_tools import QueryErrorLogsTool
from backend.tools.ticket_tools import SearchTicketsTool


class MCPToolCaller(Protocol):
    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class MCPProxyTool(BaseTool[BaseModel, BaseModel]):
    """Preserve local tool contracts while changing the execution transport."""

    def __init__(self, source: BaseTool[Any, Any], client: MCPToolCaller) -> None:
        self.name = source.name
        self.description = source.description
        self.input_schema = source.input_schema
        self.output_schema = source.output_schema
        self.risk_level = source.risk_level
        self.enabled = source.enabled
        self._client = client

    def execute(self, context: ToolContext, tool_input: BaseModel) -> BaseModel:
        raw_output = self._client.call_tool(
            self.name,
            tool_input.model_dump(mode="json"),
        )
        return self.output_schema.model_validate(raw_output)


def create_mcp_proxy_tools(client: MCPToolCaller) -> list[MCPProxyTool]:
    return [
        MCPProxyTool(SearchTicketsTool(), client),
        MCPProxyTool(QueryErrorLogsTool(), client),
        MCPProxyTool(SearchKnowledgeTool(), client),
    ]
