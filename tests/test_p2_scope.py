from sqlalchemy import create_engine
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from types import SimpleNamespace
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from backend.database import Base
from backend.models.agent_run import AgentRun, AgentRunService
from backend.models.team import Team, TeamMember, ServiceGrant
from backend.models.user import User
from backend.models.ticket import Ticket
from backend.models.knowledge import KnowledgeDoc
from backend.tools import create_default_registry
from backend.tools.base import ToolContext
from backend.tools.registry import ToolExecutionError
from backend.mcp.server import _call_tool


@compiles(LONGTEXT, 'sqlite')
def sqlite_longtext(element, compiler, **kwargs):
    return 'TEXT'


def test_ticket_tool_filters_unscoped_queries_and_rejects_other_service(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'p2.db'))
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        user, team, other_team = User(username='scope-user'), Team(name='scope-team'), Team(name='other-team')
        session.add_all([user, team, other_team]); session.flush()
        session.add_all([TeamMember(user_id=user.id, team_id=team.id),
                         ServiceGrant(team_id=team.id, service_name='order-service'),
                         Ticket(title='order', content='order', service_name='order-service'),
                         Ticket(title='inventory', content='inventory', service_name='inventory-service'),
                         KnowledgeDoc(team_id=team.id, title='redis order', content='redis timeout order', source='test-order'),
                         KnowledgeDoc(team_id=other_team.id, title='redis inventory', content='redis timeout inventory', source='test-inventory')])
        run = AgentRun(request_id='scope-test', user_query='tickets', creator_user_id=user.id,
                       team_id=team.id, service_scope_recorded=True)
        session.add(run); session.flush()
        session.add(AgentRunService(agent_run_id=run.id, service_name='order-service'))
        session.commit()
        registry = create_default_registry()
        context = ToolContext(session, run.id)
        output = registry.call_tool('search_tickets', {'limit': 20}, context)
        assert [item.service_name for item in output.items] == ['order-service']
        knowledge = registry.call_tool('search_knowledge', {'query': 'redis', 'limit': 5}, context)
        assert len(knowledge.items) == 1 and knowledge.items[0].title == 'redis order'
        with pytest.raises(ToolExecutionError):
            registry.call_tool('search_tickets', {'service_name': 'inventory-service'}, context)
        runtime = SimpleNamespace(session_factory=lambda: Session(engine))
        mcp_context = SimpleNamespace(request_context=SimpleNamespace(lifespan_context=runtime))
        with pytest.raises(ToolError):
            _call_tool(mcp_context, 'search_tickets', {'service_name': 'inventory-service', 'limit': 20}, run.id)
        actual = _call_tool(mcp_context, 'search_tickets', {'limit': 20}, run.id)
        assert [item.service_name for item in actual.items] == ['order-service']
    engine.dispose()
