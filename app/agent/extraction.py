from __future__ import annotations

import re
from typing import Any

from app.models.evolution import WORKFLOW_TOOL_INTENTS


INTENT_TOOL_NAMES: dict[str, str] = {
    intent: tool_name for tool_name, intent in WORKFLOW_TOOL_INTENTS.items()
}

USER_CONTEXT_REFS: tuple[str, ...] = (
    "刚才那个用户",
    "上一个用户",
    "刚刚创建的用户",
    "刚才创建的用户",
)
PORT_CONTEXT_REFS: tuple[str, ...] = ("刚才那个端口", "上一个端口")
PATH_CONTEXT_REFS: tuple[str, ...] = ("刚才那个目录", "上一个目录")

CONFIRMATION_PREFIXES: tuple[str, ...] = ("确认", "confirm")

_NEGATED_PRIVILEGE_PATTERNS = (
    re.compile(
        r"(?:不要|不用|无需|不需要|别|不能|不可|不给|无|没有)\s*(?:给\s*)?"
        r"(?:[a-z_][a-z0-9_-]{2,31}\s*)?"
        r"(?:sudo|管理员|admin|administrator|wheel|root)\s*(?:权限|访问)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"不\s*(?:加入|加到|添加到|加进)\s*(?:sudo|wheel|admin|administrator|管理员)",
        re.IGNORECASE,
    ),
)
_PRIVILEGE_TOKEN_PATTERN = re.compile(
    r"(?:\bsudo\b|\bsudoers\b|\bwheel\b|\badmin\b|\badministrator\b|\broot\b"
    r"|管理员|超级用户|特权|提权)",
    re.IGNORECASE,
)
_GROUP_ASSIGNMENT_PATTERN = re.compile(
    r"(?:加入|加到|添加到|加进|放到|放进|移入|归到)\s*[^\s，,。；;、]{0,16}?组"
)
_USERNAME_PATTERN = re.compile(
    r"(?:普通用户|测试用户|用户)\s*([a-z_][a-z0-9_-]{2,31})",
    re.IGNORECASE,
)
_NORMAL_USER_TOKEN_PATTERN = re.compile(r"普通用户\s*([^\s，,。、]+)", re.IGNORECASE)
_PATH_PATTERN = re.compile(r"(/[^\s，,。；;、]+)")
_PORT_PATTERNS = (
    re.compile(r"(?<!\d)(\d+)(?!\d)\s*端口"),
    re.compile(r"端口\s*(?<!\d)(\d+)(?!\d)"),
    re.compile(r"\bport\s*(?<!\d)(\d+)(?!\d)", re.IGNORECASE),
)
_CLAUSE_SEPARATOR_PATTERN = re.compile(
    r"(?:然后|接着|随后|之后|再|并且|同时|以及|，|,|；|;|。|\bthen\b|\band\b)",
    re.IGNORECASE,
)

MIN_PORT = 0
MAX_PORT = 65535


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def contains_any(text: str, needles: list[str] | tuple[str, ...]) -> bool:
    lower_text = str(text or "").lower()
    return any(str(needle).lower() in lower_text for needle in needles)


def find_context_ref(text: str, refs: tuple[str, ...]) -> str | None:
    for ref in refs:
        if ref in str(text or ""):
            return ref
    return None


def strip_negated_privilege_constraints(text: str) -> str:
    cleaned = str(text or "")
    for pattern in _NEGATED_PRIVILEGE_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    return cleaned


def mentions_privilege_token(text: str) -> bool:
    """Report whether the request still names a privilege or group target.

    Used as a fail-closed gate: a request whose privileged part is not
    recognized must be refused, never silently narrowed to a plain action.
    """

    scan_text = strip_negated_privilege_constraints(text)
    return bool(
        _PRIVILEGE_TOKEN_PATTERN.search(scan_text)
        or _GROUP_ASSIGNMENT_PATTERN.search(scan_text)
    )


def looks_like_privilege_escalation(text: str) -> bool:
    scan_text = strip_negated_privilege_constraints(text)
    lower_text = scan_text.lower()
    return bool(
        (
            "sudo" in lower_text
            and contains_any(scan_text, ["加", "加入", "添加", "给", "权限", "所有用户", "全部用户"])
        )
        or (
            contains_any(scan_text, ["管理员权限", "root 权限", "root权限"])
            and contains_any(scan_text, ["给", "授予", "提升", "加", "加入", "添加", "设为", "设置"])
        )
        or (
            re.search(r"\b(?:admin|administrator|wheel)\b", lower_text) is not None
            and contains_any(
                scan_text,
                ["给", "授予", "提升", "加", "加入", "添加", "设为", "设置", "权限"],
            )
        )
        or ("提升" in scan_text and "权限" in scan_text)
    )


def looks_like_confirmation_reply(text: str) -> bool:
    """Report whether the input belongs to the reserved confirmation vocabulary."""

    stripped = clean_text(text)
    if not stripped:
        return False
    lower_text = stripped.lower()
    return any(lower_text.startswith(prefix) for prefix in CONFIRMATION_PREFIXES)


def extract_username(text: str) -> str | None:
    match = _USERNAME_PATTERN.search(str(text or ""))
    if not match:
        return None
    return match.group(1)


def extract_all_usernames(text: str) -> list[str]:
    found: list[str] = []
    for match in _USERNAME_PATTERN.finditer(str(text or "")):
        username = match.group(1)
        if username not in found:
            found.append(username)
    return found


def extract_normal_user_token(text: str) -> str | None:
    """Extract the raw token after 普通用户 so policy validation can reject it."""

    match = _NORMAL_USER_TOKEN_PATTERN.search(str(text or ""))
    if not match:
        return None
    return match.group(1).strip() or None


def extract_path(text: str) -> str | None:
    match = _PATH_PATTERN.search(str(text or ""))
    if not match:
        return None
    return match.group(1).rstrip("，,。；;、") or None


def extract_port(text: str) -> int | None:
    """Extract a port number without ever truncating an over-long digit run."""

    for pattern in _PORT_PATTERNS:
        match = pattern.search(str(text or ""))
        if not match:
            continue
        port = int(match.group(1))
        if MIN_PORT <= port <= MAX_PORT:
            return port
        return None
    return None


def split_clauses(text: str) -> list[str]:
    """Split a request on sequencing and clause boundaries, preserving order."""

    parts = _CLAUSE_SEPARATOR_PATTERN.split(str(text or ""))
    return [clause for clause in (part.strip(" 　\t") for part in parts) if clause]


def tool_name_for_intent(intent: str) -> str | None:
    return INTENT_TOOL_NAMES.get(str(intent or ""))
