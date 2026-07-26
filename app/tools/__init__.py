from __future__ import annotations

from typing import Any

from app.models import CommandResult


def safe_run(executor: Any, argv: list[str], *, timeout: int) -> CommandResult:
    """Run a vetted argv through an executor without letting failures escape."""

    try:
        result = executor.run(argv, timeout=timeout)
    except Exception as exc:
        return CommandResult(
            argv=[str(arg) for arg in argv],
            stderr=f"executor failed: {exc}",
            success=False,
        )

    if isinstance(result, CommandResult):
        return result

    return CommandResult(
        argv=[str(arg) for arg in argv],
        stderr=f"executor returned unsupported result type: {type(result).__name__}",
        success=False,
    )


__all__ = ["safe_run"]
