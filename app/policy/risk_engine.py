from __future__ import annotations

from typing import Any

from app.models import ParsedIntent, PolicyDecision, RiskLevel
from app.policy.rules import (
    CONFIRM_CREATE_USER,
    CONFIRM_DELETE_USER,
    CREATE_USER_INTENTS,
    DELETE_USER_INTENTS,
    PRIVILEGED_GROUPS,
    READ_ONLY_INTENTS,
    SAFE_ALTERNATIVES,
    contains_destructive_word,
    contains_write_word,
    is_deep_search_refused_path,
    is_protected_path,
    is_same_or_child_path,
    is_sshd_config_path,
    is_sudoers_path,
    normalize_intent_name,
)
from app.policy.validators import validate_username_with_reasons


def evaluate(
    intent: ParsedIntent | dict[str, Any],
    env: Any | None = None,
    memory: Any | None = None,
) -> PolicyDecision:
    """Evaluate a structured intent and return the code-level policy decision."""

    del env, memory

    data = _IntentData.from_input(intent)
    intent_name = normalize_intent_name(data.intent)
    reasons: list[str] = []

    path_decision = _evaluate_path_rules(data, intent_name)
    if path_decision is not None:
        return path_decision

    if _requests_privilege_escalation(data):
        return _deny_s3(
            [
                "request would grant sudo, wheel, admin, or root privileges",
                "GuardedOps only supports normal non-privileged user operations",
            ],
            SAFE_ALTERNATIVES["privilege"],
        )

    if _is_bulk_permission_change(data, intent_name):
        return _deny_s3(
            [
                "bulk chmod/chown or recursive permission changes are forbidden",
                "permission changes can affect large parts of the system",
            ],
            SAFE_ALTERNATIVES["bulk_permission"],
        )

    if intent_name in CREATE_USER_INTENTS:
        username = data.username
        validation = validate_username_with_reasons(username)
        if not validation.valid:
            return _deny_s3(
                ["create_user username failed validation", *validation.reasons],
                SAFE_ALTERNATIVES["invalid_username"],
            )
        return PolicyDecision(
            risk_level=RiskLevel.S1,
            allow=True,
            requires_confirmation=True,
            confirmation_text=CONFIRM_CREATE_USER.format(username=username),
            reasons=_dedupe_reasons(["creating a normal non-privileged user is a restricted change"]),
            safe_alternative=None,
        )

    if intent_name in DELETE_USER_INTENTS:
        username = data.username
        validation = validate_username_with_reasons(username)
        if not validation.valid:
            return _deny_s3(
                ["delete_user username failed validation", *validation.reasons],
                SAFE_ALTERNATIVES["invalid_username"],
            )
        return PolicyDecision(
            risk_level=RiskLevel.S2,
            allow=True,
            requires_confirmation=True,
            confirmation_text=CONFIRM_DELETE_USER.format(username=username),
            reasons=_dedupe_reasons(
                ["deleting a normal user is a sensitive change and needs strong confirmation"]
            ),
            safe_alternative=None,
        )

    if _is_read_only(data, intent_name):
        reasons.append("recognized read-only operation")
        if intent_name == "search_files":
            reasons.append("file search must remain bounded by max_depth and max_results")
        return PolicyDecision(
            risk_level=RiskLevel.S0,
            allow=True,
            requires_confirmation=False,
            confirmation_text=None,
            reasons=_dedupe_reasons(reasons),
            safe_alternative=None,
        )

    if data.requires_write or contains_write_word(intent_name):
        return _deny_s3(
            [
                "unknown or unsupported write operation",
                "unknown writes are denied by default",
            ],
            SAFE_ALTERNATIVES["unknown_write"],
        )

    return PolicyDecision(
        risk_level=RiskLevel.S0,
        allow=False,
        requires_confirmation=False,
        confirmation_text=None,
        reasons=_dedupe_reasons(["unsupported read-only operation"]),
        safe_alternative=None,
    )


class _IntentData:
    def __init__(
        self,
        *,
        intent: str,
        target: dict[str, Any],
        constraints: dict[str, Any],
        requires_write: bool,
        raw_user_input: str | None,
    ) -> None:
        self.intent = intent
        self.target = target
        self.constraints = constraints
        self.requires_write = requires_write
        self.raw_user_input = raw_user_input

    @classmethod
    def from_input(cls, value: ParsedIntent | dict[str, Any]) -> "_IntentData":
        if isinstance(value, ParsedIntent):
            return cls(
                intent=value.intent,
                target=value.target.model_dump(),
                constraints=dict(value.constraints),
                requires_write=value.requires_write,
                raw_user_input=value.raw_user_input,
            )

        if not isinstance(value, dict):
            return cls(
                intent="unknown",
                target={},
                constraints={},
                requires_write=True,
                raw_user_input=str(value),
            )

        target = value.get("target") or {}
        if not isinstance(target, dict):
            target = {}

        constraints = value.get("constraints") or {}
        if not isinstance(constraints, dict):
            constraints = {}

        return cls(
            intent=str(value.get("intent") or value.get("operation") or value.get("action") or "unknown"),
            target={
                **target,
                **{key: value[key] for key in ("username", "path") if key in value},
            },
            constraints={
                **constraints,
                **{key: value[key] for key in ("groups", "recursive", "bulk", "base_path") if key in value},
            },
            requires_write=bool(value.get("requires_write", False)),
            raw_user_input=value.get("raw_user_input"),
        )

    @property
    def username(self) -> str | None:
        value = self.target.get("username")
        return value if isinstance(value, str) else None

    @property
    def path(self) -> str | None:
        path = self.target.get("path") or self.constraints.get("base_path")
        return path if isinstance(path, str) else None

    @property
    def base_paths(self) -> list[str]:
        values = self.target.get("base_paths") or self.constraints.get("base_paths") or []
        if isinstance(values, str):
            return [values]
        if not isinstance(values, list):
            return []
        return [value for value in values if isinstance(value, str)]


