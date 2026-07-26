from __future__ import annotations

import re
from typing import Any

from app.models.evolution import WORKFLOW_TOOL_INTENTS
from app.policy.rules import PRIVILEGED_GROUPS


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

_ASCII_WORD_START = r"(?<![0-9A-Za-z_])"
_ASCII_WORD_END = r"(?![0-9A-Za-z_])"
_PRIVILEGED_GROUP_ALTERNATION = "|".join(
    re.escape(group) for group in sorted(PRIVILEGED_GROUPS, key=len, reverse=True)
)
_PRIVILEGED_GROUP_WORD = (
    rf"{_ASCII_WORD_START}(?:{_PRIVILEGED_GROUP_ALTERNATION}){_ASCII_WORD_END}"
)
_CHINESE_PRIVILEGE_TOKENS = (
    "管理员",
    "超级用户",
    "特权",
    "提权",
    "提升权限",
    "最高权限",
    "完全访问",
    "全部权限",
    "所有权限",
    "免密",
)
_PRIVILEGE_TOKEN_PATTERN = re.compile(
    _PRIVILEGED_GROUP_WORD
    + rf"|{_ASCII_WORD_START}sudoers{_ASCII_WORD_END}"
    rf"|{_ASCII_WORD_START}super\s*users?{_ASCII_WORD_END}"
    rf"|{_ASCII_WORD_START}(?:privileg|elevat|escalat)[a-z]*"
    rf"|{_ASCII_WORD_START}unrestricted{_ASCII_WORD_END}"
    rf"|{_ASCII_WORD_START}all\s+permissions{_ASCII_WORD_END}"
    rf"|{_ASCII_WORD_START}full\s+(?:system\s+|server\s+|machine\s+|root\s+)?access{_ASCII_WORD_END}"
    rf"|{_ASCII_WORD_START}(?:uid|gid)\s*[:=]?\s*0{_ASCII_WORD_END}"
    rf"|{'|'.join(_CHINESE_PRIVILEGE_TOKENS)}",
    re.IGNORECASE,
)
_GROUP_ASSIGNMENT_PATTERN = re.compile(
    r"(?:加入|加到|添加到|加进|放到|放进|移入|归到)\s*"
    r"([^，,。；;、]{0,24}?)\s*"
    r"(?:用户组|用户群组|群组|组)"
)
_PLAIN_GROUP_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_NEGATION_CLAUSE_LEAD = r"(?:^|[\s，,。；;、（）()])"
_NEGATED_PRIVILEGE_PATTERNS = (
    re.compile(
        _NEGATION_CLAUSE_LEAD
        + r"(?:不要|不用|无需|无须|不需要|别|不能|不可|不给|没有|无)\s*(?:给\s*)?"
        r"(?:[a-z_][a-z0-9_-]{2,31}\s*)?"
        rf"(?:{_PRIVILEGED_GROUP_WORD}|管理员|超级用户|特权)"
        r"\s*(?:权限|访问)?",
        re.IGNORECASE,
    ),
    re.compile(
        _NEGATION_CLAUSE_LEAD
        + r"不(?:要|用|能|可|需要)?\s*(?:加入|加到|添加到|加进|放到|放进|移入|归到)\s*"
        rf"(?:{_PRIVILEGED_GROUP_WORD}|管理员|超级用户)"
        r"\s*(?:用户)?(?:组|群组)?",
        re.IGNORECASE,
    ),
    re.compile(
        rf"{_ASCII_WORD_START}(?:non[-\s]?privileg|unprivileg)[a-z]*{_ASCII_WORD_END}",
        re.IGNORECASE,
    ),
)
_PRIVILEGE_GRANT_VERBS = (
    "给",
    "授予",
    "提升",
    "加",
    "加入",
    "添加",
    "设为",
    "设成",
    "设置",
    "权限",
)
_PHRASE_PRIVILEGE_TOKENS = (
    "管理员权限",
    "root 权限",
    "root权限",
    "超级用户",
    "最高权限",
    "完全访问",
    "全部权限",
    "所有权限",
)
_ENGLISH_PRIVILEGE_WORD_PATTERN = re.compile(
    rf"{_ASCII_WORD_START}(?:administrator|admin|wheel|superuser|super\s+user)"
    rf"{_ASCII_WORD_END}",
    re.IGNORECASE,
)
_ELEVATED_ACCESS_PATTERN = re.compile(
    rf"{_ASCII_WORD_START}(?:elevated|elevate|escalated|escalate|privileged|privilege)\s+"
    rf"(?:access|privileges?|permissions?|rights?){_ASCII_WORD_END}",
    re.IGNORECASE,
)
_CONFIRMATION_SENTENCE_PATTERN = re.compile(
    r"^(?:确认|confirm)\s*(?:一下)?\s*"
    r"(?:创建|新建|新增|添加|删除|删掉|移除|create|delete|remove|add)\s*"
    r"(?:普通用户|测试用户|用户|user)\s*"
    r"[a-z_][a-z0-9_-]{2,31}\s*[。.！!]?$",
    re.IGNORECASE,
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
    """Drop the clauses that explicitly refuse a privilege.

    The rewritten text is only ever a hint. It never decides on its own that a
    privilege was declined: :func:`mentions_privilege_token` scores the raw
    request and accepts the negated reading only for the tokens an explicit
    refusal actually covers.
    """

    cleaned = str(text or "")
    for pattern in _NEGATED_PRIVILEGE_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    return cleaned


def _privilege_signal_spans(text: str) -> list[tuple[int, int]]:
    spans = [match.span() for match in _PRIVILEGE_TOKEN_PATTERN.finditer(text)]
    spans.extend(
        match.span()
        for match in _GROUP_ASSIGNMENT_PATTERN.finditer(text)
        if not _is_plain_group_name(match.group(1))
    )
    return spans


def _negation_spans(text: str) -> list[tuple[int, int]]:
    return [
        match.span()
        for pattern in _NEGATED_PRIVILEGE_PATTERNS
        for match in pattern.finditer(text)
    ]


def _is_plain_group_name(token: str) -> bool:
    return _PLAIN_GROUP_NAME_PATTERN.match(str(token or "").strip().lower()) is not None


def _is_covered(span: tuple[int, int], covering_spans: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(low <= start and end <= high for low, high in covering_spans)


def mentions_privilege_token(text: str) -> bool:
    """Report whether the request still names a privilege or group target.

    Used as a fail-closed gate: a request whose privileged part is not
    recognized must be refused, never silently narrowed to a plain action.
    Every privilege token is scored on the raw request, and one is only read as
    declined when an explicit refusal clause covers that same token.
    """

    raw_text = str(text or "")
    spans = _privilege_signal_spans(raw_text)
    if not spans:
        return False

    negated = _negation_spans(raw_text)
    return any(not _is_covered(span, negated) for span in spans)


def extract_assigned_groups(text: str) -> list[str]:
    """Return the group names a request asks to put the user into.

    Handing the actual group name to the policy engine keeps the decision with
    ``has_privileged_group`` instead of with whatever a text pattern happened to
    recognize. A group an explicit refusal clause covers is not a request.
    """

    raw_text = str(text or "")
    negated = _negation_spans(raw_text)
    found: list[str] = []
    for match in _GROUP_ASSIGNMENT_PATTERN.finditer(raw_text):
        token = match.group(1).strip().lower()
        if not _is_plain_group_name(token) or token in found:
            continue
        if _is_covered(match.span(), negated):
            continue
        found.append(token)
    return found


def _privilege_escalation_signal(text: str) -> bool:
    lower_text = text.lower()
    return bool(
        (
            "sudo" in lower_text
            and contains_any(text, ["加", "加入", "添加", "给", "权限", "所有用户", "全部用户"])
        )
        or (
            contains_any(text, list(_PHRASE_PRIVILEGE_TOKENS))
            and contains_any(text, list(_PRIVILEGE_GRANT_VERBS))
        )
        or (
            _ENGLISH_PRIVILEGE_WORD_PATTERN.search(lower_text) is not None
            and contains_any(text, list(_PRIVILEGE_GRANT_VERBS))
        )
        or _ELEVATED_ACCESS_PATTERN.search(lower_text) is not None
        or ("提升" in text and "权限" in text)
        or "提权" in text
    )


def looks_like_privilege_escalation(text: str) -> bool:
    raw_text = str(text or "")
    if not _privilege_escalation_signal(raw_text):
        return False
    if mentions_privilege_token(raw_text):
        return True
    return _privilege_escalation_signal(strip_negated_privilege_constraints(raw_text))


def looks_like_confirmation_reply(text: str) -> bool:
    """Report whether the input has the shape of a confirmation sentence.

    Reserving the whole 确认 prefix refuses ordinary read-only requests such as
    "确认一下磁盘使用情况"; only the confirmation sentence itself is reserved.
    """

    stripped = clean_text(text)
    if not stripped:
        return False
    return _CONFIRMATION_SENTENCE_PATTERN.match(stripped) is not None


def starts_with_confirmation_prefix(text: str) -> bool:
    """Report whether the input opens with reserved confirmation vocabulary."""

    lower_text = clean_text(text).lower()
    if not lower_text:
        return False
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
