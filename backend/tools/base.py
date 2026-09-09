"""Tool contract shared by the registry and concrete tool strategies."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
import re
from typing import Callable, Generic, TypeVar, cast

from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.models.enums import RiskLevel


InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)
ToolHandler = Callable[["ToolContext", BaseModel], BaseModel]
TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,127}$")


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Resources and trace identity available to one tool invocation."""

    session: Session
    agent_run_id: int


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Discoverable metadata plus the validated execution strategy."""

    name: str
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    risk_level: RiskLevel
    handler: ToolHandler
    enabled: bool = True

    def __post_init__(self) -> None:
        if TOOL_NAME_PATTERN.fullmatch(self.name) is None:
            raise ValueError("Tool name must use lowercase letters, digits, and underscores.")
        if not self.description.strip():
            raise ValueError("Tool description must not be empty.")
        if not isinstance(self.risk_level, RiskLevel):
            raise ValueError("Tool risk_level must be a RiskLevel value.")


class BaseTool(ABC, Generic[InputT, OutputT]):
    """Strategy interface: every tool supplies metadata and one execute method."""

    name: str
    description: str
    input_schema: type[InputT]
    output_schema: type[OutputT]
    risk_level: RiskLevel
    enabled: bool = True

    @abstractmethod
    def execute(self, context: ToolContext, tool_input: InputT) -> OutputT:
        """Execute validated input and return data matching output_schema."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
            risk_level=self.risk_level,
            handler=cast(ToolHandler, self.execute),
            enabled=self.enabled,
        )
