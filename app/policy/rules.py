from __future__ import annotations

import posixpath
import re
from typing import Any


ENV_PROBE_INTENT = "env_probe"
SEARCH_FILES_INTENT = "search_files"
QUERY_AUDIT_INTENT = "query_audit"
QUERY_DISK_USAGE_INTENT = "query_disk_usage"
QUERY_MEMORY_USAGE_INTENT = "query_memory_usage"
QUERY_PORT_INTENT = "query_port"
QUERY_PROCESS_INTENT = "query_process"
CREATE_USER_INTENT = "create_user"
DELETE_USER_INTENT = "delete_user"
UNKNOWN_INTENT = "unknown"

CANONICAL_READ_ONLY_INTENTS = frozenset(
    {
        ENV_PROBE_INTENT,
        QUERY_AUDIT_INTENT,
        QUERY_DISK_USAGE_INTENT,
        QUERY_MEMORY_USAGE_INTENT,
        QUERY_PORT_INTENT,
        QUERY_PROCESS_INTENT,
        SEARCH_FILES_INTENT,
    }
)

CANONICAL_SEARCH_INTENTS = frozenset({SEARCH_FILES_INTENT})

READ_ONLY_INTENTS = frozenset(
    {
        "audit_query",
        "audit_query_tool",
        "env_probe",
        "env_probe_tool",
        "file_search",
        "file_search_tool",
        "memory_usage_tool",
        "query_audit",
        "query_disk_usage",
        "query_memory_usage",
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

INTENT_ALIASES: dict[str, str] = {
    "audit_query": QUERY_AUDIT_INTENT,
    "audit_query_tool": QUERY_AUDIT_INTENT,
    "env_probe_tool": ENV_PROBE_INTENT,
    "file_search": SEARCH_FILES_INTENT,
    "file_search_tool": SEARCH_FILES_INTENT,
    "memory_usage_tool": QUERY_MEMORY_USAGE_INTENT,
    "add_user": CREATE_USER_INTENT,
    "create_user_tool": CREATE_USER_INTENT,
    "user_create": CREATE_USER_INTENT,
    "delete_user_tool": DELETE_USER_INTENT,
    "remove_user": DELETE_USER_INTENT,
    "user_delete": DELETE_USER_INTENT,
}

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
    "/root",
    "/var/spool/cron",
)

DEEP_SEARCH_REFUSED_PATHS = ("/dev", "/proc", "/sys")
SUDOERS_PATHS = ("/etc/sudoers", "/etc/sudoers.d")
SSHD_CONFIG_PATHS = ("/etc/ssh/sshd_config",)

PRIVILEGED_GROUPS = frozenset(
    {
        "adm",
        "admin",
        "administrator",
        "disk",
        "docker",
        "kmem",
        "lxd",
        "operator",
        "root",
        "shadow",
        "staff",
        "sudo",
        "sys",
        "systemd-journal",
        "wheel",
    }
)

GROUP_CONSTRAINT_KEYS = (
    "groups",
    "group",
    "add_groups",
    "supplementary_groups",
    "secondary_groups",
    "extra_groups",
)

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
        "drop",
        "erase",
        "format",
        "mkfs",
        "overwrite",
        "purge",
        "remove",
        "rm",
        "shred",
        "truncate",
        "unlink",
        "wipe",
    }
)

WRITE_INTENT_WORDS = frozenset(
    {
        "append",
        "change",
        "chgrp",
        "chmod",
        "chown",
        "create",
        "delete",
        "destroy",
        "disable",
        "drop",
        "enable",
        "erase",
        "exec",
        "execute",
        "flush",
        "format",
        "grant",
        "install",
        "kill",
        "mkdir",
        "mkfs",
        "modify",
        "mount",
        "move",
        "overwrite",
        "patch",
        "purge",
        "reboot",
        "reload",
        "remove",
        "rename",
        "reset",
        "restart",
        "revoke",
        "rm",
        "shred",
        "shutdown",
        "start",
        "stop",
        "truncate",
        "uninstall",
        "unlink",
        "unmount",
        "update",
        "upgrade",
        "wipe",
        "write",
    }
)

SAFE_ALTERNATIVES = {
    "protected_path": "Narrow the request to a non-core application path and use a read-only inspection first.",
    "deep_search": "Search a specific non-virtual directory such as /var/log with max_depth and max_results limits.",
    "full_disk_search": "Provide a narrower base_path such as /var/log, /home, or a project directory.",
    "sudoers": "Ask an administrator to review and apply sudo changes manually outside GuardedOps; GuardedOps can only create or manage normal non-privileged users.",
    "sshd_config": "Ask an administrator to review and apply SSH configuration changes manually outside GuardedOps; GuardedOps can only run bounded read-only diagnostics such as searching /var/log.",
    "privilege": "Create or manage a normal non-privileged user without sudo, wheel, admin, or root access.",
    "bulk_permission": "Limit permission checks to a small path and review the planned file list before any change.",
    "unknown_write": "Use a supported whitelisted operation or make the request read-only.",
    "unknown_operation": "Use a supported read-only diagnostic such as disk, memory, process, port, or bounded file search.",
    "invalid_username": "Use a normal username like demo_guest that matches the project username rules.",
}

