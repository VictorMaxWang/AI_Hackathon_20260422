from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExecutionStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    REFUSED = "refused"
    PENDING_CONFIRMATION = "pending_confirmation"


class CommandResult(BaseModel):
    """Normalized command outcome shared by future local and SSH executors."""

    model_config = ConfigDict(extra="forbid")

    argv: list[str] = Field(default_factory=list)
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = Field(default=0, ge=0)
    timed_out: bool = False
    success: bool = False


class ToolCall(BaseModel):
    """Audit-friendly record of a whitelisted tool invocation."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str = "unknown"
    args: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ToolResult(BaseModel):
    """Structured result returned by a whitelisted tool."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str = "unknown"
    success: bool = False
    data: Any = None
    error: str | None = None
