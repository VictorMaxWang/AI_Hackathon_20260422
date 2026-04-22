from __future__ import annotations

import re
from typing import Any

from app.models import IntentTarget, ParsedIntent


DISK_INTENT = "query_disk_usage"
FILE_INTENT = "search_files"
PROCESS_INTENT = "query_process"
PORT_INTENT = "query_port"
UNKNOWN_INTENT = "unknown"


class ReadonlyParser:
    """Rule-based parser for Phase 1 read-only Chinese operations requests."""

    def parse(self, raw_user_input: str) -> ParsedIntent:
        text = _clean_text(raw_user_input)
        if not text:
            return _unknown(raw_user_input, "empty input")

        write_reason = _detect_write_like_request(_write_scan_text(text))
        if write_reason:
            return ParsedIntent(
                intent=UNKNOWN_INTENT,
                target=IntentTarget(),
                constraints={"unsupported_reason": write_reason},
                requires_write=True,
                raw_user_input=raw_user_input,
                confidence=0.75,
            )

        if _looks_like_file_search(text):
            return self._parse_file_search(text, raw_user_input)
        if _looks_like_port_query(text):
            return self._parse_port_query(text, raw_user_input)
        if _looks_like_process_query(text):
            return self._parse_process_query(text, raw_user_input)
        if _looks_like_disk_query(text):
            return ParsedIntent(
                intent=DISK_INTENT,
                target=IntentTarget(),
                constraints={
                    "focus": "tightest_mount" if "紧张" in text else "overview",
                },
                raw_user_input=raw_user_input,
                confidence=0.9,
            )

        return _unknown(raw_user_input, "unsupported read-only request")

    def _parse_file_search(self, text: str, raw_user_input: str) -> ParsedIntent:
        base_path = _extract_path(text)
        keyword = _extract_file_keyword(text)
        modified_days = _extract_modified_days(text)
        max_results = _extract_max_results(text, default=20)
        max_depth = _extract_max_depth(text, default=4)

        confidence = 0.88 if base_path else 0.55
        constraints: dict[str, Any] = {
            "modified_within_days": modified_days,
            "max_results": max_results,
            "max_depth": max_depth,
        }
        if base_path is None:
            constraints["missing"] = "base_path"

        return ParsedIntent(
            intent=FILE_INTENT,
            target=IntentTarget(
                path=base_path,
                keyword=keyword,
                base_paths=[base_path] if base_path else [],
            ),
            constraints=constraints,
            raw_user_input=raw_user_input,
            confidence=confidence,
        )

    def _parse_process_query(self, text: str, raw_user_input: str) -> ParsedIntent:
        pid = _extract_pid(text)
        if pid is not None:
            return ParsedIntent(
                intent=PROCESS_INTENT,
                target=IntentTarget(pid=pid),
                constraints={"mode": "pid", "limit": 10},
                raw_user_input=raw_user_input,
                confidence=0.9,
            )

        keyword = _extract_process_keyword(text)
        if keyword:
            return ParsedIntent(
                intent=PROCESS_INTENT,
                target=IntentTarget(keyword=keyword),
                constraints={"mode": "keyword", "limit": _extract_top_limit(text, 10)},
                raw_user_input=raw_user_input,
                confidence=0.85,
            )

        mode = "memory" if _contains_any(text, ["内存", "memory", "mem"]) else "cpu"
        return ParsedIntent(
            intent=PROCESS_INTENT,
            target=IntentTarget(),
            constraints={"mode": mode, "limit": _extract_top_limit(text, 10)},
            raw_user_input=raw_user_input,
            confidence=0.82,
        )

    def _parse_port_query(self, text: str, raw_user_input: str) -> ParsedIntent:
        port = _extract_port(text)
        constraints: dict[str, Any] = {}
        if port is None:
            constraints["missing"] = "port"

        return ParsedIntent(
            intent=PORT_INTENT,
            target=IntentTarget(port=port),
            constraints=constraints,
            raw_user_input=raw_user_input,
            confidence=0.85 if port is not None else 0.5,
        )


def parse_readonly_intent(raw_user_input: str) -> ParsedIntent:
    return ReadonlyParser().parse(raw_user_input)


def _clean_text(value: str) -> str:
    return str(value or "").strip()


