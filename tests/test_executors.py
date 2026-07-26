from __future__ import annotations

import socket
import sys
from unittest.mock import Mock, patch

import paramiko

from app.executors import LocalExecutor, SSHConnectionConfig, SSHExecutor
from app.models import CommandResult


class FakeChannel:
    """Minimal paramiko channel stand-in for the interleaved read loop."""

    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        exit_code: int = 0,
        never_exits: bool = False,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.never_exits = never_exits
        self.blocking = True

    def setblocking(self, blocking: bool) -> None:
        self.blocking = bool(blocking)

    def recv_ready(self) -> bool:
        return bool(self.stdout)

    def recv(self, size: int) -> bytes:
        chunk, self.stdout = self.stdout[:size], self.stdout[size:]
        return chunk

    def recv_stderr_ready(self) -> bool:
        return bool(self.stderr)

    def recv_stderr(self, size: int) -> bytes:
        chunk, self.stderr = self.stderr[:size], self.stderr[size:]
        return chunk

    def exit_status_ready(self) -> bool:
        return not self.never_exits

    def recv_exit_status(self) -> int:
        return self.exit_code


def fake_ssh_client(
    stdout: bytes = b"",
    stderr: bytes = b"",
    exit_code: int = 0,
    never_exits: bool = False,
) -> Mock:
    channel = FakeChannel(
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        never_exits=never_exits,
    )
    stdout_stream = Mock()
    stdout_stream.channel = channel
    client = Mock()
    client.exec_command.return_value = (Mock(), stdout_stream, Mock())
    return client


def test_local_executor_runs_whoami() -> None:
    result = LocalExecutor().run(["whoami"], timeout=5)

    assert isinstance(result, CommandResult)
    assert result.argv == ["whoami"]
    assert result.success is True
    assert result.exit_code == 0
    assert result.stdout.strip()
    assert result.timed_out is False


def test_local_executor_runs_hostname() -> None:
    result = LocalExecutor().run(["hostname"], timeout=5)

    assert result.success is True
    assert result.exit_code == 0
    assert result.stdout.strip()


def test_local_executor_handles_missing_command() -> None:
    result = LocalExecutor().run(["guardedops-command-that-does-not-exist"], timeout=5)

    assert result.success is False
    assert result.exit_code == -1
    assert "command not found" in result.stderr


def test_local_executor_returns_non_zero_exit_code() -> None:
    result = LocalExecutor().run(
        [sys.executable, "-c", "import sys; sys.exit(7)"],
        timeout=5,
    )

    assert result.success is False
    assert result.exit_code == 7
    assert result.timed_out is False


def test_local_executor_handles_timeout() -> None:
    result = LocalExecutor().run(
        [sys.executable, "-c", "import time; time.sleep(2)"],
        timeout=1,
    )

    assert result.success is False
    assert result.exit_code == -1
    assert result.timed_out is True
    assert "timed out" in result.stderr


def test_local_executor_truncates_stdout_and_stderr() -> None:
    executor = LocalExecutor(max_output_chars=12)

    stdout_result = executor.run(
        [sys.executable, "-c", "print('x' * 50)"],
        timeout=5,
    )
    stderr_result = executor.run(
        [sys.executable, "-c", "import sys; print('y' * 50, file=sys.stderr)"],
        timeout=5,
    )

    assert stdout_result.stdout.startswith("xxxxxxxxxxxx")
    assert "[truncated" in stdout_result.stdout
    assert stderr_result.stderr.startswith("yyyyyyyyyyyy")
    assert "[truncated" in stderr_result.stderr


def test_executor_rejects_empty_or_non_string_argv() -> None:
    executor = LocalExecutor()

    empty_result = executor.run([], timeout=5)
    invalid_result = executor.run(["whoami", 123], timeout=5)  # type: ignore[list-item]

    assert empty_result.success is False
    assert "argv must not be empty" in empty_result.stderr
    assert invalid_result.success is False
    assert "argv must contain only strings" in invalid_result.stderr


