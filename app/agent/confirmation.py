from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import RiskLevel


CREATE_USER_CONFIRMATION_TEMPLATE = "\u786e\u8ba4\u521b\u5efa\u666e\u901a\u7528\u6237 {username}"
DELETE_USER_CONFIRMATION_TEMPLATE = "\u786e\u8ba4\u5220\u9664\u666e\u901a\u7528\u6237 {username}"
CANCEL_PENDING_TEXTS = frozenset(
    {"\u53d6\u6d88", "\u653e\u5f03", "cancel"}
)
DEFAULT_CONFIRMATION_TOKEN_TTL = timedelta(minutes=5)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_for_hash(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _normalize_for_hash(value.model_dump(mode="json"))
    if isinstance(value, datetime):
        return _ensure_utc(value).isoformat()
    if isinstance(value, RiskLevel):
        return value.value
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        return {
            str(key): _normalize_for_hash(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_for_hash(item) for item in value]
    if isinstance(value, set):
        normalized = [_normalize_for_hash(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
        )
    return value


def stable_json_dumps(value: Any) -> str:
    return json.dumps(
        _normalize_for_hash(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def stable_hash(value: Any) -> str:
    return hashlib.sha256(stable_json_dumps(value).encode("utf-8")).hexdigest()


def stable_file_content_hash(paths: Iterable[Path]) -> str:
    entries: list[dict[str, str]] = []
    for path in sorted(paths, key=lambda item: item.as_posix()):
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            digest = "missing"
        entries.append({"path": path.name, "digest": digest})
    return stable_hash(entries)


class ConfirmationToken(BaseModel):
    """Confirmation token bound to the current executable closure."""

    model_config = ConfigDict(extra="forbid")

    plan_hash: str
    host_id: str
    target_fingerprint: str
    risk_level: RiskLevel
    policy_version: str
    issued_at: datetime = Field(default_factory=_utc_now)
    expires_at: datetime

    def is_expired(self, now: datetime | None = None) -> bool:
        current_time = _ensure_utc(now or _utc_now())
        return current_time >= _ensure_utc(self.expires_at)


class PendingAction(BaseModel):
    """Serializable action waiting for an exact user confirmation phrase."""

    model_config = ConfigDict(extra="forbid")

    intent: str
    target: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel
    confirmation_text: str
    created_at: datetime = Field(default_factory=_utc_now)
    tool_name: str
    tool_args: dict[str, Any] = Field(default_factory=dict)
    raw_user_input: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    confirmation_token: ConfirmationToken | None = None

    def matches_confirmation(self, raw_user_input: str) -> bool:
        return str(raw_user_input or "").strip() == self.confirmation_text

    def public_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def issue_confirmation_token(
    *,
    plan_payload: Any,
    host_id: str,
    target: dict[str, Any],
    risk_level: RiskLevel,
    policy_version: str,
    issued_at: datetime | None = None,
    ttl: timedelta = DEFAULT_CONFIRMATION_TOKEN_TTL,
) -> ConfirmationToken:
    token_issued_at = _ensure_utc(issued_at or _utc_now())
    normalized_host_id = str(host_id or "unknown").strip() or "unknown"
    normalized_policy_version = str(policy_version or "unknown").strip() or "unknown"
    return ConfirmationToken(
        plan_hash=stable_hash(plan_payload),
        host_id=normalized_host_id,
        target_fingerprint=stable_hash(target),
        risk_level=risk_level,
        policy_version=normalized_policy_version,
        issued_at=token_issued_at,
        expires_at=token_issued_at + ttl,
    )


def validate_confirmation_token(
    token: ConfirmationToken | None,
    *,
    plan_payload: Any,
    host_id: str,
    target: dict[str, Any],
    risk_level: RiskLevel,
    policy_version: str,
    now: datetime | None = None,
) -> str | None:
    if token is None:
        return "missing_confirmation_token"
    if token.is_expired(now):
        return "confirmation_token_expired"
    normalized_host_id = str(host_id or "unknown").strip() or "unknown"
    if token.host_id != normalized_host_id:
        return "confirmation_token_host_mismatch"
    if token.target_fingerprint != stable_hash(target):
        return "confirmation_token_target_mismatch"
    if token.risk_level != risk_level:
        return "confirmation_token_risk_mismatch"
    normalized_policy_version = str(policy_version or "unknown").strip() or "unknown"
    if token.policy_version != normalized_policy_version:
        return "confirmation_token_policy_mismatch"
    if token.plan_hash != stable_hash(plan_payload):
        return "confirmation_token_plan_mismatch"
    return None


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
