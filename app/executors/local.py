from __future__ import annotations

import subprocess
from time import monotonic

from app.executors.base import BaseExecutor
from app.models import CommandResult


class LocalExecutor(BaseExecutor):
    """Controlled local executor that only accepts argv vectors."""

    def run(self, argv: list[str], timeout: int = 10) -> CommandResult:
        started_at = monotonic()
        try:
            safe_argv = self._validate_argv(argv)
            safe_timeout = self._validate_timeout(timeout)
        except ValueError as exc:
            return self._result(
                argv=self._safe_argv(argv),
                stderr=str(exc),
                duration_ms=self._duration_ms(started_at),
            )

        try:
            completed = subprocess.run(
                safe_argv,
                capture_output=True,
                text=True,
                timeout=safe_timeout,
                check=False,
            )
            return self._result(
                argv=safe_argv,
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                duration_ms=self._duration_ms(started_at),
            )
        except subprocess.TimeoutExpired as exc:
            return self._result(
                argv=safe_argv,
                stdout=exc.stdout,
                stderr=_timeout_stderr(exc.stderr, safe_timeout),
                duration_ms=self._duration_ms(started_at),
                timed_out=True,
            )
        except FileNotFoundError as exc:
            return self._result(
                argv=safe_argv,
                stderr=f"command not found: {safe_argv[0]} ({exc})",
                duration_ms=self._duration_ms(started_at),
            )
        except OSError as exc:
            return self._result(
                argv=safe_argv,
                stderr=f"failed to execute command: {exc}",
                duration_ms=self._duration_ms(started_at),
            )


def _timeout_stderr(partial_stderr: str | bytes | None, timeout: int) -> str:
    message = f"command timed out after {timeout} seconds"
    if isinstance(partial_stderr, bytes):
        text = partial_stderr.decode(errors="replace").strip()
    else:
        text = str(partial_stderr or "").strip()
    if not text:
        return message
    return f"{message}\n{text}"
