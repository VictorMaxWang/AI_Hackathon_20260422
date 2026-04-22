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
