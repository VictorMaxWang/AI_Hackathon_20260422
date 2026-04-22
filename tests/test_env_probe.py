from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import CommandResult, EnvironmentSnapshot
from app.tools.env_probe import env_probe_tool


def _result(
    argv: list[str],
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
) -> CommandResult:
    return CommandResult(
        argv=argv,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        success=(exit_code == 0 and not timed_out),
    )


class MockExecutor:
    def __init__(self, results: dict[tuple[str, ...], CommandResult]) -> None:
        self.results = results
        self.calls: list[list[str]] = []

    def run(self, argv: list[str], timeout: int = 10) -> CommandResult:
        self.calls.append(argv)
        key = tuple(argv)
        if key in self.results:
            return self.results[key]
        return _result(argv, exit_code=127, stderr="command not found")


class MockSSHExecutor(MockExecutor):
    pass


def _base_results() -> dict[tuple[str, ...], CommandResult]:
    results = {
        ("hostname",): _result(["hostname"], stdout="demo-host\n"),
        (
            "cat",
            "/etc/os-release",
        ): _result(
            ["cat", "/etc/os-release"],
            stdout='NAME="Ubuntu"\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu 24.04 LTS"\n',
        ),
        ("uname", "-r"): _result(["uname", "-r"], stdout="6.8.0\n"),
        ("id", "-un"): _result(["id", "-un"], stdout="demo\n"),
        ("id", "-u"): _result(["id", "-u"], stdout="1000\n"),
        ("sudo", "-n", "true"): _result(["sudo", "-n", "true"]),
    }

    for command, argv in {
        "df": ["df", "--version"],
        "find": ["find", "--version"],
        "ps": ["ps", "--version"],
        "ss": ["ss", "-V"],
        "lsof": ["lsof", "-v"],
        "getent": ["getent", "--help"],
        "useradd": ["useradd", "--help"],
        "userdel": ["userdel", "--help"],
        "sudo": ["sudo", "-V"],
    }.items():
        results[tuple(argv)] = _result(argv, stdout=f"{command} available\n")

    return results


def test_env_probe_tool_returns_basic_environment_snapshot() -> None:
    executor = MockExecutor(_base_results())

    snapshot = env_probe_tool(executor)

    assert isinstance(snapshot, EnvironmentSnapshot)
    assert snapshot.hostname == "demo-host"
    assert snapshot.distro == "Ubuntu 24.04 LTS"
    assert snapshot.kernel == "6.8.0"
    assert snapshot.current_user == "demo"
    assert snapshot.is_root is False
    assert snapshot.sudo_available is True
    assert snapshot.connection_mode == "local"
    assert "df" in snapshot.available_commands
    assert ["hostname"] in executor.calls


def test_env_probe_tool_does_not_crash_when_commands_are_missing() -> None:
    results = _base_results()
    results[("find", "--version")] = _result(
        ["find", "--version"],
        exit_code=-1,
        stderr="command not found: find",
    )
    results[("ps", "--version")] = _result(
        ["ps", "--version"],
        exit_code=127,
        stderr="ps: not found",
    )

    snapshot = env_probe_tool(MockExecutor(results))

    assert "df" in snapshot.available_commands
    assert "find" not in snapshot.available_commands
    assert "ps" not in snapshot.available_commands


def test_env_probe_tool_includes_sudo_available_field_when_sudo_fails() -> None:
    results = _base_results()
    results[("sudo", "-n", "true")] = _result(
        ["sudo", "-n", "true"],
        exit_code=1,
        stderr="a password is required",
    )

    snapshot = env_probe_tool(MockExecutor(results))

    assert isinstance(snapshot.sudo_available, bool)
    assert snapshot.sudo_available is False


def test_env_probe_tool_includes_connection_mode_field_for_ssh_executor() -> None:
    snapshot = env_probe_tool(MockSSHExecutor(_base_results()))

    assert snapshot.connection_mode == "ssh"


def test_env_probe_tool_output_is_json_serializable() -> None:
    snapshot = env_probe_tool(MockExecutor(_base_results()))

    payload = json.loads(snapshot.model_dump_json())

    assert payload["hostname"] == "demo-host"
    assert payload["sudo_available"] is True
    assert payload["connection_mode"] == "local"
    assert "available_commands" in payload
