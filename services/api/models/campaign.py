from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CampaignCreate(BaseModel):
    """Product intake (FR-1). Inputs are size-capped; whitespace-only is rejected."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product_name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=4000)
    geography: str = Field(default="", max_length=200)
    customer_type: str = Field(default="", max_length=300)
    pricing_notes: str = Field(default="", max_length=500)
    exclusions: str = Field(default="", max_length=1000)
