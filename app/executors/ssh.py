from __future__ import annotations

import shlex
import socket
from dataclasses import dataclass, field
from time import monotonic, sleep
from types import TracebackType
from typing import Any

import paramiko

from app.executors.base import BaseExecutor
from app.models import CommandResult


READ_CHUNK_BYTES = 32_768
POLL_INTERVAL_SECONDS = 0.02
CAPTURE_OVERHEAD_BYTES = 4_096
REDACTED_PASSWORD = "***redacted***"


@dataclass(frozen=True)
class SSHConnectionConfig:
    host: str
    username: str
    port: int = 22
    password: str | None = field(default=None, repr=False)
    key_filename: str | None = None
    connect_timeout: int = 10
    allow_agent: bool = True
    look_for_keys: bool = True
    auto_add_host_key: bool = False
    known_hosts_path: str | None = None

    def __repr__(self) -> str:
        password = REDACTED_PASSWORD if self.password else None
        return (
            "SSHConnectionConfig("
            f"host={self.host!r}, username={self.username!r}, port={self.port!r}, "
            f"password={password!r}, key_filename={self.key_filename!r}, "
            f"connect_timeout={self.connect_timeout!r}, allow_agent={self.allow_agent!r}, "
            f"look_for_keys={self.look_for_keys!r}, "
            f"auto_add_host_key={self.auto_add_host_key!r}, "
            f"known_hosts_path={self.known_hosts_path!r})"
        )


class SSHExecutor(BaseExecutor):
    """Paramiko-backed executor that preserves argv-only public semantics."""

    def __init__(
        self,
        config: SSHConnectionConfig,
        max_output_chars: int = 20_000,
    ) -> None:
        super().__init__(max_output_chars=max_output_chars)
        self.config = config
        self._client: paramiko.SSHClient | None = None

    def __enter__(self) -> SSHExecutor:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.close()
        except Exception:
            pass

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
            client = self._connected_client()
            command = shlex.join(safe_argv)
            _stdin, stdout_stream, _stderr_stream = client.exec_command(
                command,
                timeout=safe_timeout,
            )
            channel = getattr(stdout_stream, "channel", None)
            stdout, stderr, timed_out = self._collect_output(
                channel,
                deadline=started_at + safe_timeout,
            )
            if timed_out:
                self.close()
                return self._result(
                    argv=safe_argv,
                    stdout=stdout,
                    stderr=self._timeout_stderr(stderr, safe_timeout),
                    duration_ms=self._duration_ms(started_at),
                    timed_out=True,
                )

            return self._result(
                argv=safe_argv,
                exit_code=channel.recv_exit_status(),
                stdout=stdout,
                stderr=stderr,
                duration_ms=self._duration_ms(started_at),
            )
        except socket.timeout as exc:
            self.close()
            return self._result(
                argv=safe_argv,
                stderr=f"ssh command timed out after {safe_timeout} seconds: {exc}",
                duration_ms=self._duration_ms(started_at),
                timed_out=True,
            )
        except paramiko.AuthenticationException as exc:
            self.close()
            return self._result(
                argv=safe_argv,
                stderr=f"ssh authentication failed: {exc}",
                duration_ms=self._duration_ms(started_at),
            )
        except paramiko.SSHException as exc:
            self.close()
            return self._result(
                argv=safe_argv,
                stderr=f"ssh execution failed: {exc}",
                duration_ms=self._duration_ms(started_at),
            )
        except OSError as exc:
            self.close()
            return self._result(
                argv=safe_argv,
                stderr=f"ssh connection failed: {exc}",
                duration_ms=self._duration_ms(started_at),
            )

    def _connected_client(self) -> paramiko.SSHClient:
        cached = self._client
        if cached is not None and self._is_active(cached):
            return cached

        self.close()
        client = paramiko.SSHClient()
        try:
            client.load_system_host_keys()
            if self.config.known_hosts_path:
                client.load_host_keys(self.config.known_hosts_path)
            if self.config.auto_add_host_key:
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            else:
                client.set_missing_host_key_policy(paramiko.RejectPolicy())
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
        except BaseException:
            try:
                client.close()
            except Exception:
                pass
            raise

        self._client = client
        return client

    def _is_active(self, client: Any) -> bool:
        try:
            transport = client.get_transport()
            return bool(transport is not None and transport.is_active())
        except Exception:
            return False

    def _collect_output(self, channel: Any, *, deadline: float) -> tuple[str, str, bool]:
        if channel is None:
            return "", "", False

        capture_limit = self.max_output_chars * 4 + CAPTURE_OVERHEAD_BYTES
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        stdout_bytes = 0
        stderr_bytes = 0
        try:
            channel.setblocking(False)
        except Exception:
            pass

        while True:
            progressed = False

            while channel.recv_ready() and monotonic() < deadline:
                chunk = channel.recv(READ_CHUNK_BYTES)
                if not chunk:
                    break
                progressed = True
                if stdout_bytes < capture_limit:
                    stdout_chunks.append(chunk)
                    stdout_bytes += len(chunk)

            while channel.recv_stderr_ready() and monotonic() < deadline:
                chunk = channel.recv_stderr(READ_CHUNK_BYTES)
                if not chunk:
                    break
                progressed = True
                if stderr_bytes < capture_limit:
                    stderr_chunks.append(chunk)
                    stderr_bytes += len(chunk)

            if monotonic() >= deadline:
                return self._decode(stdout_chunks), self._decode(stderr_chunks), True

            if (
                channel.exit_status_ready()
                and not channel.recv_ready()
                and not channel.recv_stderr_ready()
            ):
                break

            if not progressed:
                sleep(POLL_INTERVAL_SECONDS)

        return self._decode(stdout_chunks), self._decode(stderr_chunks), False

    def _decode(self, chunks: list[bytes]) -> str:
        return b"".join(chunks).decode(errors="replace")

    def _timeout_stderr(self, stderr: str, timeout: int) -> str:
        message = f"ssh command timed out after {timeout} seconds"
        partial = stderr.strip()
        if not partial:
            return message
        return f"{message}\n{partial}"