CONFIRM_CREATE_USER = "确认创建普通用户 {username}"
CONFIRM_DELETE_USER = "确认删除普通用户 {username}"

_LEADING_SLASHES_PATTERN = re.compile(r"^/{2,}")
_NON_ALNUM_PATTERN = re.compile(r"[^a-z0-9]+")
_GROUP_SEPARATOR_PATTERN = re.compile(r"[,;:|+\s]+")
_SUBSTRING_WORD_MIN_LENGTH = 4


def normalize_intent_name(value: Any) -> str:
    raw = str(value if value is not None else UNKNOWN_INTENT).strip().lower()
    if not raw:
        return UNKNOWN_INTENT

    canonical = INTENT_ALIASES.get(raw)
    if canonical is not None:
        return canonical

    underscored = _NON_ALNUM_PATTERN.sub("_", raw).strip("_")
    return INTENT_ALIASES.get(underscored, raw)


def normalize_path(path: Any) -> str | None:
    """Return a canonical absolute-or-relative POSIX path, or None when unusable."""

    if not isinstance(path, str):
        return None

    stripped = path.strip()
    if not stripped:
        return None

    normalized = posixpath.normpath(stripped)
    if stripped.startswith("/") and not normalized.startswith("/"):
        normalized = f"/{normalized}"

    normalized = _LEADING_SLASHES_PATTERN.sub("/", normalized)
    if len(normalized) > 1:
        normalized = normalized.rstrip("/")
    return normalized or "/"


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


def is_top_level_path(path: Any) -> bool:
    normalized = normalize_path(path)
    if normalized is None or not normalized.startswith("/"):
        return False
    return normalized.count("/") == 1


def is_protected_path(path: Any) -> bool:
    return is_top_level_path(path) or matches_any_path(path, PROTECTED_PATHS)


def is_deep_search_refused_path(path: Any) -> bool:
    return matches_any_path(path, DEEP_SEARCH_REFUSED_PATHS)


def is_sudoers_path(path: Any) -> bool:
    return matches_any_path(path, SUDOERS_PATHS)


def is_sshd_config_path(path: Any) -> bool:
    return matches_any_path(path, SSHD_CONFIG_PATHS)


def is_canonical_read_only_intent(intent_name: Any) -> bool:
    return normalize_intent_name(intent_name) in CANONICAL_READ_ONLY_INTENTS


def is_search_intent(intent_name: Any) -> bool:
    return normalize_intent_name(intent_name) in CANONICAL_SEARCH_INTENTS


def normalize_group_tokens(value: Any) -> frozenset[str]:
    """Flatten any group payload shape into lowercase single-group tokens."""

    if value is None:
        return frozenset()

    if isinstance(value, dict):
        raw_values = [str(key) for key in value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw_values = [str(item) for item in value]
    else:
        raw_values = [str(value)]

    tokens: set[str] = set()
    for raw in raw_values:
        for token in _GROUP_SEPARATOR_PATTERN.split(raw.strip().lower()):
            cleaned = token.strip()
            if cleaned:
                tokens.add(cleaned)
    return frozenset(tokens)


def is_privileged_group(token: Any) -> bool:
    cleaned = str(token or "").strip().lower()
    if not cleaned:
        return False
    if cleaned in PRIVILEGED_GROUPS:
        return True
    return cleaned.isdigit()


def has_privileged_group(value: Any) -> bool:
    return any(is_privileged_group(token) for token in normalize_group_tokens(value))


def _intent_word_match(intent_name: Any, words: frozenset[str]) -> bool:
    normalized = normalize_intent_name(intent_name)
    tokens = {token for token in _NON_ALNUM_PATTERN.split(normalized) if token}
    if tokens & words:
        return True

    compact = _NON_ALNUM_PATTERN.sub("", normalized)
    if not compact:
        return False
    return any(len(word) >= _SUBSTRING_WORD_MIN_LENGTH and word in compact for word in words)


def contains_write_word(intent_name: str) -> bool:
    return _intent_word_match(intent_name, WRITE_INTENT_WORDS)


def contains_destructive_word(intent_name: str) -> bool:
    return _intent_word_match(intent_name, DESTRUCTIVE_INTENT_WORDS)
