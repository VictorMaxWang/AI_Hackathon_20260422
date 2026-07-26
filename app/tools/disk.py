from __future__ import annotations

from typing import Any

from app.models import CommandResult, ToolResult
from app.policy.rules import is_same_or_child_path
from app.tools import safe_run


TOOL_NAME = "disk_usage_tool"
DF_TIMEOUT = 10
TYPED_DF_ARGV = ["df", "-hT"]
PLAIN_DF_ARGV = ["df", "-h"]
UNKNOWN_FILESYSTEM_TYPE = "unknown"
UNSUPPORTED_DISK_USAGE_MESSAGE = (
    "当前本地环境缺少可用的 df 磁盘用量工具，因此无法完成该查询。"
    "建议在 Linux/SSH 目标环境中执行。"
)
UNSUPPORTED_DF_MARKERS = (
    "unknown option",
    "invalid option",
    "illegal option",
    "unrecognized option",
    "not recognized",
    "command not found",
)

PSEUDO_FILESYSTEM_TYPES = frozenset(
    {
        "autofs",
        "binfmt_misc",
        "bpf",
        "cgroup",
        "cgroup2",
        "configfs",
        "debugfs",
        "devfs",
        "devpts",
        "devtmpfs",
        "efivarfs",
        "fusectl",
        "hugetlbfs",
        "mqueue",
        "nsfs",
        "proc",
        "pstore",
        "ramfs",
        "rpc_pipefs",
        "securityfs",
        "selinuxfs",
        "squashfs",
        "sysfs",
        "tmpfs",
        "tracefs",
    }
)
PSEUDO_MOUNT_ROOTS = ("/dev", "/proc", "/run", "/snap", "/sys")


def disk_usage_tool(executor: Any) -> ToolResult:
    """Collect disk usage with a fixed df argv and return parsed rows."""

    typed_result = safe_run(executor, list(TYPED_DF_ARGV), timeout=DF_TIMEOUT)
    if typed_result.success:
        return _success(_parse_df_output(typed_result.stdout, typed=True), source="df -hT")

    if not _looks_like_unsupported_df(typed_result):
        return _command_error(typed_result)

    plain_result = safe_run(executor, list(PLAIN_DF_ARGV), timeout=DF_TIMEOUT)
    if plain_result.success:
        return _success(_parse_df_output(plain_result.stdout, typed=False), source="df -h")

    if _looks_like_unsupported_df(plain_result):
        return _unsupported_environment(typed_result, plain_result)
    return _command_error(plain_result)


def _success(rows: list[dict[str, str]], *, source: str) -> ToolResult:
    filesystems = [row for row in rows if not _is_pseudo_filesystem(row)]
    pseudo_filesystems = [row for row in rows if _is_pseudo_filesystem(row)]
    return ToolResult(
        tool_name=TOOL_NAME,
        success=True,
        data={
            "status": "ok",
            "source": source,
            "filesystems": filesystems,
            "count": len(filesystems),
            "pseudo_filesystems": pseudo_filesystems,
            "pseudo_count": len(pseudo_filesystems),
        },
    )


def _parse_df_output(stdout: str, *, typed: bool) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    expected_columns = 7 if typed else 6
    for line in stdout.splitlines()[1:]:
        if not line.strip():
            continue

        parts = line.split(maxsplit=expected_columns - 1)
        if len(parts) < expected_columns:
            continue

        if typed:
            filesystem_type = parts[1]
            values = parts[2:]
        else:
            filesystem_type = UNKNOWN_FILESYSTEM_TYPE
            values = parts[1:]

        rows.append(
            {
                "filesystem": parts[0],
                "type": filesystem_type,
                "size": values[0],
                "used": values[1],
                "available": values[2],
                "use_percent": values[3],
                "mounted_on": values[4],
            }
        )
    return rows


def _is_pseudo_filesystem(row: dict[str, str]) -> bool:
    filesystem_type = str(row.get("type") or "").strip().lower()
    if filesystem_type in PSEUDO_FILESYSTEM_TYPES or filesystem_type.startswith("fuse."):
        return True
    if filesystem_type not in {"", UNKNOWN_FILESYSTEM_TYPE}:
        return False

    mounted_on = row.get("mounted_on")
    return any(_is_same_or_child(mounted_on, root) for root in PSEUDO_MOUNT_ROOTS)


def _is_same_or_child(path: Any, base: str) -> bool:
    try:
        return bool(is_same_or_child_path(path, base))
    except Exception:
        text = str(path or "")
        return text == base or text.startswith(f"{base}/")


def _looks_like_unsupported_df(result: CommandResult) -> bool:
    if result.timed_out:
        return False
    text = f"{result.stderr}\n{result.stdout}".lower()
    return any(marker in text for marker in UNSUPPORTED_DF_MARKERS)


def _command_error(result: CommandResult) -> ToolResult:
    message = result.stderr.strip() or f"command failed with exit code {result.exit_code}"
    return ToolResult(
        tool_name=TOOL_NAME,
        success=False,
        data={
            "status": "error",
            "filesystems": [],
            "count": 0,
            "pseudo_filesystems": [],
            "pseudo_count": 0,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
        },
        error=message,
    )


def _unsupported_environment(
    typed_result: CommandResult,
    plain_result: CommandResult,
) -> ToolResult:
    return ToolResult(
        tool_name=TOOL_NAME,
        success=False,
        data={
            "status": "unsupported_on_current_environment",
            "source": "none",
            "filesystems": [],
            "count": 0,
            "pseudo_filesystems": [],
            "pseudo_count": 0,
            "missing_tools": ["df"],
            "attempted_sources": ["df -hT", "df -h"],
            "typed_exit_code": typed_result.exit_code,
            "plain_exit_code": plain_result.exit_code,
            "timed_out": typed_result.timed_out or plain_result.timed_out,
        },
        error=UNSUPPORTED_DISK_USAGE_MESSAGE,
    )
