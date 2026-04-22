from __future__ import annotations

from typing import Any

from app.models import CommandResult, ToolResult


TOOL_NAME = "process_query_tool"
MAX_LIMIT = 50


def process_query_tool(
    executor: Any,
    mode: str = "cpu",
    limit: int = 10,
    keyword: str | None = None,
    pid: int | None = None,
) -> ToolResult:
    """Query processes with fixed ps argv vectors."""

    normalized_mode = _normalize_mode(mode)
    effective_limit = _bounded_limit(limit)

    if normalized_mode == "pid":
        if pid is None:
            return _refused("pid is required for pid process query")
        try:
            effective_pid = int(pid)
        except (TypeError, ValueError):
            return _refused("pid must be an integer")
        if effective_pid < 0:
            return _refused("pid must not be negative")
        argv = _ps_pid_argv(effective_pid)
    elif normalized_mode == "keyword":
        if not keyword:
            return _refused("keyword is required for keyword process query")
        argv = _ps_all_argv()
    else:
        sort_key = "-pcpu" if normalized_mode == "cpu" else "-pmem"
        argv = [*_ps_all_argv(), f"--sort={sort_key}"]

    result = executor.run(argv, timeout=10)
    if not result.success:
        return _command_error(result, normalized_mode)

    processes = _parse_ps_output(result.stdout)
    if normalized_mode == "keyword":
        needle = str(keyword).lower()
        processes = [
            process
            for process in processes
            if needle in str(process["command"]).lower()
            or needle in str(process["args"]).lower()
        ]

    truncated = len(processes) > effective_limit
    limited_processes = processes[:effective_limit]

    return ToolResult(
        tool_name=TOOL_NAME,
        success=True,
        data={
            "status": "ok",
            "mode": normalized_mode,
            "keyword": keyword if normalized_mode == "keyword" else None,
            "pid": pid if normalized_mode == "pid" else None,
            "processes": limited_processes,
            "count": len(limited_processes),
            "limit": effective_limit,
            "truncated": truncated,
        },
    )


def _normalize_mode(mode: str) -> str:
    normalized = str(mode or "cpu").lower()
    aliases = {
        "top_cpu": "cpu",
        "cpu_top": "cpu",
        "top_memory": "memory",
        "memory_top": "memory",
        "mem": "memory",
        "search": "keyword",
        "by_keyword": "keyword",
        "by_pid": "pid",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"cpu", "memory", "keyword", "pid"}:
        return "cpu"
    return normalized


def _bounded_limit(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 10
    return min(max(number, 1), MAX_LIMIT)


def _ps_all_argv() -> list[str]:
    return [
        "ps",
        "-eo",
        "pid=",
        "-o",
        "user=",
        "-o",
        "pcpu=",
        "-o",
        "pmem=",
        "-o",
        "comm=",
        "-o",
        "args=",
    ]


def _ps_pid_argv(pid: int) -> list[str]:
    return [
        "ps",
        "-p",
        str(pid),
        "-o",
        "pid=",
        "-o",
        "user=",
        "-o",
        "pcpu=",
        "-o",
        "pmem=",
        "-o",
        "comm=",
        "-o",
        "args=",
    ]


def _parse_ps_output(stdout: str) -> list[dict[str, Any]]:
    processes: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        parts = line.strip().split(maxsplit=5)
        if len(parts) < 5:
            continue

        processes.append(
            {
                "pid": _parse_int(parts[0]),
                "user": parts[1],
                "cpu_percent": _parse_float(parts[2]),
                "memory_percent": _parse_float(parts[3]),
                "command": parts[4],
                "args": parts[5] if len(parts) > 5 else "",
            }
        )
    return processes


def _parse_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _refused(reason: str) -> ToolResult:
    return ToolResult(
        tool_name=TOOL_NAME,
        success=False,
        data={
            "status": "refused",
            "processes": [],
            "count": 0,
            "reason": reason,
        },
        error=reason,
    )


def _command_error(result: CommandResult, mode: str) -> ToolResult:
    message = result.stderr.strip() or f"command failed with exit code {result.exit_code}"
    return ToolResult(
        tool_name=TOOL_NAME,
        success=False,
        data={
            "status": "error",
            "mode": mode,
            "processes": [],
            "count": 0,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
        },
        error=message,
    )
