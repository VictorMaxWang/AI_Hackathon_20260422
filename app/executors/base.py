from __future__ import annotations

from abc import ABC, abstractmethod
from time import monotonic
from typing import Any

from app.models import CommandResult


MAX_OUTPUT_CHARS = 20_000


class BaseExecutor(ABC):
    """Small shared contract for controlled command executors."""

    def __init__(self, max_output_chars: int = MAX_OUTPUT_CHARS) -> None:
        if max_output_chars < 1:
            raise ValueError("max_output_chars must be positive")
        self.max_output_chars = max_output_chars

    @abstractmethod
    def run(self, argv: list[str], timeout: int = 10) -> CommandResult:
        """Run a vetted argv vector and return a normalized result."""

    def _validate_argv(self, argv: list[str]) -> list[str]:
        if not isinstance(argv, list):
            raise ValueError("argv must be a list of strings")
        if not argv:
            raise ValueError("argv must not be empty")
        if not isinstance(argv[0], str) or not argv[0]:
            raise ValueError("argv[0] must be a non-empty command")
        if any(not isinstance(arg, str) for arg in argv):
            raise ValueError("argv must contain only strings")
        return list(argv)

    def _safe_argv(self, argv: Any) -> list[str]:
        if isinstance(argv, list) and all(isinstance(arg, str) for arg in argv):
            return list(argv)
        return []

    def _validate_timeout(self, timeout: int) -> int:
        if not isinstance(timeout, int) or timeout < 1:
            raise ValueError("timeout must be a positive integer")
        return timeout

    def _duration_ms(self, started_at: float) -> int:
        return max(0, int((monotonic() - started_at) * 1000))

    def _truncate(self, value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            text = value.decode(errors="replace")
        else:
            text = str(value)
        if len(text) <= self.max_output_chars:
            return text

        omitted = len(text) - self.max_output_chars
        return f"{text[: self.max_output_chars]}\n...[truncated {omitted} chars]"

    def _result(
        self,
        *,
        argv: list[str],
        exit_code: int = -1,
        stdout: str | bytes | None = "",
        stderr: str | bytes | None = "",
        duration_ms: int = 0,
        timed_out: bool = False,
    ) -> CommandResult:
        return CommandResult(
            argv=argv,
            exit_code=exit_code,
            stdout=self._truncate(stdout),
            stderr=self._truncate(stderr),
            duration_ms=max(0, duration_ms),
            timed_out=timed_out,
            success=(exit_code == 0 and not timed_out),
        )
