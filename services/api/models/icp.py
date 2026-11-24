"""Typed ICP schema (FR-2). The LLM must return exactly this shape; humans may edit it."""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
FIELD_NAMES = ("industries", "company_size", "buyer_personas", "buying_signals", "exclusions")


class CompanySize(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_employees: int = Field(ge=1, le=1_000_000)
    max_employees: int = Field(ge=1, le=1_000_000)

    @model_validator(mode="after")
    def _ordered(self):
        if self.max_employees < self.min_employees:
            raise ValueError("max_employees must be >= min_employees")
        return self


class BuyerPersona(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Short
    rationale: str = Field(default="", max_length=400)


class ICPFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    industries: list[Short] = Field(min_length=1, max_length=10)
    company_size: CompanySize
    buyer_personas: list[BuyerPersona] = Field(min_length=1, max_length=8)
    buying_signals: list[Short] = Field(min_length=1, max_length=10)
    exclusions: list[Short] = Field(default_factory=list, max_length=30)
    reasoning_summary: str = Field(min_length=1, max_length=1500)
    confidence_by_field: dict[str, float]

    @field_validator("confidence_by_field")
    @classmethod
    def _confidence(cls, v: dict[str, float]):
        unknown = set(v) - set(FIELD_NAMES)
        if unknown:
            raise ValueError(f"unknown confidence keys: {sorted(unknown)}")
        if any(not 0.0 <= x <= 1.0 for x in v.values()):
            raise ValueError("confidence values must be between 0 and 1")
        missing = set(FIELD_NAMES) - set(v)
        if missing:
            raise ValueError(f"missing confidence for: {sorted(missing)}")
        return v


class ICPUpdate(BaseModel):
    """PATCH body: any subset of ICP fields, plus `approve`. Unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid")

    industries: list[Short] | None = None
    company_size: CompanySize | None = None
    buyer_personas: list[BuyerPersona] | None = None
    buying_signals: list[Short] | None = None
    exclusions: list[Short] | None = None
    reasoning_summary: str | None = None
    confidence_by_field: dict[str, float] | None = None
    approve: bool = False

    def changed_fields(self) -> dict:
        return {k: v for k, v in self.model_dump(exclude={"approve"}, exclude_none=True).items()}
