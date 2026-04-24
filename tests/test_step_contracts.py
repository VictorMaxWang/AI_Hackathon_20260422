from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.memory import AgentMemory
from app.agent.orchestrator import ReadonlyOrchestrator
from app.models import CommandResult, EnvironmentSnapshot, ToolResult


CREATE_REQUEST = "先查询环境，如果权限足够，创建普通用户 demo_temp"
CREATE_CONFIRMATION = "确认创建普通用户 demo_temp"


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


class StepContractExecutor:
    def __init__(self) -> None:
        self.hostname = "demo-host"
        self.connection_mode = "ssh"
        self.current_user = "operator"
        self.is_root = False
        self.sudo_available = True
        self.kernel = "6.8.0"
        self.distro = "Ubuntu 24.04"
        self.users: dict[str, dict[str, Any]] = {}

    def run(self, argv: list[str], timeout: int = 10) -> CommandResult:
        del timeout

        if argv == ["hostname"]:
            return _command_result(argv, stdout=f"{self.hostname}\n")
        if argv == ["uname", "-r"]:
            return _command_result(argv, stdout=f"{self.kernel}\n")
        if argv == ["id", "-un"]:
            return _command_result(argv, stdout=f"{self.current_user}\n")
        if argv == ["id", "-u"]:
            return _command_result(argv, stdout="0\n" if self.is_root else "1000\n")
        if argv == ["cat", "/etc/os-release"]:
            return _command_result(argv, stdout=f'PRETTY_NAME="{self.distro}"\n')
        if argv == ["sudo", "-n", "true"]:
            return _command_result(argv, exit_code=0 if self.sudo_available else 1)

        if len(argv) == 3 and argv[:2] == ["getent", "passwd"]:
            username = argv[2]
            user = self.users.get(username)
            if user is None:
                return _command_result(argv, exit_code=1)
            return _command_result(
                argv,
                stdout=(
                    f"{username}:x:{user['uid']}:{user['gid']}:{username}:"
                    f"{user['home']}:{user['shell']}\n"
                ),
            )

        command = argv[0] if argv else ""
        if command in {"df", "find", "ps", "ss", "lsof", "getent", "useradd", "userdel", "sudo"}:
            return _command_result(argv, stdout="ok\n")

        return _command_result(argv, exit_code=127, stderr="unexpected command")


class ContractHarness:
    def __init__(self) -> None:
        self.env_probe_calls = 0
        self.raise_env_probe_on_call: int | None = None
        self.create_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []

    def env_probe(self, executor: StepContractExecutor) -> EnvironmentSnapshot:
        self.env_probe_calls += 1
        if self.raise_env_probe_on_call == self.env_probe_calls:
            raise RuntimeError("ssh connection dropped")
        return EnvironmentSnapshot(
            hostname=executor.hostname,
            distro=executor.distro,
            kernel=executor.kernel,
            current_user=executor.current_user,
            is_root=executor.is_root,
            sudo_available=executor.sudo_available,
            available_commands=["getent", "useradd", "userdel", "ss", "ps", "sudo"],
            connection_mode=executor.connection_mode,
        )

    def create_user(self, executor: StepContractExecutor, **kwargs: Any) -> ToolResult:
        self.create_calls.append(dict(kwargs))
        username = kwargs["username"]
        executor.users[username] = {
            "uid": 1001,
            "gid": 1001,
            "home": f"/home/{username}",
            "shell": "/bin/bash",
        }
        return ToolResult(
            tool_name="create_user_tool",
            success=True,
            data={"status": "created", "verified": True, **kwargs},
        )

    def delete_user(self, executor: StepContractExecutor, **kwargs: Any) -> ToolResult:
        self.delete_calls.append(dict(kwargs))
        executor.users.pop(kwargs["username"], None)
        return ToolResult(
            tool_name="delete_user_tool",
            success=True,
            data={"status": "deleted", "verified_absent": True, **kwargs},
        )


def make_orchestrator(
    executor: StepContractExecutor | None = None,
    harness: ContractHarness | None = None,
    memory: AgentMemory | None = None,
) -> tuple[ReadonlyOrchestrator, StepContractExecutor, ContractHarness, AgentMemory]:
    resolved_executor = executor or StepContractExecutor()
    resolved_harness = harness or ContractHarness()
    resolved_memory = memory or AgentMemory()
    orchestrator = ReadonlyOrchestrator(
        resolved_executor,
        memory=resolved_memory,
        env_probe=resolved_harness.env_probe,
        create_user_tool_fn=resolved_harness.create_user,
        delete_user_tool_fn=resolved_harness.delete_user,
    )
    return orchestrator, resolved_executor, resolved_harness, resolved_memory