def _unknown(raw_user_input: str, reason: str) -> ParsedIntent:
    return ParsedIntent(
        intent=UNKNOWN_INTENT,
        target=IntentTarget(),
        constraints={"unsupported_reason": reason},
        raw_user_input=raw_user_input,
        confidence=0.2,
    )


def _detect_write_like_request(text: str) -> str | None:
    write_keywords = [
        "创建",
        "新增",
        "删除",
        "删掉",
        "移除",
        "修改",
        "更改",
        "写入",
        "清理",
        "杀掉",
        "重启",
        "启动",
        "停止",
        "安装",
        "卸载",
        "chmod",
        "chown",
        "sudoers",
        "useradd",
        "userdel",
    ]
    if _contains_any(text, write_keywords):
        return "当前只支持只读基础能力，写操作不在 P1-T05 范围内"
    return None


def _write_scan_text(text: str) -> str:
    return text.replace("修改过", "")


def _looks_like_disk_query(text: str) -> bool:
    return _contains_any(text, ["磁盘", "磁盘空间", "挂载点", "空间怎么样"])


def _looks_like_file_search(text: str) -> bool:
    if "文件" not in text and "目录" not in text:
        return False
    return _contains_any(text, ["找", "检索", "搜索", "查找"])


def _looks_like_process_query(text: str) -> bool:
    return bool(
        re.search(r"\bpid\s*\d+\b", text, flags=re.IGNORECASE)
        or _contains_any(text, ["进程", "CPU", "cpu", "内存", "相关进程"])
    )


def _looks_like_port_query(text: str) -> bool:
    return "端口" in text


def _contains_any(text: str, needles: list[str]) -> bool:
    lower_text = text.lower()
    return any(needle.lower() in lower_text for needle in needles)


def _extract_path(text: str) -> str | None:
    match = re.search(r"(/[^\s，,。；;、]+)", text)
    if not match:
        return None
    return match.group(1).rstrip("，,。；;、")


def _extract_modified_days(text: str) -> int | None:
    match = re.search(r"最近\s*(\d+)\s*天", text)
    if not match:
        return None
    return int(match.group(1))


def _extract_max_results(text: str, default: int) -> int:
    patterns = [
        r"最多(?:返回|显示)?\s*(\d+)\s*(?:条|个|项)?",
        r"最大(?:返回|显示)?\s*(\d+)\s*(?:条|个|项)?",
        r"前\s*(\d+)\s*(?:条|个|项)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return default


def _extract_max_depth(text: str, default: int) -> int:
    match = re.search(r"(?:最大)?深度\s*(\d+)", text)
    if not match:
        return default
    return int(match.group(1))


def _extract_file_keyword(text: str) -> str | None:
    explicit = re.search(r"文件名(?:包含|包括|带有)\s*([^\s，,。；;、的]+)", text)
    if explicit:
        return explicit.group(1).strip()

    after_find = re.search(r"找\s+(.+?)\s*文件", text)
    if not after_find:
        return None

    candidate = after_find.group(1)
    candidate = re.sub(r"最近\s*\d+\s*天修改过[，,、]?", "", candidate)
    candidate = re.sub(r"文件名(?:包含|包括|带有)\s*", "", candidate)
    candidate = candidate.strip(" 的，,。；;、")
    if not candidate or "/" in candidate or " " in candidate:
        return None
    return candidate


def _extract_pid(text: str) -> int | None:
    match = re.search(r"\bpid\s*(\d+)\b", text, flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def _extract_process_keyword(text: str) -> str | None:
    patterns = [
        r"查(?:一下)?\s*([A-Za-z0-9_.:-]+)\s*相关进程",
        r"看(?:一下)?\s*([A-Za-z0-9_.:-]+)\s*相关进程",
        r"([A-Za-z0-9_.:-]+)\s*相关进程",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def _extract_top_limit(text: str, default: int) -> int:
    match = re.search(r"最高的?\s*(\d+)\s*个", text)
    if match:
        return int(match.group(1))
    return _extract_max_results(text, default)


def _extract_port(text: str) -> int | None:
    match = re.search(r"(\d{1,5})\s*端口", text)
    if not match:
        return None
    port = int(match.group(1))
    if port < 0 or port > 65535:
        return None
    return port
