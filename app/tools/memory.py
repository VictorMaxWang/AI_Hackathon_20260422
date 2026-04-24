from __future__ import annotations

import json
from typing import Any

from app.models import CommandResult, ToolResult
from app.tools.process import process_query_tool


TOOL_NAME = "memory_usage_tool"
MAX_LIMIT = 50
DEFAULT_LIMIT = 10


def memory_usage_tool(executor: Any, limit: int = DEFAULT_LIMIT) -> ToolResult:
    """Collect system memory usage and a best-effort memory process ranking."""

    effective_limit = _bounded_limit(limit)

    linux_result = executor.run(["cat", "/proc/meminfo"], timeout=10)
    if linux_result.success:
        linux_payload = _parse_linux_meminfo(linux_result.stdout)
        if linux_payload is not None:
            _attach_linux_process_ranking(executor, linux_payload, effective_limit)
            return _success(linux_payload)

    windows_result = executor.run(_windows_memory_argv(), timeout=10)
    if windows_result.success:
        windows_payload = _parse_windows_memory_json(windows_result.stdout)
        if windows_payload is not None:
            _attach_windows_process_ranking(executor, windows_payload, effective_limit)
            return _success(windows_payload)

    return _error(
        "unable to collect memory usage",
        linux_result=linux_result,
        windows_result=windows_result,
    )


def _success(payload: dict[str, Any]) -> ToolResult:
    payload.setdefault("status", "ok")
    payload.setdefault("top_processes", [])
    payload.setdefault("process_source", "")
    payload.setdefault("process_error", "")
    return ToolResult(tool_name=TOOL_NAME, success=True, data=payload)


def _parse_linux_meminfo(stdout: str) -> dict[str, Any] | None:
    values: dict[str, int] = {}
    for line in stdout.splitlines():
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        parts = raw_value.strip().split()
        if not parts:
            continue
        try:
            values[key] = int(parts[0]) * 1024
        except ValueError:
            continue

    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if available is None:
        fallback_available = [
            values.get("MemFree"),
            values.get("Buffers"),
            values.get("Cached"),
            values.get("SReclaimable"),
        ]
        available = sum(item for item in fallback_available if item is not None)
    if not total or available is None:
        return None
    return _memory_payload(total, available, source="/proc/meminfo")


def _parse_windows_memory_json(stdout: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        return None
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        return None

    total_kb = _number_from_payload(
        payload,
        "TotalVisibleMemorySize",
        "TotalVisibleMemorySizeKB",
        "total_visible_memory_size_kb",
    )
    available_kb = _number_from_payload(
        payload,
        "FreePhysicalMemory",
        "FreePhysicalMemoryKB",
        "free_physical_memory_kb",
    )
    if total_kb is None or available_kb is None:
        return None
    return _memory_payload(int(total_kb * 1024), int(available_kb * 1024), source="Win32_OperatingSystem")


def _memory_payload(total: int, available: int, *, source: str) -> dict[str, Any]:
    available = max(0, min(available, total))
    used = max(0, total - available)
    used_percent = round((used / total) * 100, 1) if total else 0.0
    return {
        "status": "ok",
        "total_bytes": total,
        "used_bytes": used,
        "available_bytes": available,
        "used_percent": used_percent,
        "source": source,
    }


def _attach_linux_process_ranking(executor: Any, payload: dict[str, Any], limit: int) -> None:
    result = process_query_tool(executor, mode="memory", limit=limit)
    if result.success and isinstance(result.data, dict):
        payload["top_processes"] = list(result.data.get("processes") or [])
        payload["process_source"] = "ps"
        payload["process_error"] = ""
        return
    if _should_try_windows_process_ranking(result.error):
        _attach_windows_process_ranking(executor, payload, limit)
        if payload.get("process_error") == "":
            return
    payload["top_processes"] = []
    payload["process_source"] = "ps"
    payload["process_error"] = result.error or "process ranking unavailable"


def _attach_windows_process_ranking(executor: Any, payload: dict[str, Any], limit: int) -> None:
    result = executor.run(_windows_process_argv(limit), timeout=10)
    payload["process_source"] = "Get-Process"
    if not result.success:
        payload["top_processes"] = []
        payload["process_error"] = result.stderr.strip() or "process ranking unavailable"
        return
    payload["top_processes"] = _parse_windows_process_json(result.stdout)
    payload["process_error"] = ""


def _parse_windows_process_json(stdout: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(stdout or "[]")
    except json.JSONDecodeError:
        return []
    items = payload if isinstance(payload, list) else [payload]
    processes: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        processes.append(
            {
                "pid": _int_or_none(item.get("pid")),
                "user": item.get("user"),
                "cpu_percent": None,
                "memory_percent": None,
                "memory_bytes": _int_or_none(item.get("memory_bytes")),
                "command": str(item.get("command") or ""),
                "args": str(item.get("args") or ""),
            }
        )
    return processes


def _should_try_windows_process_ranking(error: str | None) -> bool:
    text = str(error or "").lower()
    return (
        "unknown option" in text
        or "not recognized" in text
        or "parameter cannot" in text
        or "invalid option" in text
    )


def _number_from_payload(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bounded_limit(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = DEFAULT_LIMIT
    return min(max(number, 1), MAX_LIMIT)


def _windows_memory_argv() -> list[str]:
    return [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "$os = Get-CimInstance Win32_OperatingSystem; "
            "[pscustomobject]@{"
            "TotalVisibleMemorySize=$os.TotalVisibleMemorySize;"
            "FreePhysicalMemory=$os.FreePhysicalMemory"
            "} | ConvertTo-Json -Compress"
        ),
    ]


def _windows_process_argv(limit: int) -> list[str]:
    script = (
        "$items = Get-Process | Sort-Object WorkingSet64 -Descending | "
        f"Select-Object -First {limit} Id,ProcessName,WorkingSet64; "
        "$items | ForEach-Object { [pscustomobject]@{"
        "pid=$_.Id;"
        "user=$null;"
        "memory_bytes=$_.WorkingSet64;"
        "command=$_.ProcessName;"
        "args=''"
        "} } | ConvertTo-Json -Compress"
    )
    return ["powershell", "-NoProfile", "-Command", script]


def _error(
    message: str,
    *,
    linux_result: CommandResult,
    windows_result: CommandResult,
) -> ToolResult:
    details = {
        "status": "error",
        "total_bytes": None,
        "used_bytes": None,
        "available_bytes": None,
        "used_percent": None,
        "source": "",
        "top_processes": [],
        "process_source": "",
        "process_error": "",
        "linux_error": linux_result.stderr.strip(),
        "windows_error": windows_result.stderr.strip(),
    }
    return ToolResult(tool_name=TOOL_NAME, success=False, data=details, error=message)
