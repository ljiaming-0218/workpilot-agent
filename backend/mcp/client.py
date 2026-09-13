"""Synchronous facade over one persistent async MCP stdio client."""

import asyncio
from collections.abc import Mapping
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
import sys
from threading import Event, Thread
from typing import Any, Literal

from mcp import Client, StdioServerParameters

from backend.mcp.tools import MCP_TOOL_NAMES


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLIENT_START_TIMEOUT_SECONDS = 45
CLIENT_CALL_TIMEOUT_SECONDS = 30
CLIENT_STOP_TIMEOUT_SECONDS = 10


class MCPClientError(RuntimeError):
    code = "MCP_CLIENT_ERROR"


@dataclass(frozen=True, slots=True)
class _ClientRequest:
    operation: Literal["list_tools", "call_tool"]
    future: Future[Any]
    name: str | None = None
    arguments: dict[str, Any] | None = None


class WorkPilotMCPClient:
    """Own one MCP subprocess while serving synchronous Agent tool calls."""

    def __init__(
        self,
        python_executable: str = sys.executable,
        project_root: Path = PROJECT_ROOT,
        server_environment: Mapping[str, str] | None = None,
    ) -> None:
        self._server = StdioServerParameters(
            command=python_executable,
            args=["-m", "backend.mcp.server"],
            cwd=project_root,
            env=dict(server_environment) if server_environment is not None else None,
        )
        self._requests: Queue[_ClientRequest | None] = Queue()
        self._ready = Event()
        self._thread: Thread | None = None
        self._startup_error: BaseException | None = None

    def connect(self) -> None:
        if self._thread is not None:
            raise MCPClientError("MCP client is already connected.")

        self._ready.clear()
        self._startup_error = None
        thread = Thread(
            target=self._run_worker,
            name="workpilot-mcp-client",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        if not self._ready.wait(CLIENT_START_TIMEOUT_SECONDS):
            self.close()
            raise MCPClientError("MCP client startup timed out.")
        if self._startup_error is not None:
            error = self._startup_error
            self.close()
            raise MCPClientError("MCP client failed to connect.") from error

        try:
            available = set(self.list_tools())
            missing = MCP_TOOL_NAMES - available
            if not missing:
                return
        except Exception:
            self.close()
            raise
        self.close()
        raise MCPClientError(
            "MCP server is missing tools: " + ", ".join(sorted(missing))
        )

    def list_tools(self) -> list[str]:
        try:
            result = self._submit(_ClientRequest("list_tools", Future()))
        except MCPClientError:
            raise
        except Exception as exc:
            raise MCPClientError("MCP tools/list failed.") from exc
        return sorted(tool.name for tool in result.tools)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in MCP_TOOL_NAMES:
            raise MCPClientError(f"MCP tool is not allowed: {name}")
        try:
            result = self._submit(
                _ClientRequest(
                    "call_tool",
                    Future(),
                    name=name,
                    arguments=arguments,
                )
            )
        except MCPClientError:
            raise
        except Exception as exc:
            raise MCPClientError(f"MCP tools/call failed: {name}") from exc
        if result.is_error:
            raise MCPClientError(f"MCP tool failed: {name}")
        if not isinstance(result.structured_content, dict):
            raise MCPClientError(f"MCP tool returned no structured output: {name}")
        return result.structured_content

    def close(self) -> None:
        thread = self._thread
        self._thread = None
        if thread is None:
            return
        if thread.is_alive():
            self._requests.put(None)
            thread.join(CLIENT_STOP_TIMEOUT_SECONDS)
        if thread.is_alive():
            raise MCPClientError("MCP client did not stop cleanly.")

    def _submit(self, request: _ClientRequest) -> Any:
        thread = self._thread
        if thread is None or not thread.is_alive():
            raise MCPClientError("MCP client is not connected.")
        self._requests.put(request)
        try:
            return request.future.result(CLIENT_CALL_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            raise MCPClientError("MCP request timed out.") from exc

    def _run_worker(self) -> None:
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        try:
            async with Client(
                self._server,
                read_timeout_seconds=CLIENT_CALL_TIMEOUT_SECONDS,
            ) as client:
                self._ready.set()
                while True:
                    request = await asyncio.to_thread(self._requests.get)
                    if request is None:
                        return
                    try:
                        if request.operation == "list_tools":
                            result = await client.list_tools()
                        else:
                            result = await client.call_tool(
                                request.name or "",
                                request.arguments,
                            )
                    except BaseException as exc:
                        request.future.set_exception(exc)
                    else:
                        request.future.set_result(result)
        except BaseException as exc:
            if not self._ready.is_set():
                self._startup_error = exc
                self._ready.set()
            self._fail_pending_requests(exc)

    def _fail_pending_requests(self, error: BaseException) -> None:
        while not self._requests.empty():
            request = self._requests.get_nowait()
            if request is not None and not request.future.done():
                request.future.set_exception(error)
