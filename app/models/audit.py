from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from app.models.environment import EnvironmentSnapshot
from app.models.intent import ParsedIntent
from app.models.policy import PolicyDecision
from app.models.result import CommandResult, ExecutionStatus, ToolCall


class AuditRecord(BaseModel):
    """Complete request audit envelope for later JSONL and SQLite storage."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw_user_input: str
    parsed_intent: ParsedIntent = Field(default_factory=ParsedIntent)
    environment_snapshot: EnvironmentSnapshot = Field(default_factory=EnvironmentSnapshot)
    risk_decision: PolicyDecision = Field(default_factory=PolicyDecision)
    confirmation_status: str = "not_required"
    tool_calls: list[ToolCall] = Field(default_factory=list)
    command_results: list[CommandResult] = Field(default_factory=list)
    final_status: ExecutionStatus = ExecutionStatus.FAILED
    final_answer: str = ""