def test_ssh_executor_uses_paramiko_and_returns_command_result() -> None:
    config = SSHConnectionConfig(
        host="example.test",
        username="demo",
        password="secret",
        connect_timeout=3,
        allow_agent=False,
        look_for_keys=False,
        auto_add_host_key=True,
    )
    fake_client = fake_ssh_client(stdout=b"ok\n")

    with patch("app.executors.ssh.paramiko.SSHClient", return_value=fake_client):
        executor = SSHExecutor(config)
        result = executor.run(["uname", "-a"], timeout=4)

    fake_client.set_missing_host_key_policy.assert_called_once()
    fake_client.connect.assert_called_once_with(
        hostname="example.test",
        port=22,
        username="demo",
        password="secret",
        key_filename=None,
        timeout=3,
        allow_agent=False,
        look_for_keys=False,
    )
    fake_client.exec_command.assert_called_once_with("uname -a", timeout=4)
    fake_client.close.assert_not_called()
    executor.close()
    fake_client.close.assert_called_once()
    assert result.success is True
    assert result.exit_code == 0
    assert result.stdout == "ok\n"
    assert result.argv == ["uname", "-a"]


def test_ssh_executor_quotes_argv_without_raw_shell_api() -> None:
    config = SSHConnectionConfig(host="example.test", username="demo")
    fake_client = fake_ssh_client()

    with patch("app.executors.ssh.paramiko.SSHClient", return_value=fake_client):
        SSHExecutor(config).run(["printf", "%s", "hello world"], timeout=5)

    fake_client.exec_command.assert_called_once_with(
        "printf %s 'hello world'",
        timeout=5,
    )
    assert not hasattr(SSHExecutor, "run_shell")
    assert not hasattr(SSHExecutor, "run_command")


def test_ssh_executor_returns_structured_error_on_auth_failure() -> None:
    config = SSHConnectionConfig(host="example.test", username="demo")
    fake_client = Mock()
    fake_client.connect.side_effect = paramiko.AuthenticationException("denied")

    with patch("app.executors.ssh.paramiko.SSHClient", return_value=fake_client):
        result = SSHExecutor(config).run(["hostname"], timeout=5)

    assert result.success is False
    assert result.exit_code == -1
    assert "ssh authentication failed" in result.stderr
    fake_client.close.assert_called_once()


def test_ssh_executor_returns_structured_timeout() -> None:
    config = SSHConnectionConfig(host="example.test", username="demo")
    fake_client = Mock()
    fake_client.connect.side_effect = socket.timeout("slow")

    with patch("app.executors.ssh.paramiko.SSHClient", return_value=fake_client):
        result = SSHExecutor(config).run(["hostname"], timeout=5)

    assert result.success is False
    assert result.timed_out is True
    assert "ssh command timed out" in result.stderr
    fake_client.close.assert_called_once()


def test_ssh_executor_default_config_rejects_unknown_host_keys() -> None:
    config = SSHConnectionConfig(host="example.test", username="demo")
    fake_client = fake_ssh_client(stdout=b"demo-host\n")

    with patch("app.executors.ssh.paramiko.SSHClient", return_value=fake_client):
        executor = SSHExecutor(config)
        result = executor.run(["hostname"], timeout=5)
        executor.close()

    assert config.auto_add_host_key is False
    fake_client.load_system_host_keys.assert_called_once_with()
    fake_client.load_host_keys.assert_not_called()
    policy = fake_client.set_missing_host_key_policy.call_args.args[0]
    assert isinstance(policy, paramiko.RejectPolicy)
    assert result.success is True


def test_ssh_executor_loads_configured_known_hosts_file() -> None:
    config = SSHConnectionConfig(
        host="example.test",
        username="demo",
        known_hosts_path="/etc/guardedops/known_hosts",
    )
    fake_client = fake_ssh_client()

    with patch("app.executors.ssh.paramiko.SSHClient", return_value=fake_client):
        executor = SSHExecutor(config)
        executor.run(["hostname"], timeout=5)
        executor.close()

    fake_client.load_system_host_keys.assert_called_once_with()
    fake_client.load_host_keys.assert_called_once_with("/etc/guardedops/known_hosts")


def test_ssh_executor_auto_add_policy_only_when_explicitly_enabled() -> None:
    config = SSHConnectionConfig(host="example.test", username="demo", auto_add_host_key=True)
    fake_client = fake_ssh_client()

    with patch("app.executors.ssh.paramiko.SSHClient", return_value=fake_client):
        executor = SSHExecutor(config)
        executor.run(["hostname"], timeout=5)
        executor.close()

    policy = fake_client.set_missing_host_key_policy.call_args.args[0]
    assert isinstance(policy, paramiko.AutoAddPolicy)


def test_ssh_config_repr_does_not_leak_password() -> None:
    config = SSHConnectionConfig(host="example.test", username="demo", password="hunter2")

    text = f"{config!r} {config}"

    assert "hunter2" not in text
    assert "redacted" in text


