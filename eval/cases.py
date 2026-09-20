"""Strict schemas and loaders for deterministic evaluation datasets."""

import json
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from backend.models.enums import AgentRunStatus, RiskLevel
from backend.schemas.agent import AgentIntent, AgentToolName


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=100)


class RoutingCase(EvalCase):
    query: str = Field(min_length=1, max_length=2_000)
    expected_intent: AgentIntent
    expected_source: Literal["rule", "fallback"]


class ToolCase(EvalCase):
    query: str = Field(min_length=1, max_length=2_000)
    intent: AgentIntent
    expected_tool: AgentToolName | Literal[""]
    expected_inputs: dict[str, Any]


class SQLGuardCase(EvalCase):
    sql: str = Field(min_length=1, max_length=20_000)
    expected_allowed: bool
    expected_error_code: Literal["INVALID_SQL", "UNSAFE_SQL"] | None = None
    expected_tables: list[str] = Field(default_factory=list)
    expected_limit: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def validate_expectation(self) -> "SQLGuardCase":
        if self.expected_allowed and self.expected_error_code is not None:
            raise ValueError("Allowed SQL cannot expect an error code.")
        if not self.expected_allowed and self.expected_error_code is None:
            raise ValueError("Rejected SQL must declare its expected error code.")
        return self


class RetrievalDocumentCase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)
    category: str | None = Field(default=None, max_length=64)
    source: str | None = Field(default=None, max_length=512)


class RetrievalCase(EvalCase):
    query: str = Field(min_length=1, max_length=500)
    relevant_document_ids: list[int] = Field(min_length=1)
    k: int = Field(default=5, ge=1, le=20)


class RetrievalSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documents: list[RetrievalDocumentCase] = Field(min_length=1)
    cases: list[RetrievalCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_document_references(self) -> "RetrievalSuite":
        document_ids = [document.id for document in self.documents]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("Retrieval document IDs must be unique.")
        available = set(document_ids)
        for case in self.cases:
            missing = set(case.relevant_document_ids) - available
            if missing:
                raise ValueError(
                    f"Retrieval case {case.id} references unknown documents: "
                    + ", ".join(str(item) for item in sorted(missing))
                )
        return self


class LiveAgentCase(EvalCase):
    query: str = Field(min_length=1, max_length=2_000)
    expected_intent: AgentIntent
    expected_status: AgentRunStatus
    expected_plan_tools: list[AgentToolName] = Field(max_length=5)
    expected_executed_tools: list[AgentToolName] = Field(max_length=5)
    expected_risk_level: RiskLevel
    expected_requires_approval: bool
    minimum_evidence: int = Field(default=0, ge=0, le=5)
    analysis_required: bool = False
    required_answer_terms: list[str] = Field(default_factory=list)
    required_output_keys: dict[AgentToolName, list[str]] = Field(default_factory=dict)


ModelT = TypeVar("ModelT", bound=BaseModel)


def load_case_list(path: Path, model: type[ModelT]) -> list[ModelT]:
    """Load a JSON array and validate every case before evaluation starts."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = TypeAdapter(list[model]).validate_python(payload)
    case_ids = [case.id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError(f"Evaluation case IDs must be unique in {path.name}.")
    return cases


def load_retrieval_suite(path: Path) -> RetrievalSuite:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return RetrievalSuite.model_validate(payload)
