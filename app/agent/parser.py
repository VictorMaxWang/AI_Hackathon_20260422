from __future__ import annotations

import re
from typing import Any

from app.models import IntentTarget, ParsedIntent


DISK_INTENT = "query_disk_usage"
MEMORY_INTENT = "query_memory_usage"
FILE_INTENT = "search_files"
PROCESS_INTENT = "query_process"
PORT_INTENT = "query_port"
UNKNOWN_INTENT = "unknown"
CREATE_USER_INTENT = "create_user"
DELETE_USER_INTENT = "delete_user"
DELETE_PATH_INTENT = "delete_path"
MODIFY_SUDOERS_INTENT = "modify_sudoers"
GRANT_SUDO_INTENT = "grant_sudo"
MODIFY_SSHD_CONFIG_INTENT = "modify_sshd_config"
BULK_PERMISSION_INTENT = "bulk_permission_change"

USER_CONTEXT_REFS = ("刚才那个用户", "上一个用户", "刚刚创建的用户")
PORT_CONTEXT_REFS = ("刚才那个端口", "上一个端口")
PATH_CONTEXT_REFS = ("刚才那个目录", "上一个目录")


class ReadonlyParser:
    """Rule-based parser for Phase 1 read-only Chinese operations requests."""

    def parse(self, raw_user_input: str, memory: Any | None = None) -> ParsedIntent:
        text = _clean_text(raw_user_input)
        if not text:
            return _unknown(raw_user_input, "empty input")

        contextual_intent = _parse_contextual_reference(text, raw_user_input, memory)
        if contextual_intent is not None:
            return contextual_intent

        dangerous_intent = _parse_dangerous_intent(text, raw_user_input)
        if dangerous_intent is not None:
            return dangerous_intent

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
        if _looks_like_memory_usage_query(text):
            return self._parse_memory_usage(text, raw_user_input)
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

    def _parse_memory_usage(self, text: str, raw_user_input: str) -> ParsedIntent:
        return ParsedIntent(
            intent=MEMORY_INTENT,
            target=IntentTarget(),
            constraints={
                "mode": "summary_with_top_processes",
                "limit": _extract_top_limit(text, 10),
            },
            raw_user_input=raw_user_input,
            confidence=0.88,
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


def parse_readonly_intent(raw_user_input: str, memory: Any | None = None) -> ParsedIntent:
    return ReadonlyParser().parse(raw_user_input, memory=memory)


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


def _parse_contextual_reference(
    text: str,
    raw_user_input: str,
    memory: Any | None,
) -> ParsedIntent | None:
    user_ref = _find_context_ref(text, USER_CONTEXT_REFS)
    if user_ref is not None:
        username = _resolve_memory(memory, "username")
        if not username:
            return _unresolved_context_ref(raw_user_input, user_ref, "username", text)
        if _looks_like_privilege_escalation(text):
            return ParsedIntent(
                intent=GRANT_SUDO_INTENT,
                target=IntentTarget(username=username),
                constraints={
                    "danger_category": "privilege_escalation",
                    "groups": ["sudo"],
                    "privilege": "sudo",
                    "resolved_from_memory": True,
                },
                context_refs=[user_ref],
                requires_write=True,
                raw_user_input=raw_user_input,
                confidence=0.9,
            )
        if _contains_any(text, ["删除", "删掉", "移除", "remove", "delete"]):
            return ParsedIntent(
                intent=DELETE_USER_INTENT,
                target=IntentTarget(username=username),
                constraints={"remove_home": False, "resolved_from_memory": True},
                context_refs=[user_ref],
                requires_write=True,
                raw_user_input=raw_user_input,
                confidence=0.9,
            )
        return ParsedIntent(
            intent=UNKNOWN_INTENT,
            target=IntentTarget(username=username),
            constraints={
                "unsupported_reason": "当前不支持该用户引用操作",
                "resolved_from_memory": True,
            },
            context_refs=[user_ref],
            raw_user_input=raw_user_input,
            confidence=0.4,
        )

    port_ref = _find_context_ref(text, PORT_CONTEXT_REFS)
    if port_ref is not None:
        port = _resolve_memory(memory, "port")
        if port is None:
            return _unresolved_context_ref(raw_user_input, port_ref, "port", text)
        if _detect_write_like_request(text):
            return ParsedIntent(
                intent=UNKNOWN_INTENT,
                target=IntentTarget(port=port),
                constraints={
                    "unsupported_reason": "当前不支持该端口引用写操作",
                    "resolved_from_memory": True,
                },
                context_refs=[port_ref],
                requires_write=True,
                raw_user_input=raw_user_input,
                confidence=0.4,
            )
        return ParsedIntent(
            intent=PORT_INTENT,
            target=IntentTarget(port=port),
            constraints={"resolved_from_memory": True},
            context_refs=[port_ref],
            raw_user_input=raw_user_input,
            confidence=0.9,
        )

    path_ref = _find_context_ref(text, PATH_CONTEXT_REFS)
    if path_ref is not None:
        path = _resolve_memory(memory, "path")
        if not path:
            return _unresolved_context_ref(raw_user_input, path_ref, "path", text)
        if _contains_any(text, ["删除", "删掉", "移除", "清理", "清空", "rm", "wipe", "purge"]):
            return ParsedIntent(
                intent=DELETE_PATH_INTENT,
                target=IntentTarget(path=path, base_paths=[path]),
                constraints={
                    "danger_category": "path_destruction",
                    "resolved_from_memory": True,
                },
                context_refs=[path_ref],
                requires_write=True,
                raw_user_input=raw_user_input,
                confidence=0.85,
            )
        return ParsedIntent(
            intent=FILE_INTENT,
            target=IntentTarget(
                path=path,
                keyword=_extract_file_keyword(text),
                base_paths=[path],
            ),
            constraints={
                "modified_within_days": _extract_modified_days(text),
                "max_results": _extract_max_results(text, default=20),
                "max_depth": _extract_max_depth(text, default=4),
                "resolved_from_memory": True,
            },
            context_refs=[path_ref],
            raw_user_input=raw_user_input,
            confidence=0.86,
        )

    return None


def _find_context_ref(text: str, refs: tuple[str, ...]) -> str | None:
    for ref in refs:
        if ref in text:
            return ref
    return None


def _resolve_memory(memory: Any | None, slot: str) -> Any:
    if memory is None:
        return None
    resolver = getattr(memory, "resolve", None)
    if callable(resolver):
        return resolver(slot)
    return getattr(memory, f"last_{slot}", None)


def _unresolved_context_ref(
    raw_user_input: str,
    ref_text: str,
    ref_type: str,
    text: str,
) -> ParsedIntent:
    return ParsedIntent(
        intent=UNKNOWN_INTENT,
        target=IntentTarget(),
        constraints={
            "unresolved_context_ref": ref_type,
            "context_ref_text": ref_text,
            "unsupported_reason": f"无法解析该引用：{ref_text}",
        },
        context_refs=[ref_text],
        requires_write=_detect_write_like_request(text) is not None,
        raw_user_input=raw_user_input,
        confidence=0.0,
    )


def _parse_dangerous_intent(text: str, raw_user_input: str) -> ParsedIntent | None:
    if _looks_like_sudoers_change(text):
        return ParsedIntent(
            intent=MODIFY_SUDOERS_INTENT,
            target=IntentTarget(
                username=_extract_username_before_sudoers(text),
                path="/etc/sudoers",
                base_paths=["/etc/sudoers"],
            ),
            constraints={"danger_category": "sudoers_change"},
            requires_write=True,
            raw_user_input=raw_user_input,
            confidence=0.95,
        )

    if _looks_like_sshd_config_change(text):
        return ParsedIntent(
            intent=MODIFY_SSHD_CONFIG_INTENT,
            target=IntentTarget(path="/etc/ssh/sshd_config", base_paths=["/etc/ssh/sshd_config"]),
            constraints={
                "danger_category": "sshd_config_change",
                "setting": "PermitRootLogin" if "root" in text.lower() else None,
            },
            requires_write=True,
            raw_user_input=raw_user_input,
            confidence=0.93,
        )

    if _looks_like_privilege_escalation(text):
        return ParsedIntent(
            intent=GRANT_SUDO_INTENT,
            target=IntentTarget(username=_extract_username_for_sudo(text)),
            constraints={
                "danger_category": "privilege_escalation",
                "groups": ["sudo"],
                "privilege": "sudo",
                "bulk": _contains_any(text, ["所有用户", "全部用户", "所有人", "全员"]),
            },
            requires_write=True,
            raw_user_input=raw_user_input,
            confidence=0.92,
        )

    if _looks_like_bulk_permission_change(text):
        path = _extract_path(text)
        return ParsedIntent(
            intent=BULK_PERMISSION_INTENT,
            target=IntentTarget(path=path if path != "/chown" else None),
            constraints={
                "danger_category": "bulk_permission_change",
                "bulk": True,
                "recursive": True,
            },
            requires_write=True,
            raw_user_input=raw_user_input,
            confidence=0.9,
        )

    if _looks_like_core_path_destruction(text):
        path = _extract_dangerous_path(text)
        return ParsedIntent(
            intent=DELETE_PATH_INTENT,
            target=IntentTarget(path=path, base_paths=[path] if path else []),
            constraints={"danger_category": "protected_path_destruction"},
            requires_write=True,
            raw_user_input=raw_user_input,
            confidence=0.9,
        )

    return None


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
        "清空",
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


def _looks_like_sudoers_change(text: str) -> bool:
    return "sudoers" in text.lower()


def _looks_like_sshd_config_change(text: str) -> bool:
    lower_text = text.lower()
    if "sshd_config" in lower_text:
        return True
    return bool(
        "root" in lower_text
        and _contains_any(text, ["远程登录", "ssh 登录", "SSH 登录", "登录"])
        and _contains_any(text, ["允许", "打开", "开启", "启用", "修改"])
    )


def _looks_like_privilege_escalation(text: str) -> bool:
    lower_text = text.lower()
    return bool(
        "sudo" in lower_text
        and _contains_any(text, ["加", "加入", "添加", "给", "权限", "所有用户", "全部用户"])
    )


def _looks_like_bulk_permission_change(text: str) -> bool:
    lower_text = text.lower()
    if "chmod" in lower_text or "chown" in lower_text:
        return _contains_any(text, ["批量", "递归", "-r", "所有", "整个", "全部"]) or "/" in text
    return bool(
        "权限" in text
        and _contains_any(text, ["批量", "递归", "所有", "整个目录", "全部", "都改掉", "都改"])
    )


def _looks_like_core_path_destruction(text: str) -> bool:
    path = _extract_dangerous_path(text)
    if path is None:
        return False
    return _contains_any(
        text,
        [
            "删除",
            "删掉",
            "移除",
            "清理",
            "清空",
            "删",
            "rm",
            "wipe",
            "purge",
        ],
    )


def _extract_dangerous_path(text: str) -> str | None:
    explicit_path = _extract_path(text)
    if explicit_path:
        for protected_path in ["/etc", "/usr", "/boot", "/bin", "/sbin", "/lib", "/lib64", "/"]:
            if explicit_path == protected_path or explicit_path.startswith(f"{protected_path}/"):
                return protected_path if protected_path != "/" else explicit_path
        return explicit_path

    if "系统目录" in text or "核心目录" in text:
        return "/"
    return None


def _extract_username_before_sudoers(text: str) -> str | None:
    match = re.search(r"([a-z_][a-z0-9_-]{2,31})\s*(?:加到|加入|添加到)?\s*sudoers", text, re.IGNORECASE)
    if not match:
        return None
    return match.group(1)


def _extract_username_for_sudo(text: str) -> str | None:
    for pattern in [
        r"把\s*([a-z_][a-z0-9_-]{2,31})\s*(?:加到|加入|添加到)",
        r"给\s*([a-z_][a-z0-9_-]{2,31})\s*sudo",
        r"给\s*([a-z_][a-z0-9_-]{2,31})\s*.*?sudo\s*权限",
    ]:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
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


def _looks_like_memory_usage_query(text: str) -> bool:
    if not _contains_any(text, ["内存", "memory", "mem", "ram"]):
        return False
    if re.search(r"\bpid\s*\d+\b", text, flags=re.IGNORECASE):
        return False
    return not _contains_any(
        text,
        ["进程", "process", "相关进程", "排行", "排名", "最高", "top"],
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
