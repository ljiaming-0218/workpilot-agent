"""Read-only ticket strategies backed by the Phase 2 service layer."""

from backend.models.enums import RiskLevel
from backend.schemas.ticket import TicketFilters
from backend.schemas.tool import (
    GetTicketDetailInput,
    GetTicketDetailOutput,
    SearchTicketsInput,
    SearchTicketsOutput,
)
from backend.services.ticket_service import get_ticket, list_tickets
from backend.tools.base import BaseTool, ToolContext


class SearchTicketsTool(BaseTool[SearchTicketsInput, SearchTicketsOutput]):
    name = "search_tickets"
    description = "Search support tickets by title keyword and service name."
    input_schema = SearchTicketsInput
    output_schema = SearchTicketsOutput
    risk_level = RiskLevel.LOW

    def execute(
        self, context: ToolContext, tool_input: SearchTicketsInput,
    ) -> SearchTicketsOutput:
        page = list_tickets(
            context.session,
            TicketFilters(
                keyword=tool_input.keyword,
                service_name=tool_input.service_name,
                page=1,
                page_size=tool_input.limit,
            ),
        )
        return SearchTicketsOutput(items=page.items, total=page.total)


class GetTicketDetailTool(BaseTool[GetTicketDetailInput, GetTicketDetailOutput]):
    name = "get_ticket_detail"
    description = "Get one support ticket by its database ID."
    input_schema = GetTicketDetailInput
    output_schema = GetTicketDetailOutput
    risk_level = RiskLevel.LOW

    def execute(
        self, context: ToolContext, tool_input: GetTicketDetailInput,
    ) -> GetTicketDetailOutput:
        ticket = get_ticket(context.session, tool_input.ticket_id)
        return GetTicketDetailOutput(ticket=ticket)
