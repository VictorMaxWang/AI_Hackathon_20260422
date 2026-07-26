from __future__ import annotations

import re
from typing import Any

from app.models import CommandResult, ToolResult
from app.policy.rules import is_same_or_child_path, normalize_path
from app.tools import safe_run


TOOL_NAME = "file_search_tool"
DEFAULT_MAX_RESULTS = 20
MAX_RESULTS_LIMIT = 50
DEFAULT_MAX_DEPTH = 4
MAX_DEPTH_LIMIT = 8
FIND_TIMEOUT = 15
MAX_WARNING_LINES = 20
BLOCKED_SEARCH_ROOTS = ("/proc", "/sys", "/dev")
FIND_PARTIAL_EXIT_CODE = 1
UNSUPPORTED_FILE_SEARCH_MESSAGE = (
    "当前环境的 find 不支持本工具所需的固定参数，因此无法完成文件检索。"
    "建议在 Linux/SSH 目标环境中执行，或改用受支持的只读查询。"
)
UNSUPPORTED_FIND_MARKERS = (
    "unknown predicate",
    "unknown option",
    "invalid predicate",
    "illegal option",
    "not recognized",
    "command not found",
    "unrecognized option",
)

_MULTI_SLASH_RE = re.compile(r"/{2,}")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def file_search_tool(
    executor: Any,
    base_path: str,
    name_contains: str | None = None,
    modified_within_days: int | None = None,
    max_results: int = DEFAULT_MAX_RESULTS,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> ToolResult:
    """Search files under a bounded path using a fixed find argv."""

    safe_base_path, validation_error = _validate_base_path(base_path)
    if validation_error is not None or safe_base_path is None:
        return _refused(validation_error or "base_path is invalid", base_path)

    safe_name_contains, name_error = _validate_name_contains(name_contains)
    if name_error is not None:
        return _refused(name_error, safe_base_path)

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
        return _refused(str(exc), safe_base_path)

    argv = [
        "find",
        safe_base_path,
        "-maxdepth",
        str(effective_max_depth),
        "-type",
        "f",
    ]
    if safe_name_contains:
        argv.extend(["-iname", f"*{safe_name_contains}*"])
    if effective_modified_days is not None:
        argv.extend(["-mtime", f"-{effective_modified_days}"])
    argv.extend(["-printf", "%p\t%f\t%s\t%T@\n"])

    result = safe_run(executor, argv, timeout=FIND_TIMEOUT)
    parsed = _parse_find_output(result.stdout)

    if not result.success:
        if _looks_like_unsupported_find(result):
            return _unsupported_environment(
                safe_base_path,
                effective_max_results,
                effective_max_depth,
                result,
            )
        if (
            result.timed_out
            or result.exit_code != FIND_PARTIAL_EXIT_CODE
            or not parsed
        ):
            return _command_error(
                result,
                safe_base_path,
                effective_max_results,
                effective_max_depth,
            )
        return _search_payload(
            base_path=safe_base_path,
            name_contains=safe_name_contains,
            modified_within_days=effective_modified_days,
            max_results=effective_max_results,
            max_depth=effective_max_depth,
            parsed=parsed,
            stdout=result.stdout,
            partial=True,
            warnings=_warning_lines(result.stderr),
        )

    return _search_payload(
        base_path=safe_base_path,
        name_contains=safe_name_contains,
        modified_within_days=effective_modified_days,
        max_results=effective_max_results,
        max_depth=effective_max_depth,
        parsed=parsed,
        stdout=result.stdout,
        partial=False,
        warnings=[],
    )


def _search_payload(
    *,
    base_path: str,
    name_contains: str | None,
    modified_within_days: int | None,
    max_results: int,
    max_depth: int,
    parsed: list[dict[str, Any]],
    stdout: str,
    partial: bool,
    warnings: list[str],
) -> ToolResult:
    truncated_by_limit = len(parsed) > max_results
    truncated_by_executor = "[truncated" in stdout
    limited_results = parsed[:max_results]

    return ToolResult(
        tool_name=TOOL_NAME,
        success=True,
        data={
            "status": "ok",
            "base_path": base_path,
            "name_contains": name_contains,
            "modified_within_days": modified_within_days,
            "max_results": max_results,
            "max_depth": max_depth,
            "results": limited_results,
            "count": len(limited_results),
            "truncated": truncated_by_limit or truncated_by_executor,
            "partial": partial,
            "warnings": warnings,
        },
    )


def _validate_base_path(base_path: Any) -> tuple[str | None, str | None]:
    if not isinstance(base_path, str) or not base_path.strip():
        return None, "base_path is required"

    stripped = base_path.strip()
    if stripped.startswith("-"):
        return None, "base_path must not start with '-'; option-like paths are refused"
    if _CONTROL_CHARS_RE.search(stripped):
        return None, "base_path must not contain control characters"

    normalized = _normalize_base_path(stripped)
    if normalized is None or not normalized.startswith("/"):
        return None, "base_path must be an absolute path starting with /"
    if normalized.startswith("-"):
        return None, "base_path must not start with '-'; option-like paths are refused"

    if normalized == "/":
        return None, "full filesystem search from / is refused; provide a narrower base_path"

    blocked_root = _blocked_search_root(normalized)
    if blocked_root is not None:
        return None, f"deep search under {blocked_root} is refused"

    return normalized, None


def _normalize_base_path(base_path: str) -> str | None:
    collapsed = _MULTI_SLASH_RE.sub("/", base_path)
    try:
        normalized = normalize_path(collapsed)
    except Exception:
        return None
    if not isinstance(normalized, str) or not normalized:
        return None
    return _MULTI_SLASH_RE.sub("/", normalized)


def _blocked_search_root(normalized: str) -> str | None:
    for blocked_root in BLOCKED_SEARCH_ROOTS:
        try:
            blocked = bool(is_same_or_child_path(normalized, blocked_root))
        except Exception:
            blocked = normalized == blocked_root or normalized.startswith(f"{blocked_root}/")
        if blocked:
            return blocked_root
    return None


def _validate_name_contains(name_contains: Any) -> tuple[str | None, str | None]:
    if name_contains is None:
        return None, None
    if not isinstance(name_contains, str):
        return None, "name_contains must be a string"
    if not name_contains:
        return None, None
    if _CONTROL_CHARS_RE.search(name_contains):
        return None, "name_contains must not contain control characters"
    return name_contains, None


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


def _warning_lines(stderr: str) -> list[str]:
    lines = [line.strip() for line in str(stderr or "").splitlines() if line.strip()]
    return lines[:MAX_WARNING_LINES]


def _looks_like_unsupported_find(result: CommandResult) -> bool:
    if result.timed_out:
        return False
    text = f"{result.stderr}\n{result.stdout}".lower()
    return any(marker in text for marker in UNSUPPORTED_FIND_MARKERS)


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


def _refused(reason: str, base_path: Any = None) -> ToolResult:
    return ToolResult(
        tool_name=TOOL_NAME,
        success=False,
        data={
            "status": "refused",
            "base_path": base_path,
            "results": [],
            "count": 0,
            "truncated": False,
            "partial": False,
            "warnings": [],
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
            "partial": False,
            "warnings": _warning_lines(result.stderr),
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
        },
        error=message,
    )


def _unsupported_environment(
    base_path: str,
    max_results: int,
    max_depth: int,
    result: CommandResult,
) -> ToolResult:
    return ToolResult(
        tool_name=TOOL_NAME,
        success=False,
        data={
            "status": "unsupported_on_current_environment",
            "base_path": base_path,
            "max_results": max_results,
            "max_depth": max_depth,
            "results": [],
            "count": 0,
            "truncated": False,
            "partial": False,
            "warnings": _warning_lines(result.stderr),
            "attempted_sources": ["find"],
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
        },
        error=UNSUPPORTED_FILE_SEARCH_MESSAGE,
    )
