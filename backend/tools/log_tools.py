"""Read-only log query strategy backed by the Phase 2 service layer."""

from datetime import datetime, timedelta, timezone

from backend.models.enums import RiskLevel
from backend.schemas.log import LogFilters
from backend.schemas.tool import QueryErrorLogsInput, QueryErrorLogsOutput
from backend.services.log_service import list_logs
from backend.tools.base import BaseTool, ToolContext


class QueryErrorLogsTool(BaseTool[QueryErrorLogsInput, QueryErrorLogsOutput]):
    name = "query_error_logs"
    description = "Query recent error logs for one service."
    input_schema = QueryErrorLogsInput
    output_schema = QueryErrorLogsOutput
    risk_level = RiskLevel.LOW

    def execute(
        self, context: ToolContext, tool_input: QueryErrorLogsInput,
    ) -> QueryErrorLogsOutput:
        now = datetime.now(timezone.utc)
        page = list_logs(
            context.session,
            LogFilters(
                service_name=tool_input.service_name,
                error_type=tool_input.error_type,
                created_from=now - timedelta(minutes=tool_input.minutes),
                created_to=now,
                page=1,
                page_size=tool_input.limit,
            ),
        )
        return QueryErrorLogsOutput(items=page.items, total=page.total)
