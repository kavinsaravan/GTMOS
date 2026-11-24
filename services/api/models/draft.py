"""Typed LLM outputs for outreach drafting (writer) and review (Reviewer)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WriterOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=3, max_length=120)
    body: str = Field(min_length=40, max_length=1800)
    fact_ids: list[int] = Field(min_length=1, max_length=5)


class ReviewRemoval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0, le=200)
    reason: str = Field(default="", max_length=200)


class ReviewOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    remove: list[ReviewRemoval] = Field(default_factory=list, max_length=30)


class DraftEdit(BaseModel):
    """Human edit of a draft (FR-6). Unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid")

    subject: str | None = Field(default=None, min_length=3, max_length=120)
    body: str | None = Field(default=None, min_length=20, max_length=1800)
