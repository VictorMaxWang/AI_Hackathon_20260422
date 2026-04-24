from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agent.confirmation import PendingAction
from app.models import ParsedIntent, RiskLevel


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SessionMemory(BaseModel):
    """Serializable in-memory context for one conversation session."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = "default"
    last_username: str | None = None
    last_path: str | None = None
    last_port: int | None = Field(default=None, ge=0, le=65535)
    last_pid: int | None = Field(default=None, ge=0)
    last_intent: str | None = None
    last_risk_level: str | None = None
    pending_action: PendingAction | None = None
    updated_at: datetime = Field(default_factory=_utc_now)

    def set_pending_action(self, action: PendingAction) -> None:
        self.pending_action = action
        self._touch()

    def clear_pending_action(self) -> PendingAction | None:
        action = self.pending_action
        self.pending_action = None
        self._touch()
        return action

    def get_pending_checkpoint(self) -> dict[str, Any] | None:
        if self.pending_action is None:
            return None
        checkpoint = self.pending_action.context.get("checkpoint")
        if not isinstance(checkpoint, dict):
            return None
        return dict(checkpoint)

    def clear_pending_checkpoint(self) -> None:
        if self.pending_action is None:
            return
        context = dict(self.pending_action.context)
        if "checkpoint" not in context:
            return
        context.pop("checkpoint", None)
        self.pending_action = self.pending_action.model_copy(update={"context": context})
        self._touch()

    def remember_intent(
        self,
        parsed_intent: ParsedIntent,
        *,
        risk_level: RiskLevel | str | None = None,
    ) -> None:
        """Record explicit structured targets from a parsed, accepted request."""

        target = parsed_intent.target
        if target.username:
            self.last_username = target.username
        if target.path:
            self.last_path = target.path
        elif target.base_paths:
            self.last_path = target.base_paths[0]
        if target.port is not None:
            self.last_port = target.port
        if target.pid is not None:
            self.last_pid = target.pid

        self.last_intent = parsed_intent.intent
        if risk_level is not None:
            self.last_risk_level = (
                risk_level.value if isinstance(risk_level, RiskLevel) else str(risk_level)
            )
        self._touch()

    def resolve(self, slot: str) -> Any:
        if slot == "username":
            return self.last_username
        if slot == "path":
            return self.last_path
        if slot == "port":
            return self.last_port
        if slot == "pid":
            return self.last_pid
        return None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def _touch(self) -> None:
        self.updated_at = _utc_now()


class AgentMemory(SessionMemory):
    """Backward-compatible name used by the orchestrator."""
