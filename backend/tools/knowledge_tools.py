"""Read-only BM25 knowledge retrieval tool."""

from backend.models.enums import RiskLevel
from backend.schemas.tool import SearchKnowledgeInput, SearchKnowledgeOutput
from backend.services.retrieval_service import search_knowledge
from backend.tools.base import BaseTool, ToolContext


class SearchKnowledgeTool(BaseTool[SearchKnowledgeInput, SearchKnowledgeOutput]):
    name = "search_knowledge"
    description = "Search internal knowledge documents with BM25 keyword ranking."
    input_schema = SearchKnowledgeInput
    output_schema = SearchKnowledgeOutput
    risk_level = RiskLevel.LOW

    def execute(
        self, context: ToolContext, tool_input: SearchKnowledgeInput,
    ) -> SearchKnowledgeOutput:
        items, total = search_knowledge(
            context.session,
            tool_input.query,
            category=tool_input.category,
            limit=tool_input.limit,
        )
        return SearchKnowledgeOutput(items=items, total=total)
