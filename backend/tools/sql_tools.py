"""Guarded natural-language database query tool."""

from backend.models.enums import RiskLevel
from backend.schemas.tool import QueryDatabaseInput, QueryDatabaseOutput
from backend.services.text2sql_service import Text2SQLService
from backend.tools.base import BaseTool, ToolContext


class QueryDatabaseTool(BaseTool[QueryDatabaseInput, QueryDatabaseOutput]):
    name = "query_database"
    description = "Answer a data question with one guarded read-only MySQL query."
    input_schema = QueryDatabaseInput
    output_schema = QueryDatabaseOutput
    risk_level = RiskLevel.LOW

    def __init__(self, service: Text2SQLService) -> None:
        self._service = service

    def execute(
        self, context: ToolContext, tool_input: QueryDatabaseInput,
    ) -> QueryDatabaseOutput:
        result = self._service.query(context.session, tool_input.question)
        return QueryDatabaseOutput.model_validate(result.model_dump())
