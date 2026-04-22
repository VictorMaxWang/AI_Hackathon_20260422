from __future__ import annotations

from typing import Any

from app.models import CommandResult, ToolResult


TOOL_NAME = "file_search_tool"
DEFAULT_MAX_RESULTS = 20
MAX_RESULTS_LIMIT = 50
DEFAULT_MAX_DEPTH = 4
MAX_DEPTH_LIMIT = 8
BLOCKED_SEARCH_ROOTS = ("/proc", "/sys", "/dev")


def file_search_tool(
    executor: Any,
    base_path: str,
    name_contains: str | None = None,
    modified_within_days: int | None = None,
    max_results: int = DEFAULT_MAX_RESULTS,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> ToolResult:
    """Search files under a bounded path using a fixed find argv."""

    validation_error = _validate_base_path(base_path)
    if validation_error is not None:
        return _refused(validation_error, base_path)

    try:
        effective_max_results = _bounded_int(
            max_results,
            default=DEFAULT_MAX_RESULTS,
            minimum=1,
            maximum=MAX_RESULTS_LIMIT,
        )
        effective_max_depth = _bounded_int(
            max_depth,
            default=DEFAULT_MAX_DEPTH,
            minimum=1,
            maximum=MAX_DEPTH_LIMIT,
        )
        effective_modified_days = _optional_positive_int(modified_within_days)
    except ValueError as exc:
        return _refused(str(exc), base_path)

    argv = [
        "find",
        base_path,
        "-maxdepth",
        str(effective_max_depth),
        "-type",
        "f",
    ]
    if name_contains:
        argv.extend(["-iname", f"*{name_contains}*"])
    if effective_modified_days is not None:
        argv.extend(["-mtime", f"-{effective_modified_days}"])
    argv.extend(["-printf", "%p\t%f\t%s\t%T@\n"])

    result = executor.run(argv, timeout=15)
    if not result.success:
        return _command_error(result, base_path, effective_max_results, effective_max_depth)

    parsed = _parse_find_output(result.stdout)
    truncated_by_limit = len(parsed) > effective_max_results
    truncated_by_executor = "[truncated" in result.stdout
    limited_results = parsed[:effective_max_results]

    return ToolResult(
        tool_name=TOOL_NAME,
        success=True,
        data={
            "status": "ok",
            "base_path": base_path,
            "name_contains": name_contains,
            "modified_within_days": effective_modified_days,
            "max_results": effective_max_results,
            "max_depth": effective_max_depth,
            "results": limited_results,
            "count": len(limited_results),
            "truncated": truncated_by_limit or truncated_by_executor,
        },
    )


def _validate_base_path(base_path: str) -> str | None:
    if not isinstance(base_path, str) or not base_path.strip():
        return "base_path is required"

    normalized = base_path.strip()
    if normalized != "/":
        normalized = normalized.rstrip("/")

    if normalized == "/":
        return "full filesystem search from / is refused; provide a narrower base_path"

    for blocked_root in BLOCKED_SEARCH_ROOTS:
        if normalized == blocked_root or normalized.startswith(f"{blocked_root}/"):
            return f"deep search under {blocked_root} is refused"

    return None


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        value = default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("numeric limits must be integers") from exc

    return min(max(number, minimum), maximum)


def _optional_positive_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("modified_within_days must be a positive integer") from exc
    if number < 1:
        raise ValueError("modified_within_days must be a positive integer")
    return number


def _parse_find_output(stdout: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.strip() or line.startswith("...[truncated"):
            continue

        parts = line.split("\t")
        path = parts[0]
        name = parts[1] if len(parts) > 1 and parts[1] else path.rsplit("/", 1)[-1]
        size_bytes = _parse_int(parts[2]) if len(parts) > 2 else None
        modified_epoch = _parse_float(parts[3]) if len(parts) > 3 else None

        results.append(
            {
                "path": path,
                "name": name,
                "size_bytes": size_bytes,
                "modified_epoch": modified_epoch,
            }
        )
    return results


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


def _refused(reason: str, base_path: str | None = None) -> ToolResult:
    return ToolResult(
        tool_name=TOOL_NAME,
        success=False,
        data={
            "status": "refused",
            "base_path": base_path,
            "results": [],
            "count": 0,
            "truncated": False,
            "reason": reason,
        },
        error=reason,
    )


def _command_error(
    result: CommandResult,
    base_path: str,
    max_results: int,
    max_depth: int,
) -> ToolResult:
    message = result.stderr.strip() or f"command failed with exit code {result.exit_code}"
    return ToolResult(
        tool_name=TOOL_NAME,
        success=False,
        data={
            "status": "error",
            "base_path": base_path,
            "max_results": max_results,
            "max_depth": max_depth,
            "results": [],
            "count": 0,
            "truncated": False,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
        },
        error=message,
    )
