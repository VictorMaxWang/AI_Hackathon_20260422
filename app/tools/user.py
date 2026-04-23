from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models import CommandResult, ToolResult
from app.policy.validators import validate_username_with_reasons


CREATE_USER_TOOL_NAME = "create_user_tool"
DELETE_USER_TOOL_NAME = "delete_user_tool"

CREATE_USER_WRAPPER = "scripts/guardedops_create_user.sh"
DELETE_USER_WRAPPER = "scripts/guardedops_delete_user.sh"

LOOKUP_TIMEOUT = 5
CURRENT_USER_TIMEOUT = 5
USER_CHANGE_TIMEOUT = 20
MISSING_USER_EXIT_CODES = {1, 2}
MIN_NORMAL_UID = 1000


@dataclass(frozen=True)
class UserRecord:
    username: str
    uid: int
    gid: int | None
    home: str | None
    shell: str | None
    raw: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "uid": self.uid,
            "gid": self.gid,
            "home": self.home,
            "shell": self.shell,
        }


@dataclass(frozen=True)
class UserLookup:
    result: CommandResult
    record: UserRecord | None = None
    error: str | None = None

    @property
    def exists(self) -> bool:
        return self.record is not None


def create_user_tool(
    executor: Any,
    username: str,
    create_home: bool = True,
    no_sudo: bool = True,
) -> ToolResult:
    """Create a normal non-privileged user through a fixed wrapper."""

    validation_error = _validate_tool_inputs(
        username,
        bool_fields={"create_home": create_home, "no_sudo": no_sudo},
        tool_name=CREATE_USER_TOOL_NAME,
    )
    if validation_error is not None:
        return validation_error

    if no_sudo is not True:
        return _refused(
            CREATE_USER_TOOL_NAME,
            "creating sudo, wheel, admin, or privileged users is forbidden",
            username,
            {"create_home": create_home, "no_sudo": no_sudo},
        )

    lookup = _lookup_user(executor, username)
    if lookup.error is not None:
        return _error(
            CREATE_USER_TOOL_NAME,
            lookup.error,
            username,
            lookup.result,
            {"create_home": create_home, "no_sudo": no_sudo},
        )
    if lookup.exists:
        return _refused(
            CREATE_USER_TOOL_NAME,
            f"user {username} already exists",
            username,
            {
                "create_home": create_home,
                "no_sudo": no_sudo,
                "existing_user": lookup.record.as_dict() if lookup.record else None,
            },
        )

    home_flag = "--create-home" if create_home else "--no-create-home"
    create_result = _run(
        executor,
        ["bash", CREATE_USER_WRAPPER, home_flag, username],
        timeout=USER_CHANGE_TIMEOUT,
    )
    if not create_result.success:
        return _command_error(
            CREATE_USER_TOOL_NAME,
            "user creation command failed",
            username,
            create_result,
            {"create_home": create_home, "no_sudo": no_sudo},
        )

    verification = _lookup_user(executor, username)
    if verification.error is not None:
        return _error(
            CREATE_USER_TOOL_NAME,
            f"user creation could not be verified: {verification.error}",
            username,
            verification.result,
            {"create_home": create_home, "no_sudo": no_sudo},
        )
    if not verification.exists:
        return _error(
            CREATE_USER_TOOL_NAME,
            "user creation could not be verified with getent passwd",
            username,
            verification.result,
            {"create_home": create_home, "no_sudo": no_sudo},
        )

    return ToolResult(
        tool_name=CREATE_USER_TOOL_NAME,
        success=True,
        data={
            "status": "created",
            "username": username,
            "create_home": create_home,
            "no_sudo": True,
            "verified": True,
            "user": verification.record.as_dict() if verification.record else None,
            "command": _command_summary(create_result),
        },
    )


def delete_user_tool(
    executor: Any,
    username: str,
    remove_home: bool = False,
) -> ToolResult:
    """Delete a normal non-privileged user through a fixed wrapper."""

    validation_error = _validate_tool_inputs(
        username,
        bool_fields={"remove_home": remove_home},
        tool_name=DELETE_USER_TOOL_NAME,
    )
    if validation_error is not None:
        return validation_error

    lookup = _lookup_user(executor, username)
    if lookup.error is not None:
        return _error(
            DELETE_USER_TOOL_NAME,
            lookup.error,
            username,
            lookup.result,
            {"remove_home": remove_home},
        )
    if not lookup.exists or lookup.record is None:
        return _refused(
            DELETE_USER_TOOL_NAME,
            f"user {username} does not exist",
            username,
            {"remove_home": remove_home},
        )

    if lookup.record.uid < MIN_NORMAL_UID:
        return _refused(
            DELETE_USER_TOOL_NAME,
            f"user {username} has UID {lookup.record.uid}; deleting system users is forbidden",
            username,
            {
                "remove_home": remove_home,
                "user": lookup.record.as_dict(),
            },
        )

    current_user_result = _run(executor, ["id", "-un"], timeout=CURRENT_USER_TIMEOUT)
    if not current_user_result.success:
        return _command_error(
            DELETE_USER_TOOL_NAME,
            "could not determine current login user",
            username,
            current_user_result,
            {"remove_home": remove_home},
        )

    current_user = _first_nonempty_line(current_user_result.stdout)
    if current_user == username:
        return _refused(
            DELETE_USER_TOOL_NAME,
            f"deleting the current login user {username} is forbidden",
            username,
            {
                "remove_home": remove_home,
                "current_user": current_user,
                "user": lookup.record.as_dict(),
            },
        )

    home_flag = "--remove-home" if remove_home else "--keep-home"
    delete_result = _run(
        executor,
        ["bash", DELETE_USER_WRAPPER, home_flag, username],
        timeout=USER_CHANGE_TIMEOUT,
    )
    if not delete_result.success:
        return _command_error(
            DELETE_USER_TOOL_NAME,
            "user deletion command failed",
            username,
            delete_result,
            {"remove_home": remove_home, "current_user": current_user},
        )

    verification = _lookup_user(executor, username)
    if verification.error is not None:
        return _error(
            DELETE_USER_TOOL_NAME,
            f"user deletion could not be verified: {verification.error}",
            username,
            verification.result,
            {"remove_home": remove_home, "current_user": current_user},
        )
    if verification.exists:
        return _error(
            DELETE_USER_TOOL_NAME,
            "user deletion could not be verified; getent passwd still returns the user",
            username,
            verification.result,
            {
                "remove_home": remove_home,
                "current_user": current_user,
                "user": verification.record.as_dict() if verification.record else None,
            },
        )

    return ToolResult(
        tool_name=DELETE_USER_TOOL_NAME,
        success=True,
        data={
            "status": "deleted",
            "username": username,
            "remove_home": remove_home,
            "current_user": current_user,
            "deleted_user": lookup.record.as_dict(),
            "verified_absent": True,
            "command": _command_summary(delete_result),
        },
    )


