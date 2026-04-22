from __future__ import annotations

from typing import Any

from app.models import CommandResult, ToolResult


TOOL_NAME = "disk_usage_tool"


def disk_usage_tool(executor: Any) -> ToolResult:
    """Collect disk usage with a fixed df argv and return parsed rows."""

    result = executor.run(["df", "-hT"], timeout=10)
    if not result.success:
        return _command_error(result)

    filesystems = _parse_df_output(result.stdout)
    return ToolResult(
        tool_name=TOOL_NAME,
        success=True,
        data={
            "status": "ok",
            "filesystems": filesystems,
            "count": len(filesystems),
        },
    )


def _parse_df_output(stdout: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in stdout.splitlines()[1:]:
        if not line.strip():
            continue

        parts = line.split(maxsplit=6)
        if len(parts) < 7:
            continue

        rows.append(
            {
                "filesystem": parts[0],
                "type": parts[1],
                "size": parts[2],
                "used": parts[3],
                "available": parts[4],
                "use_percent": parts[5],
                "mounted_on": parts[6],
            }
        )
    return rows


def _command_error(result: CommandResult) -> ToolResult:
    message = result.stderr.strip() or f"command failed with exit code {result.exit_code}"
    return ToolResult(
        tool_name=TOOL_NAME,
        success=False,
        data={
            "status": "error",
            "filesystems": [],
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
        },
        error=message,
    )
