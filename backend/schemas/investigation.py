"""Bounded human input; execution state and provenance are never editable here."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReviewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_version: int = Field(ge=0)
    owner_user_id: int | None = Field(default=None, ge=1)
    handling_status: Literal["open", "in_progress", "resolved"] = "open"
    feedback: Literal["unreviewed", "supported", "needs_correction", "inconclusive"] = "unreviewed"
    note: str = Field(default="", max_length=4000)
