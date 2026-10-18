from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.mcp.client import MCPClientError, WorkPilotMCPClient


@pytest.mark.parametrize("is_error,content", [(True, {}), (False, None), (False, [])])
def test_mcp_failure_or_missing_structured_output_is_not_empty_success(is_error, content):
    client = WorkPilotMCPClient()
    client._submit = MagicMock(return_value=SimpleNamespace(is_error=is_error, structured_content=content))
    with pytest.raises(MCPClientError):
        client.call_tool("query_error_logs", {"service_name": "order-service"})


def test_mcp_empty_success_and_unknown_tool_have_distinct_outcomes():
    client = WorkPilotMCPClient()
    client._submit = MagicMock(return_value=SimpleNamespace(is_error=False, structured_content={"items": [], "total": 0}))
    assert client.call_tool("query_error_logs", {"service_name": "order-service"}) == {"items": [], "total": 0}
    client._submit.reset_mock()
    with pytest.raises(MCPClientError, match="not allowed"):
        client.call_tool("unregistered_operation", {})
    client._submit.assert_not_called()
