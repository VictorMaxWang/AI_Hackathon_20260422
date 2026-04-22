from __future__ import annotations

import socket
import sys
from unittest.mock import Mock, patch

import paramiko

from app.executors import LocalExecutor, SSHConnectionConfig, SSHExecutor
from app.models import CommandResult


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
    fake_client = Mock()
    fake_stdout = Mock()
    fake_stderr = Mock()
    fake_stdout.read.return_value = b"ok\n"
    fake_stderr.read.return_value = b""
    fake_stdout.channel.recv_exit_status.return_value = 0
    fake_client.exec_command.return_value = (Mock(), fake_stdout, fake_stderr)

    with patch("app.executors.ssh.paramiko.SSHClient", return_value=fake_client):
        result = SSHExecutor(config).run(["uname", "-a"], timeout=4)

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
    fake_client.close.assert_called_once()
    assert result.success is True
    assert result.exit_code == 0
    assert result.stdout == "ok\n"
    assert result.argv == ["uname", "-a"]


def test_ssh_executor_quotes_argv_without_raw_shell_api() -> None:
    config = SSHConnectionConfig(host="example.test", username="demo")
    fake_client = Mock()
    fake_stdout = Mock()
    fake_stderr = Mock()
    fake_stdout.read.return_value = b""
    fake_stderr.read.return_value = b""
    fake_stdout.channel.recv_exit_status.return_value = 0
    fake_client.exec_command.return_value = (Mock(), fake_stdout, fake_stderr)

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
