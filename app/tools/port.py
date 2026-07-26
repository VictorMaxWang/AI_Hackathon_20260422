from __future__ import annotations

import re
from typing import Any

from app.models import CommandResult, ToolResult
from app.tools import safe_run


TOOL_NAME = "port_query_tool"
PROCESS_RE = re.compile(r'"(?P<name>[^"]+)"[^)]*?pid=(?P<pid>\d+)')
MISSING_COMMAND_EXIT_CODES = {-1, 126, 127}
PORT_QUERY_TIMEOUT = 10
PS_LOOKUP_TIMEOUT = 5
MISSING_COMMAND_MARKERS = (
    "command not found",
    "not recognized",
    "no such file",
    "not found",
    "系统找不到指定的文件",
)
UNSUPPORTED_PORT_QUERY_MESSAGE = (
    "当前本地环境缺少端口查询所需的系统工具，因此无法完成该查询。"
    "建议在 Linux/SSH 目标环境中执行，或配置可用的端口查询工具。"
)


def port_query_tool(executor: Any, port: int) -> ToolResult:
    """Query whether a port is listening, preferring ss and falling back to lsof."""

    try:
        effective_port = int(port)
    except (TypeError, ValueError):
        return _refused("port must be an integer", port)
    if effective_port < 0 or effective_port > 65535:
        return _refused("port must be between 0 and 65535", port)

    ss_result = safe_run(executor, ["ss", "-ltnup"], timeout=PORT_QUERY_TIMEOUT)
    if ss_result.success:
        listeners = _parse_ss_output(ss_result.stdout, effective_port)
        _enrich_listeners_with_ps(executor, listeners)
        if not listeners or _has_resolved_pid(listeners):
            return _listening_result(effective_port, listeners, source="ss")
        return _resolve_owner_with_lsof(executor, effective_port, listeners)

    lsof_result = safe_run(executor, _lsof_argv(effective_port), timeout=PORT_QUERY_TIMEOUT)
    if _looks_like_missing_command(ss_result, "ss") and _looks_like_missing_command(
        lsof_result,
        "lsof",
    ):
        return _unsupported_environment_result(
            effective_port,
            missing_tools=["ss", "lsof"],
            attempted_sources=["ss", "lsof"],
        )

    listeners = _parse_lsof_output(lsof_result.stdout, effective_port)
    if listeners or _looks_like_no_lsof_match(lsof_result):
        return _listening_result(
            effective_port,
            listeners,
            source="lsof",
            attempted_sources=["ss", "lsof"],
        )

    message = lsof_result.stderr.strip() or ss_result.stderr.strip()
    if not message:
        message = f"port query failed with exit code {lsof_result.exit_code}"
    return ToolResult(
        tool_name=TOOL_NAME,
        success=False,
        data={
            "status": "error",
            "port": effective_port,
            "listeners": [],
            "count": 0,
            "source": "lsof",
            "attempted_sources": ["ss", "lsof"],
            "exit_code": lsof_result.exit_code,
            "timed_out": lsof_result.timed_out,
        },
        error=message,
    )


def _resolve_owner_with_lsof(
    executor: Any,
    port: int,
    ss_listeners: list[dict[str, Any]],
) -> ToolResult:
    lsof_result = safe_run(executor, _lsof_argv(port), timeout=PORT_QUERY_TIMEOUT)
    lsof_listeners = _parse_lsof_output(lsof_result.stdout, port)
    if _has_resolved_pid(lsof_listeners):
        return _listening_result(
            port,
            lsof_listeners,
            source="lsof",
            attempted_sources=["ss", "lsof"],
        )
    return _listening_result(
        port,
        ss_listeners,
        source="ss",
        attempted_sources=["ss", "lsof"],
    )


def _lsof_argv(port: int) -> list[str]:
    return ["lsof", "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN"]


def _has_resolved_pid(listeners: list[dict[str, Any]]) -> bool:
    return any(isinstance(listener.get("pid"), int) for listener in listeners)


def _parse_ss_output(stdout: str, port: int) -> list[dict[str, Any]]:
    listeners: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.strip() or line.lower().startswith(("netid", "state")):
            continue

        parts = line.split()
        if len(parts) < 5:
            continue

        local_address = parts[4]
        if not _address_matches_port(local_address, port):
            continue

        process_info = " ".join(parts[6:]) if len(parts) > 6 else ""
        processes = _parse_ss_processes(process_info)
        first = processes[0] if processes else {"pid": None, "process_name": None}
        listeners.append(
            {
                "protocol": parts[0],
                "state": parts[1],
                "local_address": local_address,
                "pid": first["pid"],
                "process_name": first["process_name"],
                "processes": processes,
                "user": None,
                "source": "ss",
            }
        )
    return listeners


