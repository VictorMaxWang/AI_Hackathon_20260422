from __future__ import annotations

import shlex
import socket
from dataclasses import dataclass
from time import monotonic

import paramiko

from app.executors.base import BaseExecutor
from app.models import CommandResult


@dataclass(frozen=True)
class SSHConnectionConfig:
    host: str
    username: str
    port: int = 22
    password: str | None = None
    key_filename: str | None = None
    connect_timeout: int = 10
    allow_agent: bool = True
    look_for_keys: bool = True
    auto_add_host_key: bool = False


class SSHExecutor(BaseExecutor):
    """Paramiko-backed executor that preserves argv-only public semantics."""

    def __init__(
        self,
        config: SSHConnectionConfig,
        max_output_chars: int = 20_000,
    ) -> None:
        super().__init__(max_output_chars=max_output_chars)
        self.config = config

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

        client = paramiko.SSHClient()
        if self.config.auto_add_host_key:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        else:
            client.set_missing_host_key_policy(paramiko.RejectPolicy())

        try:
            client.connect(
                hostname=self.config.host,
                port=self.config.port,
                username=self.config.username,
                password=self.config.password,
                key_filename=self.config.key_filename,
                timeout=self.config.connect_timeout,
                allow_agent=self.config.allow_agent,
                look_for_keys=self.config.look_for_keys,
            )
            command = shlex.join(safe_argv)
            _stdin, stdout_stream, stderr_stream = client.exec_command(
                command,
                timeout=safe_timeout,
            )
            stdout = stdout_stream.read()
            stderr = stderr_stream.read()
            exit_code = stdout_stream.channel.recv_exit_status()

            return self._result(
                argv=safe_argv,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=self._duration_ms(started_at),
            )
        except socket.timeout as exc:
            return self._result(
                argv=safe_argv,
                stderr=f"ssh command timed out after {safe_timeout} seconds: {exc}",
                duration_ms=self._duration_ms(started_at),
                timed_out=True,
            )
        except paramiko.AuthenticationException as exc:
            return self._result(
                argv=safe_argv,
                stderr=f"ssh authentication failed: {exc}",
                duration_ms=self._duration_ms(started_at),
            )
        except paramiko.SSHException as exc:
            return self._result(
                argv=safe_argv,
                stderr=f"ssh execution failed: {exc}",
                duration_ms=self._duration_ms(started_at),
            )
        except OSError as exc:
            return self._result(
                argv=safe_argv,
                stderr=f"ssh connection failed: {exc}",
                duration_ms=self._duration_ms(started_at),
            )
        finally:
            client.close()
