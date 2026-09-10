"""Controlled lookup, validation, execution, and audit logging for tools."""

import logging
from time import perf_counter_ns
from typing import Any, Iterable

from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from backend.models.enums import ToolCallStatus
from backend.models.tool_call import ToolCallLog
from backend.tools.base import BaseTool, ToolContext, ToolDefinition


logger = logging.getLogger(__name__)


class ToolRegistryError(RuntimeError):
    code = "TOOL_ERROR"


class ToolAlreadyRegisteredError(ToolRegistryError):
    code = "TOOL_ALREADY_REGISTERED"


class ToolNotFoundError(ToolRegistryError):
    code = "TOOL_NOT_FOUND"


class ToolDisabledError(ToolRegistryError):
    code = "TOOL_DISABLED"


class ToolInputValidationError(ToolRegistryError):
    code = "TOOL_INPUT_VALIDATION_ERROR"

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__("Tool input validation failed.")
        self.errors = errors


class ToolOutputValidationError(ToolRegistryError):
    code = "TOOL_OUTPUT_VALIDATION_ERROR"


class ToolExecutionError(ToolRegistryError):
    code = "TOOL_EXECUTION_ERROR"


class ToolRegistry:
    """Registry Pattern: one name maps to one immutable ToolDefinition."""

    def __init__(self, tools: Iterable[BaseTool[Any, Any]] = ()) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        for tool in tools:
            self.register_tool(tool)

    def register_tool(self, tool: BaseTool[Any, Any]) -> ToolDefinition:
        definition = tool.definition
        if definition.name in self._tools:
            raise ToolAlreadyRegisteredError(f"Tool already registered: {definition.name}")
        self._tools[definition.name] = definition
        return definition

    def get_tool(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(f"Tool not found: {name}") from exc

    def list_tools(self) -> list[ToolDefinition]:
        return [self._tools[name] for name in sorted(self._tools)]

    def call_tool(
        self, name: str, raw_input: Any, context: ToolContext,
    ) -> BaseModel:
        if context.agent_run_id is None:
            raise ToolExecutionError("Agent tool calls require an AgentRun ID.")
        definition = self.get_tool(name)
        if not definition.enabled:
            self._persist_terminal_log(
                context,
                definition,
                self._known_input_fields(definition, raw_input),
                ToolCallStatus.REJECTED,
                0,
                ToolDisabledError.code,
            )
            raise ToolDisabledError(f"Tool is disabled: {name}")

        try:
            tool_input = definition.input_schema.model_validate(raw_input)
        except ValidationError as exc:
            self._persist_terminal_log(
                context,
                definition,
                self._known_input_fields(definition, raw_input),
                ToolCallStatus.REJECTED,
                0,
                ToolInputValidationError.code,
            )
            errors = exc.errors(include_input=False, include_context=False, include_url=False)
            raise ToolInputValidationError(errors) from exc

        input_json = tool_input.model_dump(mode="json")
        started_ns = perf_counter_ns()
        call_log = ToolCallLog(
            agent_run_id=context.agent_run_id,
            tool_name=definition.name,
            input_json=input_json,
            risk_level=definition.risk_level,
            status=ToolCallStatus.RUNNING,
        )
        context.session.add(call_log)
        try:
            context.session.flush()
        except SQLAlchemyError as exc:
            context.session.rollback()
            logger.warning("TOOL_LOG_ERROR: could not start tool log (%s)", type(exc).__name__)
            raise ToolExecutionError("Could not start tool execution.") from exc

        try:
            raw_output = definition.handler(context, tool_input)
            output = definition.output_schema.model_validate(raw_output)
        except ValidationError as exc:
            latency_ms = self._latency_ms(started_ns)
            self._replace_with_failure_log(
                context, definition, input_json, latency_ms, ToolOutputValidationError.code,
            )
            raise ToolOutputValidationError("Tool output validation failed.") from exc
        except Exception as exc:
            latency_ms = self._latency_ms(started_ns)
            self._replace_with_failure_log(
                context, definition, input_json, latency_ms, type(exc).__name__,
            )
            raise ToolExecutionError("Tool execution failed.") from exc

        call_log.output_json = output.model_dump(mode="json")
        call_log.status = ToolCallStatus.COMPLETED
        call_log.latency_ms = self._latency_ms(started_ns)
        try:
            context.session.commit()
        except SQLAlchemyError as exc:
            latency_ms = self._latency_ms(started_ns)
            self._replace_with_failure_log(
                context, definition, input_json, latency_ms, type(exc).__name__,
            )
            raise ToolExecutionError("Could not commit tool result.") from exc
        return output

    @staticmethod
    def _latency_ms(started_ns: int) -> int:
        return max(0, (perf_counter_ns() - started_ns) // 1_000_000)

    @staticmethod
    def _replace_with_failure_log(
        context: ToolContext,
        definition: ToolDefinition,
        input_json: dict[str, Any],
        latency_ms: int,
        error_type: str,
    ) -> None:
        """Discard the failed transaction, then make a best-effort audit record."""
        context.session.rollback()
        ToolRegistry._persist_terminal_log(
            context,
            definition,
            input_json,
            ToolCallStatus.FAILED,
            latency_ms,
            error_type,
        )

    @staticmethod
    def _known_input_fields(
        definition: ToolDefinition, raw_input: Any,
    ) -> dict[str, Any]:
        # Unknown keys are excluded so an invalid extra field cannot leak into audit storage.
        if not isinstance(raw_input, dict):
            return {}
        return {
            name: value
            for name, value in raw_input.items()
            if name in definition.input_schema.model_fields
        }

    @staticmethod
    def _persist_terminal_log(
        context: ToolContext,
        definition: ToolDefinition,
        input_json: dict[str, Any],
        status: ToolCallStatus,
        latency_ms: int,
        error_type: str,
    ) -> None:
        terminal_log = ToolCallLog(
            agent_run_id=context.agent_run_id,
            tool_name=definition.name,
            input_json=input_json,
            risk_level=definition.risk_level,
            status=status,
            latency_ms=latency_ms,
            error_message=f"Tool {status.value}: {error_type}",
        )
        context.session.add(terminal_log)
        try:
            context.session.commit()
        except SQLAlchemyError as log_exc:
            context.session.rollback()
            logger.warning("TOOL_LOG_ERROR: could not persist terminal state (%s)", type(log_exc).__name__)
