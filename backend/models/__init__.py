"""Import every ORM model so Base.metadata contains all six tables."""

from backend.models.agent_run import AgentRun
from backend.models.error_log import ErrorLog
from backend.models.knowledge import KnowledgeDoc
from backend.models.ticket import Ticket
from backend.models.tool_call import ToolCallLog
from backend.models.user import User

__all__ = ["User", "Ticket", "ErrorLog", "KnowledgeDoc", "AgentRun", "ToolCallLog"]