def test_ssh_executor_reuses_one_connection_for_multiple_commands() -> None:
    config = SSHConnectionConfig(host="example.test", username="demo")
    fake_client = fake_ssh_client()

    with patch("app.executors.ssh.paramiko.SSHClient", return_value=fake_client) as factory:
        with SSHExecutor(config) as executor:
            executor.run(["hostname"], timeout=5)
            fake_client.exec_command.return_value[1].channel = FakeChannel(stdout=b"ok\n")
            executor.run(["uname", "-r"], timeout=5)

    assert factory.call_count == 1
    fake_client.connect.assert_called_once()
    assert fake_client.exec_command.call_count == 2
    fake_client.close.assert_called_once()


def test_ssh_executor_reconnects_when_transport_is_inactive() -> None:
    config = SSHConnectionConfig(host="example.test", username="demo")
    first_client = fake_ssh_client()
    first_client.get_transport.return_value.is_active.return_value = False
    second_client = fake_ssh_client(stdout=b"ok\n")

    with patch(
        "app.executors.ssh.paramiko.SSHClient",
        side_effect=[first_client, second_client],
    ):
        executor = SSHExecutor(config)
        executor.run(["hostname"], timeout=5)
        result = executor.run(["uname", "-r"], timeout=5)
        executor.close()

    first_client.connect.assert_called_once()
    second_client.connect.assert_called_once()
    assert result.stdout == "ok\n"


def test_ssh_executor_enforces_wall_clock_budget_on_chatty_command() -> None:
    config = SSHConnectionConfig(host="example.test", username="demo")
    fake_client = fake_ssh_client(stdout=b"noise\n" * 10, never_exits=True)

    with patch("app.executors.ssh.paramiko.SSHClient", return_value=fake_client):
        executor = SSHExecutor(config)
        result = executor.run(["tail", "-f", "/var/log/syslog"], timeout=1)

    assert result.timed_out is True
    assert result.success is False
    assert "ssh command timed out after 1 seconds" in result.stderr
    assert "noise" in result.stdout
    fake_client.close.assert_called_once()


def test_ssh_executor_drains_stdout_and_stderr_together() -> None:
    config = SSHConnectionConfig(host="example.test", username="demo")
    fake_client = fake_ssh_client(stdout=b"out\n", stderr=b"err\n", exit_code=3)

    with patch("app.executors.ssh.paramiko.SSHClient", return_value=fake_client):
        executor = SSHExecutor(config)
        result = executor.run(["find", "/var/log"], timeout=5)
        executor.close()

    assert result.stdout == "out\n"
    assert result.stderr == "err\n"
    assert result.exit_code == 3
    assert result.success is False


def test_ssh_executor_stops_a_never_ending_stream_without_unbounded_buffering() -> None:
    class InfiniteChannel(FakeChannel):
        def recv_ready(self) -> bool:
            return True

        def recv(self, size: int) -> bytes:
            return b"x" * size

        def exit_status_ready(self) -> bool:
            return False

    stdout_stream = Mock()
    stdout_stream.channel = InfiniteChannel()
    fake_client = Mock()
    fake_client.exec_command.return_value = (Mock(), stdout_stream, Mock())
    config = SSHConnectionConfig(host="example.test", username="demo")

    with patch("app.executors.ssh.paramiko.SSHClient", return_value=fake_client):
        executor = SSHExecutor(config, max_output_chars=100)
        result = executor.run(["yes"], timeout=1)

    assert result.timed_out is True
    assert len(result.stdout) < 200
    assert "[truncated" in result.stdout
    fake_client.close.assert_called_once()


def test_ssh_executor_truncates_large_remote_output() -> None:
    config = SSHConnectionConfig(host="example.test", username="demo")
    fake_client = fake_ssh_client(stdout=b"x" * 5_000)

    with patch("app.executors.ssh.paramiko.SSHClient", return_value=fake_client):
        executor = SSHExecutor(config, max_output_chars=100)
        result = executor.run(["cat", "/var/log/syslog"], timeout=5)
        executor.close()

    assert result.stdout.startswith("x" * 100)
    assert "[truncated" in result.stdout


def test_local_executor_reports_timeout_even_with_partial_stderr() -> None:
    script = (
        "import sys, time; "
        "print('partial failure detail', file=sys.stderr); "
        "sys.stderr.flush(); "
        "time.sleep(5)"
    )
    result = LocalExecutor().run([sys.executable, "-c", script], timeout=1)

    assert result.timed_out is True
    assert result.success is False
    assert "timed out after 1 seconds" in result.stderr
