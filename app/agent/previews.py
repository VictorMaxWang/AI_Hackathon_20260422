from __future__ import annotations

from typing import Any

from app.agent.confirmation import stable_hash
from app.models import ParsedIntent, PolicyDecision
from app.policy.rules import (
    CREATE_USER_INTENTS,
    DEEP_SEARCH_REFUSED_PATHS,
    DELETE_USER_INTENTS,
    PRIVILEGED_GROUPS,
    PROTECTED_PATHS,
    READ_ONLY_INTENTS,
    contains_write_word,
    is_deep_search_refused_path,
    is_protected_path,
    is_same_or_child_path,
    is_sshd_config_path,
    is_sudoers_path,
    normalize_intent_name,
)
from app.policy.validators import validate_username_with_reasons


def build_blast_radius_preview(
    *,
    parsed_intent: ParsedIntent | dict[str, Any],
    risk: PolicyDecision | dict[str, Any],
    plan: dict[str, Any] | None = None,
    execution: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del execution, environment

    data = _PreviewIntentData.from_input(parsed_intent)
    risk_payload = _policy_payload(risk)
    plan_payload = _as_dict(plan)
    result_payload = _as_dict(result)

    if data.intent_name in CREATE_USER_INTENTS:
        return _create_user_preview(data, result_payload)
    if data.intent_name in DELETE_USER_INTENTS:
        return _delete_user_preview(data, result_payload)
    if data.intent_name == "search_files":
        return _file_search_preview(data, risk_payload)
    if not bool(risk_payload.get("allow")) or str(plan_payload.get("status") or "").lower() == "refused":
        return _refused_request_preview(data, risk_payload)
    return _generic_preview(data, risk_payload)


def build_policy_simulator(
    *,
    parsed_intent: ParsedIntent | dict[str, Any],
    risk: PolicyDecision | dict[str, Any],
    policy_version: str,
) -> dict[str, Any]:
    data = _PreviewIntentData.from_input(parsed_intent)
    risk_payload = _policy_payload(risk)
    matched_rules = _matched_policy_rules(data, risk_payload)
    scope_summary = _scope_summary(data)

    return {
        "risk_level": _first_text(risk_payload.get("risk_level"), "unknown"),
        "allow": bool(risk_payload.get("allow")),
        "requires_confirmation": bool(risk_payload.get("requires_confirmation")),
        "policy_version": _first_text(policy_version, "unknown"),
        "matched_rules": matched_rules,
        "denied_because": _string_list(risk_payload.get("reasons"))
        if not bool(risk_payload.get("allow"))
        else [],
        "requires_confirmation_because": _string_list(risk_payload.get("reasons"))
        if bool(risk_payload.get("requires_confirmation"))
        else [],
        "scope_summary": scope_summary,
        "target_fingerprint": stable_hash(
            {
                "intent": data.intent_name,
                "target": dict(data.target),
                "scope": data.scope_constraints(),
            }
        ),
        "safe_alternative": _first_text(risk_payload.get("safe_alternative")),
    }


class _PreviewIntentData:
    def __init__(
        self,
        *,
        intent_name: str,
        target: dict[str, Any],
        constraints: dict[str, Any],
        requires_write: bool,
        raw_user_input: str,
    ) -> None:
        self.intent_name = intent_name
        self.target = target
        self.constraints = constraints
        self.requires_write = requires_write
        self.raw_user_input = raw_user_input

    @classmethod
    def from_input(cls, value: ParsedIntent | dict[str, Any]) -> _PreviewIntentData:
        if isinstance(value, ParsedIntent):
            return cls(
                intent_name=normalize_intent_name(value.intent),
                target=value.target.model_dump(mode="json", exclude_none=True),
                constraints=dict(value.constraints),
                requires_write=value.requires_write,
                raw_user_input=_first_text(value.raw_user_input),
            )

        payload = _as_dict(value)
        target = _as_dict(payload.get("target"))
        constraints = _as_dict(payload.get("constraints"))
        return cls(
            intent_name=normalize_intent_name(payload.get("intent")),
            target=target,
            constraints=constraints,
            requires_write=bool(payload.get("requires_write")),
            raw_user_input=_first_text(payload.get("raw_user_input")),
        )

    @property
    def username(self) -> str:
        return _first_text(self.target.get("username"))

    @property
    def path(self) -> str:
        return _first_text(
            self.target.get("path"),
            self.constraints.get("base_path"),
        )

    @property
    def base_paths(self) -> list[str]:
        raw_value = self.target.get("base_paths")
        if isinstance(raw_value, list):
            return _string_list(raw_value)
        raw_constraints = self.constraints.get("base_paths")
        if isinstance(raw_constraints, list):
            return _string_list(raw_constraints)
        single = _first_text(raw_value, raw_constraints)
        return [single] if single else []

    def scope_constraints(self) -> dict[str, Any]:
        keys = (
            "base_path",
            "base_paths",
            "create_home",
            "remove_home",
            "groups",
            "max_depth",
            "max_results",
            "modified_within_days",
            "no_sudo",
            "role",
            "privilege",
        )
        scoped = {
            key: self.constraints[key]
            for key in keys
            if key in self.constraints and self.constraints[key] is not None
        }
        if self.path and "base_path" not in scoped:
            scoped["base_path"] = self.path
        return scoped


def _create_user_preview(
    data: _PreviewIntentData,
    result_payload: dict[str, Any],
) -> dict[str, Any]:
    username = data.username or "unknown"
    result_data = _as_dict(result_payload.get("data"))
    create_home = _bool_or_default(
        result_data.get("create_home"),
        default=bool(data.constraints.get("create_home", True)),
    )
    home_path = _first_text(
        _nested_value(result_payload, "data", "user", "home"),
        f"/home/{username}" if username and create_home else "",
    )

    impacts = [
        {
            "label": "Account scope",
            "value": f"One normal user account would be created for {username}.",
            "precision": "bounded",
        },
        {
            "label": "Home directory",
            "value": (
                f"GuardedOps would request a home directory at {home_path}."
                if create_home and home_path
                else "This request does not ask for a home directory."
            ),
            "precision": "bounded",
        },
        {
            "label": "Privilege boundary",
            "value": "The create_user flow does not add sudo, wheel, admin, or root privileges.",
            "precision": "bounded",
        },
    ]
    notes = [
        "Preview is derived from planner and policy data only.",
        "No additional system probing is performed before confirmation.",
    ]
    return {
        "scenario": "create_user",
        "summary": f"Bounded to creating the normal user {username} without privilege escalation.",
        "facts": [
            {"label": "Target user", "value": username},
            {"label": "create_home", "value": "true" if create_home else "false"},
            {"label": "Home path", "value": home_path or "not requested"},
            {
                "label": "Privileged groups",
                "value": "sudo/wheel/admin are excluded",
            },
        ],
        "impacts": impacts,
        "protected_paths": [],
        "notes": notes,
    }


def _delete_user_preview(
    data: _PreviewIntentData,
    result_payload: dict[str, Any],
) -> dict[str, Any]:
    username = data.username or "unknown"
    remove_home = _bool_or_default(
        _nested_value(result_payload, "data", "remove_home"),
        default=bool(data.constraints.get("remove_home", False)),
    )
    home_path = _first_text(
        _nested_value(result_payload, "data", "deleted_user", "home"),
        _nested_value(result_payload, "data", "user", "home"),
        f"/home/{username}" if username else "",
    )

    impacts = [
        {
            "label": "Account record",
            "value": f"The login account for {username} would be removed if the tool validation passes.",
            "precision": "bounded",
        },
        {
            "label": "Home directory",
            "value": (
                f"The request asks to remove {home_path or 'the user home'}."
                if remove_home
                else f"The request keeps {home_path or 'the user home'} in place."
            ),
            "precision": "bounded",
        },
        {
            "label": "Owned files",
            "value": "Files owned by the target user outside the home directory are not enumerated in this preview.",
            "precision": "conservative",
        },
        {
            "label": "Sessions and processes",
            "value": "Existing sessions or processes for the target user are not enumerated here and should be reviewed separately.",
            "precision": "conservative",
        },
    ]
    return {
        "scenario": "delete_user",
        "summary": f"Bounded to one normal user deletion request for {username}.",
        "facts": [
            {"label": "Target user", "value": username},
            {"label": "remove_home", "value": "true" if remove_home else "false"},
            {"label": "Known or predicted home", "value": home_path or "not available"},
        ],
        "impacts": impacts,
        "protected_paths": [],
        "notes": [
            "Preview is conservative for files, sessions, and processes.",
            "System-user, current-user, and UID safety checks still run in the guarded tool path.",
        ],
    }


def _file_search_preview(
    data: _PreviewIntentData,
    risk_payload: dict[str, Any],
) -> dict[str, Any]:
    base_path = data.path or "missing"
    max_results = _coerce_int(data.constraints.get("max_results"), default=20)
    max_depth = _coerce_int(data.constraints.get("max_depth"), default=4)
    keyword = _first_text(data.target.get("keyword"))
    modified_within_days = _first_text(data.constraints.get("modified_within_days"))
    protected_paths = _matched_protected_paths([base_path])
    if base_path == "/":
        protected_paths = ["/"]
    if any(is_same_or_child_path(base_path, blocked_root) for blocked_root in DEEP_SEARCH_REFUSED_PATHS):
        protected_paths = _matched_protected_paths([base_path], include_deep_roots=True)

    summary = f"Read-only search scope is bounded to {base_path} with depth {max_depth} and at most {max_results} results."
    if not bool(risk_payload.get("allow")):
        summary = _first_text(
            _first_reason(risk_payload),
            f"Search scope is refused for {base_path}.",
        )

    notes = [
        "Search previews reflect planner limits only and do not estimate an exact file count.",
        "GuardedOps refuses deep search under /proc, /sys, and /dev.",
    ]
    if not bool(risk_payload.get("allow")) and base_path == "/":
        notes.append("The full filesystem root is treated as too broad for a single guarded search request.")

    facts = [
        {"label": "base_path", "value": base_path},
        {"label": "max_results", "value": str(max_results)},
        {"label": "max_depth", "value": str(max_depth)},
    ]
    if keyword:
        facts.append({"label": "name filter", "value": keyword})
    if modified_within_days:
        facts.append({"label": "modified_within_days", "value": modified_within_days})

    impacts = [
        {
            "label": "Traversal scope",
            "value": f"Only files under {base_path} are eligible for read-only traversal.",
            "precision": "bounded",
        },
        {
            "label": "Result cap",
            "value": f"Returned results are capped at {max_results}; output may be truncated before exhaustively listing files.",
            "precision": "bounded",
        },
        {
            "label": "Skipped system roots",
            "value": "Deep searches under /proc, /sys, and /dev are blocked instead of traversed.",
            "precision": "bounded",
        },
    ]
    if not bool(risk_payload.get("allow")):
        impacts.append(
            {
                "label": "Scope refusal",
                "value": _first_reason(risk_payload) or "The requested search scope is broader than GuardedOps allows.",
                "precision": "bounded",
            }
        )

    return {
        "scenario": "file_search",
        "summary": summary,
        "facts": facts,
        "impacts": impacts,
        "protected_paths": protected_paths,
        "notes": notes,
    }


def _refused_request_preview(
    data: _PreviewIntentData,
    risk_payload: dict[str, Any],
) -> dict[str, Any]:
    paths = [data.path, *data.base_paths]
    protected_paths = _matched_protected_paths(paths, include_deep_roots=True)
    summary = _first_reason(risk_payload) or "The request stays outside the guarded execution boundary."

    facts = [
        {"label": "Intent", "value": data.intent_name or "unknown"},
        {"label": "Risk level", "value": _first_text(risk_payload.get("risk_level"), "unknown")},
    ]
    if data.path:
        facts.append({"label": "Target path", "value": data.path})

    impacts = [
        {
            "label": "Execution boundary",
            "value": "The policy engine blocks this request before any tool execution occurs.",
            "precision": "bounded",
        },
        {
            "label": "Blast radius rationale",
            "value": summary,
            "precision": "bounded",
        },
    ]
    if protected_paths:
        impacts.append(
            {
                "label": "Protected scope",
                "value": "The request overlaps protected system paths and is treated as too broad or too sensitive.",
                "precision": "bounded",
            }
        )

    notes = [
        "Refusal previews explain the blocked scope only; they do not simulate execution.",
    ]
    safe_alternative = _first_text(risk_payload.get("safe_alternative"))
    if safe_alternative:
        notes.append(f"Safe alternative: {safe_alternative}")

    return {
        "scenario": "dangerous_request",
        "summary": summary,
        "facts": facts,
        "impacts": impacts,
        "protected_paths": protected_paths,
        "notes": notes,
    }


def _generic_preview(
    data: _PreviewIntentData,
    risk_payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "scenario": data.intent_name or "general",
        "summary": "Scope is limited to the current guarded request envelope.",
        "facts": [
            {"label": "Intent", "value": data.intent_name or "unknown"},
            {"label": "Risk level", "value": _first_text(risk_payload.get("risk_level"), "unknown")},
        ],
        "impacts": [
            {
                "label": "Preview quality",
                "value": "Only currently available planner, policy, and result metadata are included.",
                "precision": "conservative",
            }
        ],
        "protected_paths": _matched_protected_paths([data.path, *data.base_paths], include_deep_roots=True),
        "notes": ["No additional execution or probing is performed for this preview."],
    }


def _matched_policy_rules(
    data: _PreviewIntentData,
    risk_payload: dict[str, Any],
) -> list[dict[str, str]]:
    outcome = _rule_outcome(risk_payload)
    username_validation = validate_username_with_reasons(data.username) if data.username else None
    paths = [data.path, *data.base_paths]

    if any(is_sudoers_path(path) for path in paths) or "sudoers" in data.intent_name:
        return [_rule("path.sudoers_denied", outcome, "Refuses modifications to sudoers paths.")]
    if any(is_sshd_config_path(path) for path in paths) or "sshd_config" in data.intent_name:
        return [_rule("path.sshd_config_denied", outcome, "Refuses modifications to sshd configuration.")]
    if data.intent_name == "search_files" and any(is_same_or_child_path(path, "/") for path in paths):
        return [_rule("path.full_disk_search_refused", outcome, "Refuses full filesystem searches from /.")]
    if data.intent_name == "search_files" and any(is_deep_search_refused_path(path) for path in paths):
        return [_rule("path.deep_search_refused", outcome, "Refuses deep search under /proc, /sys, or /dev.")]
    if _requests_privilege_escalation(data):
        return [_rule("user.privilege_escalation_denied", outcome, "Refuses requests that would grant privileged access.")]
    if _is_bulk_permission_change(data):
        return [_rule("permission.bulk_change_denied", outcome, "Refuses bulk chmod/chown style changes.")]
    if data.intent_name in CREATE_USER_INTENTS:
        if username_validation is not None and not username_validation.valid:
            return [_rule("user.username_invalid", outcome, "Refuses usernames that do not match GuardedOps rules.")]
        return [_rule("user.create_requires_confirmation", outcome, "Requires confirmation for creating one normal user.")]
    if data.intent_name in DELETE_USER_INTENTS:
        if username_validation is not None and not username_validation.valid:
            return [_rule("user.username_invalid", outcome, "Refuses usernames that do not match GuardedOps rules.")]
        return [_rule("user.delete_requires_confirmation", outcome, "Requires strong confirmation for deleting one normal user.")]
    if paths and (data.requires_write or contains_write_word(data.intent_name)):
        if any(is_protected_path(path) for path in paths):
            return [_rule("path.protected_write_denied", outcome, "Refuses writes to protected system paths.")]
    if not data.requires_write and data.intent_name in READ_ONLY_INTENTS:
        return [_rule("readonly.allowed", outcome, "Allows recognized read-only requests within bounded scope.")]
    if data.requires_write or contains_write_word(data.intent_name):
        return [_rule("write.unknown_denied", outcome, "Denies unknown or unsupported write requests by default.")]
    return [_rule("readonly.unsupported", outcome, "Rejects unsupported read-only requests without executing tools.")]


def _scope_summary(data: _PreviewIntentData) -> str:
    if data.intent_name in CREATE_USER_INTENTS:
        create_home = _bool_or_default(data.constraints.get("create_home"), default=True)
        return (
            f"Single-user create scope for {data.username or 'unknown'}; "
            f"create_home={'true' if create_home else 'false'}; sudo/wheel/admin excluded."
        )
    if data.intent_name in DELETE_USER_INTENTS:
        remove_home = _bool_or_default(data.constraints.get("remove_home"), default=False)
        return (
            f"Single-user delete scope for {data.username or 'unknown'}; "
            f"remove_home={'true' if remove_home else 'false'}."
        )
    if data.intent_name == "search_files":
        summary = (
            f"Read-only file search under {data.path or 'missing'}; "
            f"max_depth={_coerce_int(data.constraints.get('max_depth'), default=4)}; "
            f"max_results={_coerce_int(data.constraints.get('max_results'), default=20)}."
        )
        keyword = _first_text(data.target.get("keyword"))
        if keyword:
            summary += f" name filter={keyword}."
        modified_within_days = _first_text(data.constraints.get("modified_within_days"))
        if modified_within_days:
            summary += f" modified_within_days={modified_within_days}."
        return summary
    if data.path:
        return f"Path-targeted request against {data.path}."
    if data.username:
        return f"User-targeted request for {data.username}."
    return f"Intent scope for {data.intent_name or 'unknown'}."


def _requests_privilege_escalation(data: _PreviewIntentData) -> bool:
    raw_groups = data.constraints.get("groups") or data.constraints.get("add_groups") or []
    if isinstance(raw_groups, str):
        groups = {raw_groups.strip().lower()}
    elif isinstance(raw_groups, list):
        groups = {str(group).strip().lower() for group in raw_groups}
    else:
        groups = set()

    role = _first_text(data.constraints.get("role")).lower()
    privilege = _first_text(data.constraints.get("privilege")).lower()
    return bool(
        groups & PRIVILEGED_GROUPS
        or role in PRIVILEGED_GROUPS
        or privilege in PRIVILEGED_GROUPS
        or data.username.lower() == "root"
        or "sudo" in data.intent_name
        or "root_permission" in data.intent_name
        or "grant_admin" in data.intent_name
    )


def _is_bulk_permission_change(data: _PreviewIntentData) -> bool:
    intent_name = data.intent_name
    is_permission_change = (
        "chmod" in intent_name or "chown" in intent_name or "permission" in intent_name
    )
    return bool(
        is_permission_change
        and (
            data.constraints.get("bulk") is True
            or data.constraints.get("recursive") is True
            or "bulk" in intent_name
            or "recursive" in intent_name
        )
    )


def _matched_protected_paths(
    paths: list[str],
    *,
    include_deep_roots: bool = False,
) -> list[str]:
    matched: list[str] = []
    for path in _string_list(paths):
        for protected_path in PROTECTED_PATHS:
            if is_same_or_child_path(path, protected_path) and protected_path not in matched:
                matched.append(protected_path)
        if include_deep_roots:
            for blocked_root in DEEP_SEARCH_REFUSED_PATHS:
                if is_same_or_child_path(path, blocked_root) and blocked_root not in matched:
                    matched.append(blocked_root)
    return matched


def _policy_payload(value: PolicyDecision | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, PolicyDecision):
        return value.model_dump(mode="json")
    return _as_dict(value)


def _rule(rule_id: str, outcome: str, summary: str) -> dict[str, str]:
    return {
        "rule_id": rule_id,
        "outcome": outcome,
        "summary": summary,
    }


def _rule_outcome(risk_payload: dict[str, Any]) -> str:
    if bool(risk_payload.get("requires_confirmation")):
        return "confirm"
    if bool(risk_payload.get("allow")):
        return "allow"
    return "deny"


def _first_reason(risk_payload: dict[str, Any]) -> str:
    reasons = _string_list(risk_payload.get("reasons"))
    return reasons[0] if reasons else ""


def _nested_value(source: dict[str, Any], *path: str) -> Any:
    current: Any = source
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _bool_or_default(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    cleaned: list[str] = []
    for item in values:
        text = _first_text(item)
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""
