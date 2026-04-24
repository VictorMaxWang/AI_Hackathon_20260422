from __future__ import annotations

import re
from typing import Any

from app.models import CommandResult, ToolResult


TOOL_NAME = "port_query_tool"
PROCESS_RE = re.compile(r'"(?P<name>[^"]+)".*?pid=(?P<pid>\d+)')
MISSING_COMMAND_EXIT_CODES = {-1, 126, 127}
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

    ss_result = executor.run(["ss", "-ltnup"], timeout=10)
    if ss_result.success:
        listeners = _parse_ss_output(ss_result.stdout, effective_port)
        _enrich_listeners_with_ps(executor, listeners)
        return _listening_result(effective_port, listeners, source="ss")

    lsof_result = executor.run(
        ["lsof", "-nP", f"-iTCP:{effective_port}", "-sTCP:LISTEN"],
        timeout=10,
    )
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
        return _listening_result(effective_port, listeners, source="lsof")

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
            "source": "lsof",
            "exit_code": lsof_result.exit_code,
            "timed_out": lsof_result.timed_out,
        },
        error=message,
    )


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
        process_name, pid = _parse_ss_process(process_info)
        listeners.append(
            {
                "protocol": parts[0],
                "state": parts[1],
                "local_address": local_address,
                "pid": pid,
                "process_name": process_name,
                "user": None,
                "source": "ss",
            }
        )
    return listeners


def _parse_ss_process(process_info: str) -> tuple[str | None, int | None]:
    match = PROCESS_RE.search(process_info)
    if not match:
        return None, None
    return match.group("name"), int(match.group("pid"))


def _enrich_listeners_with_ps(executor: Any, listeners: list[dict[str, Any]]) -> None:
    seen: set[int] = set()
    for listener in listeners:
        pid = listener.get("pid")
        if not isinstance(pid, int) or pid in seen:
            continue
        seen.add(pid)

        result = executor.run(["ps", "-p", str(pid), "-o", "user=", "-o", "comm="], timeout=5)
        if not result.success:
            continue
        parts = result.stdout.strip().split(maxsplit=1)
        if not parts:
            continue

        for matching_listener in listeners:
            if matching_listener.get("pid") != pid:
                continue
            matching_listener["user"] = parts[0]
            if len(parts) > 1 and not matching_listener.get("process_name"):
                matching_listener["process_name"] = parts[1]


def _parse_lsof_output(stdout: str, port: int) -> list[dict[str, Any]]:
    listeners: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.strip() or line.upper().startswith("COMMAND"):
            continue

        parts = line.split()
        if len(parts) < 3:
            continue

        name = " ".join(parts[8:]) if len(parts) > 8 else ""
        listeners.append(
            {
                "protocol": "tcp",
                "state": "LISTEN",
                "local_address": name,
                "pid": _parse_int(parts[1]),
                "process_name": parts[0],
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
    stderr = result.stderr.lower()
    return result.exit_code in MISSING_COMMAND_EXIT_CODES and (
        "command not found" in stderr
        or "not recognized" in stderr
        or "no such file" in stderr
        or "系统找不到指定的文件" in stderr
        or command.lower() in stderr
    )


def _listening_result(port: int, listeners: list[dict[str, Any]], source: str) -> ToolResult:
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
