"""Validated output contract for deterministic Agent risk checks."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.models.enums import RiskLevel


class RiskAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_level: RiskLevel
    requires_approval: bool
    reasons: list[str] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def approval_matches_level(self) -> Self:
        if self.requires_approval != (self.risk_level == RiskLevel.HIGH):
            raise ValueError(
                "requires_approval must be true exactly when risk_level is HIGH."
            )
        return self
