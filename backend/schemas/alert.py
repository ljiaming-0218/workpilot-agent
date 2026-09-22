"""Validated contracts for one external incident alert."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.agent import AgentResult


class IncidentAlertRequest(BaseModel):
    """Allow only bounded routing metadata, not arbitrary alert payloads."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    event_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9._:-]+$")
    service_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9._-]+$",
    )
    alert_name: str = Field(min_length=1, max_length=160, pattern=r"^[^\r\n]+$")
    severity: Literal["warning", "critical"]
    error_type: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9._-]+$",
    )
    window_minutes: int = Field(default=60, ge=5, le=10_080)


class IncidentAlertResult(BaseModel):
    source: str
    event_id: str
    agent_run: AgentResult