def _evaluate_path_rules(data: _IntentData, intent_name: str) -> PolicyDecision | None:
    paths = _paths_for_policy(data)

    if any(is_sudoers_path(path) for path in paths) or "sudoers" in intent_name:
        return _deny_s3(
            [
                "modifying sudoers is forbidden",
                "sudoers changes can grant unrestricted administrative access",
            ],
            SAFE_ALTERNATIVES["sudoers"],
        )

    if any(is_sshd_config_path(path) for path in paths) or "sshd_config" in intent_name:
        return _deny_s3(
            [
                "modifying sshd_config is forbidden",
                "SSH daemon configuration changes can break or weaken remote access",
            ],
            SAFE_ALTERNATIVES["sshd_config"],
        )

    if intent_name == "search_files":
        if any(is_same_or_child_path(path, "/") for path in paths):
            return _deny_s3(
                [
                    "full filesystem search from / is refused",
                    "file searches must be explicitly narrowed",
                ],
                SAFE_ALTERNATIVES["full_disk_search"],
            )
        if any(is_deep_search_refused_path(path) for path in paths):
            return _deny_s3(
                [
                    "deep search under /dev, /proc, or /sys is refused",
                    "virtual system filesystems are unsafe and noisy search targets",
                ],
                SAFE_ALTERNATIVES["deep_search"],
            )
        return None

    if paths and (data.requires_write or contains_write_word(intent_name)):
        if any(is_same_or_child_path(path, "/etc") and contains_destructive_word(intent_name) for path in paths):
            return _deny_s3(
                [
                    "deleting or destructively modifying /etc is forbidden",
                    "/etc contains core system configuration",
                ],
                SAFE_ALTERNATIVES["protected_path"],
            )
        if any(is_protected_path(path) for path in paths):
            return _deny_s3(
                [
                    "write operation targets a protected system path",
                    "core directories are denied by policy",
                ],
                SAFE_ALTERNATIVES["protected_path"],
            )

    return None


def _paths_for_policy(data: _IntentData) -> list[str]:
    paths = []
    if data.path:
        paths.append(data.path)
    paths.extend(data.base_paths)
    return paths


def _is_read_only(data: _IntentData, intent_name: str) -> bool:
    return not data.requires_write and intent_name in READ_ONLY_INTENTS


def _requests_privilege_escalation(data: _IntentData) -> bool:
    raw_groups = data.constraints.get("groups") or data.constraints.get("add_groups") or []
    if isinstance(raw_groups, str):
        groups = {raw_groups.strip().lower()}
    elif isinstance(raw_groups, list):
        groups = {str(group).strip().lower() for group in raw_groups}
    else:
        groups = set()

    role = str(data.constraints.get("role") or "").strip().lower()
    privilege = str(data.constraints.get("privilege") or "").strip().lower()
    username = str(data.username or "").strip().lower()
    intent_name = normalize_intent_name(data.intent)

    return bool(
        groups & PRIVILEGED_GROUPS
        or role in PRIVILEGED_GROUPS
        or privilege in PRIVILEGED_GROUPS
        or username == "root"
        or "sudo" in intent_name
        or "root_permission" in intent_name
        or "grant_admin" in intent_name
    )


def _is_bulk_permission_change(data: _IntentData, intent_name: str) -> bool:
    is_permission_change = "chmod" in intent_name or "chown" in intent_name or "permission" in intent_name
    return bool(
        is_permission_change
        and (
            data.constraints.get("bulk") is True
            or data.constraints.get("recursive") is True
            or "bulk" in intent_name
            or "recursive" in intent_name
        )
    )


def _deny_s3(reasons: list[str], safe_alternative: str) -> PolicyDecision:
    return PolicyDecision(
        risk_level=RiskLevel.S3,
        allow=False,
        requires_confirmation=False,
        confirmation_text=None,
        reasons=_dedupe_reasons(reasons),
        safe_alternative=safe_alternative,
    )


def _dedupe_reasons(reasons: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        text = str(reason).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
