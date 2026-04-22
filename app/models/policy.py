from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RiskLevel(StrEnum):
    S0 = "S0"
    S1 = "S1"
    S2 = "S2"
    S3 = "S3"


class PolicyDecision(BaseModel):
    """Policy result produced by later risk evaluation code."""

    model_config = ConfigDict(extra="forbid")

    risk_level: RiskLevel = RiskLevel.S3
    allow: bool = False
    requires_confirmation: bool = False
    confirmation_text: str | None = None
    reasons: list[str] = Field(default_factory=list)
    safe_alternative: str | None = None
