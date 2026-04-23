from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import RiskLevel


CREATE_USER_CONFIRMATION_TEMPLATE = "确认创建普通用户 {username}"
DELETE_USER_CONFIRMATION_TEMPLATE = "确认删除普通用户 {username}"
CANCEL_PENDING_TEXTS = frozenset({"取消", "放弃", "cancel"})


class PendingAction(BaseModel):
    """Serializable action waiting for an exact user confirmation phrase."""

    model_config = ConfigDict(extra="forbid")

    intent: str
    target: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel
    confirmation_text: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tool_name: str
    tool_args: dict[str, Any] = Field(default_factory=dict)
    raw_user_input: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)

    def matches_confirmation(self, raw_user_input: str) -> bool:
        return str(raw_user_input or "").strip() == self.confirmation_text

    def public_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def confirmation_text_for(intent: str, target: dict[str, Any]) -> str | None:
    username = target.get("username")
    if not isinstance(username, str) or not username:
        return None
    if intent == "create_user":
        return CREATE_USER_CONFIRMATION_TEMPLATE.format(username=username)
    if intent == "delete_user":
        return DELETE_USER_CONFIRMATION_TEMPLATE.format(username=username)
    return None


def is_cancel_pending_text(raw_user_input: str) -> bool:
    return str(raw_user_input or "").strip().lower() in CANCEL_PENDING_TEXTS