def _validate_tool_inputs(
    username: Any,
    *,
    bool_fields: dict[str, Any],
    tool_name: str,
) -> ToolResult | None:
    validation = validate_username_with_reasons(username)
    if not validation.valid:
        return _refused(
            tool_name,
            "; ".join(validation.reasons),
            username,
            {"validation_reasons": list(validation.reasons)},
        )

    for field_name, field_value in bool_fields.items():
        if not isinstance(field_value, bool):
            return _refused(
                tool_name,
                f"{field_name} must be a boolean",
                username,
                {field_name: field_value},
            )

    return None


def _lookup_user(executor: Any, username: str) -> UserLookup:
    result = _run(executor, ["getent", "passwd", username], timeout=LOOKUP_TIMEOUT)
    if result.success:
        record, parse_error = _parse_passwd_record(username, result.stdout)
        if parse_error is not None:
            return UserLookup(result=result, error=parse_error)
        if record is None:
            return UserLookup(
                result=result,
                error=f"getent passwd succeeded but did not return {username}",
            )
        return UserLookup(result=result, record=record)

    if (
        result.exit_code in MISSING_USER_EXIT_CODES
        and not result.stdout.strip()
        and not result.stderr.strip()
        and not result.timed_out
    ):
        return UserLookup(result=result)

    message = result.stderr.strip() or f"getent passwd failed with exit code {result.exit_code}"
    return UserLookup(result=result, error=message)


def _parse_passwd_record(username: str, stdout: str) -> tuple[UserRecord | None, str | None]:
    for line in stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split(":")
        if len(parts) < 7 or parts[0] != username:
            continue
        uid = _parse_int(parts[2])
        if uid is None:
            return None, f"getent passwd returned invalid UID for {username}"
        return (
            UserRecord(
                username=parts[0],
                uid=uid,
                gid=_parse_int(parts[3]),
                home=parts[5] or None,
                shell=parts[6] or None,
                raw=line,
            ),
            None,
        )
    return None, None


def _parse_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _run(executor: Any, argv: list[str], *, timeout: int) -> CommandResult:
    try:
        result = executor.run(argv, timeout=timeout)
    except Exception as exc:
        return CommandResult(
            argv=argv,
            stderr=f"executor failed: {exc}",
            success=False,
        )

    if isinstance(result, CommandResult):
        return result

    return CommandResult(
        argv=argv,
        stderr=f"executor returned unsupported result type: {type(result).__name__}",
        success=False,
    )


def _first_nonempty_line(stdout: str) -> str:
    for line in stdout.splitlines():
        value = line.strip()
        if value:
            return value
    return ""


def _refused(
    tool_name: str,
    reason: str,
    username: Any,
    extra: dict[str, Any] | None = None,
) -> ToolResult:
    data: dict[str, Any] = {
        "status": "refused",
        "username": username,
        "reason": reason,
    }
    if extra:
        data.update(extra)
    return ToolResult(
        tool_name=tool_name,
        success=False,
        data=data,
        error=reason,
    )


def _error(
    tool_name: str,
    reason: str,
    username: str,
    result: CommandResult,
    extra: dict[str, Any] | None = None,
) -> ToolResult:
    data: dict[str, Any] = {
        "status": "error",
        "username": username,
        "reason": reason,
        "command": _command_summary(result),
    }
    if extra:
        data.update(extra)
    return ToolResult(
        tool_name=tool_name,
        success=False,
        data=data,
        error=reason,
    )


def _command_error(
    tool_name: str,
    reason: str,
    username: str,
    result: CommandResult,
    extra: dict[str, Any] | None = None,
) -> ToolResult:
    message = result.stderr.strip() or reason
    return _error(tool_name, f"{reason}: {message}", username, result, extra)


def _command_summary(result: CommandResult) -> dict[str, Any]:
    return {
        "argv": result.argv,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "success": result.success,
    }
