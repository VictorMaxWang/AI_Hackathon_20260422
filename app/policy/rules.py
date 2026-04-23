from __future__ import annotations

import posixpath
from typing import Any

from app.models import RiskLevel


READ_ONLY_INTENTS = frozenset(
    {
        "audit_query",
        "audit_query_tool",
        "env_probe",
        "env_probe_tool",
        "file_search",
        "file_search_tool",
        "query_audit",
        "query_disk_usage",
        "query_port",
        "query_process",
        "search_files",
    }
)

CREATE_USER_INTENTS = frozenset(
    {
        "add_user",
        "create_user",
        "create_user_tool",
        "user_create",
    }
)

DELETE_USER_INTENTS = frozenset(
    {
        "delete_user",
        "delete_user_tool",
        "remove_user",
        "user_delete",
    }
)

PROTECTED_PATHS = (
    "/",
    "/etc",
    "/boot",
    "/bin",
    "/sbin",
    "/usr",
    "/lib",
    "/lib64",
    "/dev",
    "/proc",
    "/sys",
)

DEEP_SEARCH_REFUSED_PATHS = ("/dev", "/proc", "/sys")
SUDOERS_PATHS = ("/etc/sudoers", "/etc/sudoers.d")
SSHD_CONFIG_PATHS = ("/etc/ssh/sshd_config",)
PRIVILEGED_GROUPS = frozenset({"root", "sudo", "wheel", "admin", "administrator"})

SYSTEM_USERNAMES = frozenset(
    {
        "root",
        "admin",
        "administrator",
        "sudo",
        "wheel",
        "daemon",
        "bin",
        "sys",
        "sync",
        "games",
        "man",
        "lp",
        "mail",
        "news",
        "uucp",
        "proxy",
        "www-data",
        "backup",
        "list",
        "irc",
        "gnats",
        "nobody",
        "systemd-network",
        "systemd-resolve",
        "sshd",
    }
)

DESTRUCTIVE_INTENT_WORDS = frozenset(
    {
        "delete",
        "destroy",
        "purge",
        "remove",
        "rm",
        "truncate",
        "wipe",
    }
)

WRITE_INTENT_WORDS = frozenset(
    {
        "append",
        "change",
        "chmod",
        "chown",
        "create",
        "delete",
        "destroy",
        "disable",
        "enable",
        "modify",
        "move",
        "purge",
        "remove",
        "rename",
        "restart",
        "rm",
        "start",
        "stop",
        "truncate",
        "update",
        "wipe",
        "write",
    }
)

SAFE_ALTERNATIVES = {
    "protected_path": "Narrow the request to a non-core application path and use a read-only inspection first.",
    "deep_search": "Search a specific non-virtual directory such as /var/log with max_depth and max_results limits.",
    "full_disk_search": "Provide a narrower base_path such as /var/log, /home, or a project directory.",
    "sudoers": "Inspect sudo-related configuration read-only and ask an administrator to apply reviewed changes manually.",
    "sshd_config": "Inspect SSH configuration read-only and prepare a manual change plan for administrator review.",
    "privilege": "Create or manage a normal non-privileged user without sudo, wheel, admin, or root access.",
    "bulk_permission": "Limit permission checks to a small path and review the planned file list before any change.",
    "unknown_write": "Use a supported whitelisted operation or make the request read-only.",
    "invalid_username": "Use a normal username like demo_guest that matches the project username rules.",
}

CONFIRM_CREATE_USER = "Confirm creating normal user {username}"
CONFIRM_DELETE_USER = "Confirm deleting normal user {username}"


def normalize_intent_name(value: Any) -> str:
    return str(value or "unknown").strip().lower()


def normalize_path(path: Any) -> str | None:
    if not isinstance(path, str):
        return None

    stripped = path.strip()
    if not stripped:
        return None

    normalized = posixpath.normpath(stripped)
    if stripped.startswith("/") and not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized


def is_same_or_child_path(path: Any, base: str) -> bool:
    normalized = normalize_path(path)
    if normalized is None:
        return False

    protected_base = normalize_path(base)
    if protected_base is None:
        return False

    if protected_base == "/":
        return normalized == "/"
    return normalized == protected_base or normalized.startswith(f"{protected_base}/")


def matches_any_path(path: Any, paths: tuple[str, ...]) -> bool:
    return any(is_same_or_child_path(path, protected_path) for protected_path in paths)


def is_protected_path(path: Any) -> bool:
    return matches_any_path(path, PROTECTED_PATHS)


def is_deep_search_refused_path(path: Any) -> bool:
    return matches_any_path(path, DEEP_SEARCH_REFUSED_PATHS)


def is_sudoers_path(path: Any) -> bool:
    return matches_any_path(path, SUDOERS_PATHS)


def is_sshd_config_path(path: Any) -> bool:
    return matches_any_path(path, SSHD_CONFIG_PATHS)


def contains_write_word(intent_name: str) -> bool:
    parts = intent_name.replace("-", "_").split("_")
    return any(part in WRITE_INTENT_WORDS for part in parts)


def contains_destructive_word(intent_name: str) -> bool:
    parts = intent_name.replace("-", "_").split("_")
    return any(part in DESTRUCTIVE_INTENT_WORDS for part in parts)


def risk_defaults(risk_level: RiskLevel) -> tuple[bool, bool]:
    if risk_level == RiskLevel.S0:
        return True, False
    if risk_level in {RiskLevel.S1, RiskLevel.S2}:
        return True, True
    return False, False