def _parse_ss_processes(process_info: str) -> list[dict[str, Any]]:
    processes: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for match in PROCESS_RE.finditer(process_info):
        name = match.group("name")
        pid = int(match.group("pid"))
        if (name, pid) in seen:
            continue
        seen.add((name, pid))
        processes.append({"pid": pid, "process_name": name, "user": None})
    return processes


def _enrich_listeners_with_ps(executor: Any, listeners: list[dict[str, Any]]) -> None:
    resolved: dict[int, tuple[str, str | None]] = {}
    for listener in listeners:
        for entry in _listener_process_entries(listener):
            pid = entry.get("pid")
            if not isinstance(pid, int) or pid in resolved:
                continue

            result = safe_run(
                executor,
                ["ps", "-p", str(pid), "-o", "user=", "-o", "comm="],
                timeout=PS_LOOKUP_TIMEOUT,
            )
            if not result.success:
                continue
            parts = result.stdout.strip().split(maxsplit=1)
            if not parts:
                continue
            resolved[pid] = (parts[0], parts[1] if len(parts) > 1 else None)

    for listener in listeners:
        for entry in _listener_process_entries(listener):
            pid = entry.get("pid")
            if not isinstance(pid, int) or pid not in resolved:
                continue
            user, command = resolved[pid]
            entry["user"] = user
            if command and not entry.get("process_name"):
                entry["process_name"] = command


def _listener_process_entries(listener: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = [listener]
    processes = listener.get("processes")
    if isinstance(processes, list):
        entries.extend(entry for entry in processes if isinstance(entry, dict))
    return entries


def _parse_lsof_output(stdout: str, port: int) -> list[dict[str, Any]]:
    listeners: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.strip() or line.upper().startswith("COMMAND"):
            continue

        parts = line.split()
        if len(parts) < 3:
            continue

        name = " ".join(parts[8:]) if len(parts) > 8 else ""
        pid = _parse_int(parts[1])
        listeners.append(
            {
                "protocol": "tcp",
                "state": "LISTEN",
                "local_address": name,
                "pid": pid,
                "process_name": parts[0],
                "processes": [{"pid": pid, "process_name": parts[0], "user": parts[2]}],
                "user": parts[2],
                "source": "lsof",
                "port": port,
            }
        )
    return listeners


def _address_matches_port(address: str, port: int) -> bool:
    return address.rsplit(":", 1)[-1] == str(port)


def _parse_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _looks_like_no_lsof_match(result: CommandResult) -> bool:
    if result.success:
        return True
    return not result.stdout.strip() and not result.stderr.strip()


def _looks_like_missing_command(result: CommandResult, command: str) -> bool:
    if result.exit_code not in MISSING_COMMAND_EXIT_CODES or result.timed_out:
        return False

    stderr = result.stderr.lower()
    if any(marker in stderr for marker in MISSING_COMMAND_MARKERS):
        return True
    pattern = rf"(?:^|[^0-9a-z_-]){re.escape(command.lower())}(?:[^0-9a-z_-]|$)"
    return re.search(pattern, stderr) is not None


def _listening_result(
    port: int,
    listeners: list[dict[str, Any]],
    source: str,
    attempted_sources: list[str] | None = None,
) -> ToolResult:
    status = "listening" if listeners else "not_listening"
    return ToolResult(
        tool_name=TOOL_NAME,
        success=True,
        data={
            "status": status,
            "port": port,
            "listeners": listeners,
            "count": len(listeners),
            "source": source,
            "attempted_sources": attempted_sources or [source],
        },
    )


def _unsupported_environment_result(
    port: int,
    *,
    missing_tools: list[str],
    attempted_sources: list[str],
) -> ToolResult:
    return ToolResult(
        tool_name=TOOL_NAME,
        success=False,
        data={
            "status": "unsupported_on_current_environment",
            "port": port,
            "listeners": [],
            "count": 0,
            "source": "none",
            "missing_tools": missing_tools,
            "attempted_sources": attempted_sources,
            "reason": "missing_port_query_tools",
        },
        error=UNSUPPORTED_PORT_QUERY_MESSAGE,
    )


def _refused(reason: str, port: Any) -> ToolResult:
    return ToolResult(
        tool_name=TOOL_NAME,
        success=False,
        data={
            "status": "refused",
            "port": port,
            "listeners": [],
            "count": 0,
            "reason": reason,
        },
        error=reason,
    )
