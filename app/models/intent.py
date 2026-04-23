from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IntentTarget(BaseModel):
    """Structured target extracted from a user request."""

    model_config = ConfigDict(extra="forbid")

    username: str | None = None
    path: str | None = None
    port: int | None = Field(default=None, ge=0, le=65535)
    pid: int | None = Field(default=None, ge=0)
    keyword: str | None = None
    base_paths: list[str] = Field(default_factory=list)


class ParsedIntent(BaseModel):
    """Structured intent used as the input contract for later agent layers."""

    model_config = ConfigDict(extra="forbid")

    intent: str = "unknown"
    target: IntentTarget = Field(default_factory=IntentTarget)
    constraints: dict[str, Any] = Field(default_factory=dict)
    context_refs: list[str] = Field(default_factory=list)
    requires_write: bool = False
    raw_user_input: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class PlanStep(BaseModel):
    """One controlled step in a multi-step plan.

    This model describes intent and dependencies only. It is not an executable
    tool call and must still pass through policy and confirmation later.
    """

    model_config = ConfigDict(extra="forbid")

    step_id: str
    intent: str
    target: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    condition: str | None = None
    description: str
    requires_policy: bool = True
    requires_confirmation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class ExecutionPlan(BaseModel):
    """Structured multi-step plan produced from one natural-language request."""

    model_config = ConfigDict(extra="forbid")

    raw_user_input: str
    status: str = "unsupported"
    supported: bool = False
    steps: list[PlanStep] = Field(default_factory=list)
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
