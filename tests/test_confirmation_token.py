from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.confirmation import confirmation_text_for
from app.agent.orchestrator import ReadonlyOrchestrator
from app.models import CommandResult, ToolResult


CREATE_USER_REQUEST = "\u8bf7\u521b\u5efa\u666e\u901a\u7528\u6237 demo_guest"
CREATE_USER_CONFIRMATION = confirmation_text_for(
    "create_user",
    {"username": "demo_guest"},
)
S3_CREATE_ROOT_REQUEST = "\u8bf7\u521b\u5efa\u666e\u901a\u7528\u6237 root"
CONTINUOUS_CREATE_REQUEST = (
    "\u5148\u67e5\u8be2\u73af\u5883\uff0c\u5982\u679c\u6743\u9650\u8db3\u591f\uff0c"
    "\u521b\u5efa\u666e\u901a\u7528\u6237 demo_temp"
)
CONTINUOUS_CREATE_CONFIRMATION = confirmation_text_for(
    "create_user",
    {"username": "demo_temp"},
)


def _command_result(
    argv: list[str],
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> CommandResult:
    return CommandResult(
        argv=argv,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        success=exit_code == 0,
    )


class ProbeableExecutor:
    def __init__(self, *, hostname: str = "demo-host") -> None:
        self.hostname = hostname
        self.calls: list[list[str]] = []

    def run(self, argv: list[str], timeout: int = 10) -> CommandResult:
        del timeout
        self.calls.append(argv)

        if argv == ["hostname"]:
            return _command_result(argv, stdout=f"{self.hostname}\n")
        if argv == ["uname", "-r"]:
            return _command_result(argv, stdout="6.8.0\n")
        if argv == ["id", "-un"]:
            return _command_result(argv, stdout="operator\n")
        if argv == ["id", "-u"]:
            return _command_result(argv, stdout="1000\n")
        if argv == ["cat", "/etc/os-release"]:
            return _command_result(argv, stdout='PRETTY_NAME="Ubuntu 24.04"\n')
        if argv == ["sudo", "-n", "true"]:
            return _command_result(argv, stdout="")

        command = argv[0] if argv else ""
        if command in {"df", "find", "ps", "ss", "lsof", "getent", "useradd", "userdel", "sudo"}:
            return _command_result(argv, stdout="ok\n")

        return _command_result(argv, exit_code=127, stderr="unexpected command")


class UserToolRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def create_user(self, executor: Any, **kwargs: Any) -> ToolResult:
        del executor
        self.calls.append(("create_user_tool", kwargs))
        return ToolResult(
            tool_name="create_user_tool",
            success=True,
            data={"status": "created", **kwargs},
        )

    def delete_user(self, executor: Any, **kwargs: Any) -> ToolResult:
        del executor
        self.calls.append(("delete_user_tool", kwargs))
        return ToolResult(
            tool_name="delete_user_tool",
            success=True,
            data={"status": "deleted", **kwargs},
        )


def make_orchestrator(
    executor: ProbeableExecutor | None = None,
    recorder: UserToolRecorder | None = None,
) -> tuple[ReadonlyOrchestrator, ProbeableExecutor, UserToolRecorder]:
    resolved_executor = executor or ProbeableExecutor()
    resolved_recorder = recorder or UserToolRecorder()
    orchestrator = ReadonlyOrchestrator(
        resolved_executor,
        create_user_tool_fn=resolved_recorder.create_user,
        delete_user_tool_fn=resolved_recorder.delete_user,
    )
    return orchestrator, resolved_executor, resolved_recorder


def _replace_pending_action(orchestrator: ReadonlyOrchestrator, **updates: Any) -> None:
    pending_action = orchestrator.memory.pending_action
    assert pending_action is not None
    orchestrator.memory.set_pending_action(pending_action.model_copy(update=updates))


def test_first_write_request_enters_pending_confirmation_and_generates_token() -> None:
    orchestrator, _executor, recorder = make_orchestrator()

    result = orchestrator.run(CREATE_USER_REQUEST)

    assert result["result"]["status"] == "pending_confirmation"
    assert result["result"]["confirmation_text"] == CREATE_USER_CONFIRMATION
    pending_action = orchestrator.memory.pending_action
    assert pending_action is not None
    assert pending_action.confirmation_token is not None
    token = pending_action.confirmation_token
    assert token.host_id == "demo-host"
    assert token.risk_level == "S1"
    assert token.plan_hash
    assert token.target_fingerprint
    assert token.policy_version
    assert token.expires_at > token.issued_at
    assert result["result"]["pending_action"]["confirmation_token"]["host_id"] == "demo-host"
    assert recorder.calls == []


def test_correct_confirmation_with_plan_hash_mismatch_does_not_execute() -> None:
    orchestrator, _executor, recorder = make_orchestrator()
    orchestrator.run(CREATE_USER_REQUEST)

    pending_action = orchestrator.memory.pending_action
    assert pending_action is not None
    assert pending_action.confirmation_token is not None
    _replace_pending_action(
        orchestrator,
        confirmation_token=pending_action.confirmation_token.model_copy(
            update={"plan_hash": "0" * 64}
        ),
    )

    result = orchestrator.run(CREATE_USER_CONFIRMATION)

    assert result["result"]["status"] == "refused"
    assert result["result"]["error"] == "confirmation_token_plan_mismatch"
    assert orchestrator.memory.pending_action is None
    assert recorder.calls == []


def test_host_change_invalidates_confirmation_token() -> None:
    orchestrator, executor, recorder = make_orchestrator()
    orchestrator.run(CREATE_USER_REQUEST)
    executor.hostname = "drifted-host"

    result = orchestrator.run(CREATE_USER_CONFIRMATION)

    assert result["result"]["status"] == "refused"
    assert result["result"]["error"] == "confirmation_token_host_mismatch"
    assert orchestrator.memory.pending_action is None
    assert recorder.calls == []


def test_target_change_invalidates_confirmation_token() -> None:
    orchestrator, _executor, recorder = make_orchestrator()
    orchestrator.run(CREATE_USER_REQUEST)

    pending_action = orchestrator.memory.pending_action
    assert pending_action is not None
    _replace_pending_action(
        orchestrator,
        target={"username": "other_user"},
    )

    result = orchestrator.run(CREATE_USER_CONFIRMATION)

    assert result["result"]["status"] == "refused"
    assert result["result"]["error"] == "confirmation_token_target_mismatch"
    assert orchestrator.memory.pending_action is None
    assert recorder.calls == []


def test_expired_confirmation_token_does_not_execute() -> None:
    orchestrator, _executor, recorder = make_orchestrator()
    orchestrator.run(CREATE_USER_REQUEST)

    pending_action = orchestrator.memory.pending_action
    assert pending_action is not None
    assert pending_action.confirmation_token is not None
    _replace_pending_action(
        orchestrator,
        confirmation_token=pending_action.confirmation_token.model_copy(
            update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
        ),
    )

    result = orchestrator.run(CREATE_USER_CONFIRMATION)

    assert result["result"]["status"] == "refused"
    assert result["result"]["error"] == "confirmation_token_expired"
    assert orchestrator.memory.pending_action is None
    assert recorder.calls == []


def test_policy_version_change_invalidates_confirmation_token() -> None:
    orchestrator, _executor, recorder = make_orchestrator()
    orchestrator.run(CREATE_USER_REQUEST)

    pending_action = orchestrator.memory.pending_action
    assert pending_action is not None
    assert pending_action.confirmation_token is not None
    _replace_pending_action(
        orchestrator,
        confirmation_token=pending_action.confirmation_token.model_copy(
            update={"policy_version": "stale-policy-version"}
        ),
    )

    result = orchestrator.run(CREATE_USER_CONFIRMATION)

    assert result["result"]["status"] == "refused"
    assert result["result"]["error"] == "confirmation_token_policy_mismatch"
    assert orchestrator.memory.pending_action is None
    assert recorder.calls == []


def test_exact_confirmation_with_matching_token_executes_once() -> None:
    orchestrator, _executor, recorder = make_orchestrator()
    orchestrator.run(CREATE_USER_REQUEST)

    result = orchestrator.run(CREATE_USER_CONFIRMATION)

    assert result["result"]["status"] == "success"
    assert result["result"]["tool_name"] == "create_user_tool"
    assert recorder.calls == [
        (
            "create_user_tool",
            {"username": "demo_guest", "create_home": True, "no_sudo": True},
        )
    ]
    assert orchestrator.memory.pending_action is None


def test_continuous_pending_token_blocks_resume_after_host_drift() -> None:
    orchestrator, executor, recorder = make_orchestrator()

    pending = orchestrator.run(CONTINUOUS_CREATE_REQUEST)

    assert pending["result"]["status"] == "pending_confirmation"
    assert pending["result"]["pending_action"]["confirmation_token"]["host_id"] == "demo-host"

    executor.hostname = "drifted-host"
    resumed = orchestrator.run(CONTINUOUS_CREATE_CONFIRMATION)

    assert resumed["result"]["status"] == "refused"
    assert resumed["result"]["error"] == "confirmation_token_host_mismatch"
    assert orchestrator.memory.pending_action is None
    assert recorder.calls == []


def test_s3_never_enters_confirmation_and_does_not_issue_token() -> None:
    orchestrator, _executor, recorder = make_orchestrator()

    result = orchestrator.run(S3_CREATE_ROOT_REQUEST)

    assert result["risk"]["risk_level"] == "S3"
    assert result["risk"]["requires_confirmation"] is False
    assert result["result"]["status"] == "refused"
    assert result["result"].get("confirmation_text") is None
    assert orchestrator.memory.pending_action is None
    assert recorder.calls == []