def test_write_step_resume_revalidates_before_create_and_uses_checkpoint() -> None:
    orchestrator, executor, harness, memory = make_orchestrator()

    pending = orchestrator.run(CREATE_REQUEST)

    assert pending["result"]["status"] == "pending_confirmation"
    assert [item["intent"] for item in pending["timeline"]] == [
        "env_probe",
        "checkpoint_saved",
        "create_user",
    ]
    checkpoint = memory.get_pending_checkpoint()
    assert checkpoint is not None
    assert checkpoint["step_id"] == "step_2"
    assert checkpoint["write_step"] is True
    assert "env.current_user" in checkpoint["facts"]
    assert checkpoint["facts"]["target.user_exists"] is False
    assert harness.env_probe_calls == 1
    assert harness.create_calls == []

    resumed = orchestrator.run(CREATE_CONFIRMATION)

    assert resumed["result"]["status"] == "success"
    assert [item["intent"] for item in resumed["timeline"]] == [
        "env_probe",
        "checkpoint_saved",
        "contract_revalidated",
        "create_user",
        "verify_user_exists",
    ]
    assert harness.env_probe_calls == 2
    assert harness.create_calls == [
        {"username": "demo_temp", "create_home": True, "no_sudo": True}
    ]
    assert executor.users["demo_temp"]["uid"] == 1001
    assert memory.pending_action is None


def test_host_drift_invalidates_resume_and_emits_drift_timeline_event() -> None:
    orchestrator, executor, harness, memory = make_orchestrator()

    orchestrator.run(CREATE_REQUEST)
    assert memory.get_pending_checkpoint() is not None

    executor.hostname = "drifted-host"
    resumed = orchestrator.run(CREATE_CONFIRMATION)

    assert resumed["result"]["status"] == "refused"
    assert resumed["result"]["error"] == "confirmation_token_host_mismatch"
    assert "contract_drift" in [item["intent"] for item in resumed["timeline"]]
    assert harness.create_calls == []
    assert memory.pending_action is None


def test_current_user_and_sudo_drift_refuse_resume_before_write() -> None:
    orchestrator, executor, harness, memory = make_orchestrator()

    orchestrator.run(CREATE_REQUEST)
    assert memory.get_pending_checkpoint() is not None

    executor.current_user = "other-operator"
    executor.sudo_available = False
    resumed = orchestrator.run(CREATE_CONFIRMATION)

    assert resumed["result"]["status"] == "refused"
    assert resumed["result"]["error"] is not None
    assert resumed["timeline"][-1]["intent"] == "contract_drift"
    assert "env.current_user" in resumed["timeline"][-1]["result_summary"]
    assert harness.env_probe_calls == 2
    assert harness.create_calls == []
    assert memory.pending_action is None


def test_target_drift_invalidates_old_plan_when_user_appears_during_wait() -> None:
    orchestrator, executor, harness, memory = make_orchestrator()

    orchestrator.run(CREATE_REQUEST)
    assert memory.get_pending_checkpoint() is not None

    executor.users["demo_temp"] = {
        "uid": 2001,
        "gid": 2001,
        "home": "/home/demo_temp",
        "shell": "/bin/bash",
    }
    resumed = orchestrator.run(CREATE_CONFIRMATION)

    assert resumed["result"]["status"] == "refused"
    assert resumed["timeline"][-1]["intent"] == "contract_drift"
    assert "target.user_exists" in resumed["timeline"][-1]["result_summary"]
    assert harness.create_calls == []
    assert memory.pending_action is None


def test_resume_fails_closed_when_revalidation_env_probe_is_unavailable() -> None:
    orchestrator, _executor, harness, memory = make_orchestrator()

    orchestrator.run(CREATE_REQUEST)
    assert memory.get_pending_checkpoint() is not None

    harness.raise_env_probe_on_call = 2
    resumed = orchestrator.run(CREATE_CONFIRMATION)

    assert resumed["result"]["status"] == "refused"
    assert resumed["timeline"][-1]["intent"] == "contract_drift"
    assert "unable to revalidate environment" in resumed["timeline"][-1]["result_summary"]
    assert harness.create_calls == []
    assert memory.pending_action is None
