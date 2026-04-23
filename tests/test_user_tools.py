from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import CommandResult
from app.tools.user import (
    CREATE_USER_WRAPPER,
    DELETE_USER_WRAPPER,
    create_user_tool,
    delete_user_tool,
)


class MockExecutor:
    def __init__(self, responses: list[CommandResult]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[list[str], int]] = []

    def run(self, argv: list[str], timeout: int = 10) -> CommandResult:
        self.calls.append((argv, timeout))
        if not self.responses:
            raise AssertionError(f"unexpected executor call: {argv}")
        return self.responses.pop(0)


def command_result(
    argv: list[str],
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
) -> CommandResult:
    return CommandResult(
        argv=argv,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        success=exit_code == 0,
    )


def passwd_line(username: str, uid: int = 1001) -> str:
    return f"{username}:x:{uid}:{uid}::/home/{username}:/bin/bash\n"


def missing_user(username: str) -> CommandResult:
    return command_result(["getent", "passwd", username], exit_code=2)


def existing_user(username: str, uid: int = 1001) -> CommandResult:
    return command_result(["getent", "passwd", username], stdout=passwd_line(username, uid))


def test_create_user_success_flow() -> None:
    username = "demo_user"
    executor = MockExecutor(
        [
            missing_user(username),
            command_result(["bash", CREATE_USER_WRAPPER, "--create-home", username]),
            existing_user(username),
        ]
    )

    result = create_user_tool(executor, username)

    assert result.success is True
    assert result.tool_name == "create_user_tool"
    assert result.data["status"] == "created"
    assert result.data["username"] == username
    assert result.data["create_home"] is True
    assert result.data["no_sudo"] is True
    assert result.data["verified"] is True
    assert executor.calls == [
        (["getent", "passwd", username], 5),
        (["bash", CREATE_USER_WRAPPER, "--create-home", username], 20),
        (["getent", "passwd", username], 5),
    ]


def test_create_user_fails_when_user_already_exists() -> None:
    username = "demo_user"
    executor = MockExecutor([existing_user(username)])

    result = create_user_tool(executor, username)

    assert result.success is False
    assert result.data["status"] == "refused"
    assert "already exists" in result.error
    assert executor.calls == [(["getent", "passwd", username], 5)]


def test_delete_nonexistent_user_fails() -> None:
    username = "demo_user"
    executor = MockExecutor([missing_user(username)])

    result = delete_user_tool(executor, username)

    assert result.success is False
    assert result.data["status"] == "refused"
    assert "does not exist" in result.error
    assert executor.calls == [(["getent", "passwd", username], 5)]


def test_delete_system_user_rejected() -> None:
    username = "sys_user"
    executor = MockExecutor([existing_user(username, uid=999)])

    result = delete_user_tool(executor, username)

    assert result.success is False
    assert result.data["status"] == "refused"
    assert "UID 999" in result.error
    assert executor.calls == [(["getent", "passwd", username], 5)]


def test_delete_current_user_is_refused() -> None:
    username = "demo_user"
    executor = MockExecutor(
        [
            existing_user(username),
            command_result(["id", "-un"], stdout=f"{username}\n"),
        ]
    )

    result = delete_user_tool(executor, username)

    assert result.success is False
    assert result.data["status"] == "refused"
    assert "current login user" in result.error
    assert executor.calls == [
        (["getent", "passwd", username], 5),
        (["id", "-un"], 5),
    ]


def test_delete_defaults_to_keep_home() -> None:
    username = "demo_user"
    executor = MockExecutor(
        [
            existing_user(username),
            command_result(["id", "-un"], stdout="operator\n"),
            command_result(["bash", DELETE_USER_WRAPPER, "--keep-home", username]),
            missing_user(username),
        ]
    )

    result = delete_user_tool(executor, username)

    assert result.success is True
    assert result.data["status"] == "deleted"
    assert result.data["remove_home"] is False
    assert result.data["verified_absent"] is True
    assert (["bash", DELETE_USER_WRAPPER, "--keep-home", username], 20) in executor.calls
    assert (["bash", DELETE_USER_WRAPPER, "--remove-home", username], 20) not in executor.calls


def test_create_sudo_user_rejected() -> None:
    executor = MockExecutor([])

    result = create_user_tool(executor, "demo_user", no_sudo=False)

    assert result.success is False
    assert result.data["status"] == "refused"
    assert "privileged users" in result.error
    assert executor.calls == []


def test_create_user_rejects_reserved_usernames_without_executor_call() -> None:
    executor = MockExecutor([])

    result = create_user_tool(executor, "root")

    assert result.success is False
    assert result.data["status"] == "refused"
    assert "reserved" in result.error
    assert executor.calls == []
