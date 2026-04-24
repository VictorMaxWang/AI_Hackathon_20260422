from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.memory import AgentMemory
from app.agent.orchestrator import ReadonlyOrchestrator
from app.models import EnvironmentSnapshot, ToolResult


class DummyExecutor:
    pass


class ContinuousToolMocks:
    def __init__(
        self,
        *,
        sudo_available: bool = True,
        is_root: bool = False,
        env_error: Exception | None = None,
        port_listeners: list[dict[str, Any]] | None = None,
    ) -> None:
        self.sudo_available = sudo_available
        self.is_root = is_root
        self.env_error = env_error
        self.port_listeners = port_listeners
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def env_probe(self, executor: Any) -> EnvironmentSnapshot:
        self.calls.append(("env_probe_tool", {}))
        if self.env_error is not None:
            raise self.env_error
        return EnvironmentSnapshot(
            hostname="demo-host",
            distro="Ubuntu 24.04",
            kernel="6.8.0",
            current_user="operator",
            is_root=self.is_root,
            sudo_available=self.sudo_available,
            available_commands=["getent", "useradd", "userdel", "ss", "ps"],
            connection_mode="local",
        )

    def disk_usage(self, executor: Any) -> ToolResult:
        self.calls.append(("disk_usage_tool", {}))
        return ToolResult(tool_name="disk_usage_tool", success=True, data={})

    def file_search(self, executor: Any, **kwargs: Any) -> ToolResult:
        self.calls.append(("file_search_tool", kwargs))
        return ToolResult(tool_name="file_search_tool", success=True, data=kwargs)

    def process_query(self, executor: Any, **kwargs: Any) -> ToolResult:
        self.calls.append(("process_query_tool", kwargs))
        return ToolResult(
            tool_name="process_query_tool",
            success=True,
            data={
                "status": "ok",
                **kwargs,
                "processes": [
                    {
                        "pid": kwargs.get("pid"),
                        "user": "www-data",
                        "command": "nginx",
                    }
                ],
                "count": 1,
            },
        )

    def port_query(self, executor: Any, **kwargs: Any) -> ToolResult:
        self.calls.append(("port_query_tool", kwargs))
        listeners = (
            self.port_listeners
            if self.port_listeners is not None
            else [
                {
                    "protocol": "tcp",
                    "state": "LISTEN",
                    "local_address": f"0.0.0.0:{kwargs['port']}",
                    "pid": 456,
                    "process_name": "nginx",
                    "user": "www-data",
                }
            ]
        )
        return ToolResult(
            tool_name="port_query_tool",
            success=True,
            data={
                "status": "listening" if listeners else "not_listening",
                "port": kwargs["port"],
                "listeners": listeners,
                "count": len(listeners),
            },
        )

    def create_user(self, executor: Any, **kwargs: Any) -> ToolResult:
        self.calls.append(("create_user_tool", kwargs))
        return ToolResult(
            tool_name="create_user_tool",
            success=True,
            data={"status": "created", "verified": True, **kwargs},
        )

    def delete_user(self, executor: Any, **kwargs: Any) -> ToolResult:
        self.calls.append(("delete_user_tool", kwargs))
        return ToolResult(
            tool_name="delete_user_tool",
            success=True,
            data={"status": "deleted", "verified_absent": True, **kwargs},
        )


def make_orchestrator(
    mocks: ContinuousToolMocks,
    memory: AgentMemory | None = None,
) -> ReadonlyOrchestrator:
    return ReadonlyOrchestrator(
        DummyExecutor(),
        memory=memory,
        env_probe=mocks.env_probe,
        disk_tool=mocks.disk_usage,
        file_search_tool_fn=mocks.file_search,
        process_query_tool_fn=mocks.process_query,
        port_query_tool_fn=mocks.port_query,
        create_user_tool_fn=mocks.create_user,
        delete_user_tool_fn=mocks.delete_user,
    )


VALID_TIMELINE_INTENTS = {
    "env_probe",
    "checkpoint_saved",
    "contract_revalidated",
    "query_port",
    "query_process",
    "create_user",
    "delete_user",
    "verify_user_exists",
    "verify_user_absent",
}


def assert_timeline_entries_are_readable(timeline: list[dict[str, Any]]) -> None:
    required = {"step_id", "intent", "risk", "status", "result_summary"}
    assert timeline
    for item in timeline:
        assert required <= set(item)
        assert item["intent"] in VALID_TIMELINE_INTENTS
        assert str(item["status"]).strip()
        assert str(item["result_summary"]).strip()


def test_environment_create_user_verify_exists_pause_and_resume() -> None:
    mocks = ContinuousToolMocks(sudo_available=True)
    orchestrator = make_orchestrator(mocks)

    pending = orchestrator.run("先查询环境，如果权限足够，创建普通用户 demo_temp")

    assert pending["result"]["status"] == "pending_confirmation"
    assert pending["result"]["confirmation_text"] == "确认创建普通用户 demo_temp"
    assert [item["intent"] for item in pending["timeline"]] == [
        "env_probe",
        "checkpoint_saved",
        "create_user",
    ]
    assert [item["status"] for item in pending["timeline"]] == [
        "success",
        "success",
        "pending_confirmation",
    ]
    assert_timeline_entries_are_readable(pending["timeline"])
    assert [name for name, _args in mocks.calls] == ["env_probe_tool"]

    resumed = orchestrator.run("确认创建普通用户 demo_temp")

    assert resumed["result"]["status"] == "success"
    assert [name for name, _args in mocks.calls] == [
        "env_probe_tool",
        "env_probe_tool",
        "create_user_tool",
    ]
    assert [item["intent"] for item in resumed["timeline"]] == [
        "env_probe",
        "checkpoint_saved",
        "contract_revalidated",
        "create_user",
        "verify_user_exists",
    ]
    assert_timeline_entries_are_readable(resumed["timeline"])
    assert resumed["timeline"][-1]["status"] == "success"
    assert "已存在" in resumed["timeline"][-1]["result_summary"]


def test_delete_contextual_user_requires_s2_confirmation_and_verifies_absent() -> None:
    mocks = ContinuousToolMocks()
    memory = AgentMemory(last_username="demo_temp")
    orchestrator = make_orchestrator(mocks, memory)

    pending = orchestrator.run("删除刚才那个用户，并说明为什么删除比创建更敏感")

    assert pending["risk"]["risk_level"] == "S2"
    assert pending["result"]["status"] == "pending_confirmation"
    assert pending["result"]["confirmation_text"] == "确认删除普通用户 demo_temp"
    assert "删除比创建更敏感" in pending["explanation"]
    assert "账号访问" in pending["explanation"]
    assert [item["intent"] for item in pending["timeline"]] == [
        "checkpoint_saved",
        "delete_user",
    ]
    assert [item["status"] for item in pending["timeline"]] == [
        "success",
        "pending_confirmation",
    ]
    assert_timeline_entries_are_readable(pending["timeline"])
    assert mocks.calls == []

    resumed = orchestrator.run("确认删除普通用户 demo_temp")

    assert resumed["result"]["status"] == "success"
    assert [name for name, _args in mocks.calls] == ["delete_user_tool"]
    assert [item["intent"] for item in resumed["timeline"]] == [
        "checkpoint_saved",
        "contract_revalidated",
        "delete_user",
        "verify_user_absent",
    ]
    assert_timeline_entries_are_readable(resumed["timeline"])
    assert "已不存在" in resumed["timeline"][-1]["result_summary"]
    assert "删除比创建更敏感" in resumed["explanation"]


def test_port_then_process_query_only_runs_process_when_listener_exists() -> None:
    mocks = ContinuousToolMocks()
    orchestrator = make_orchestrator(mocks)

    result = orchestrator.run("先查 8080 端口，再告诉我对应的进程")

    assert result["result"]["status"] == "success"
    assert mocks.calls == [
        ("port_query_tool", {"port": 8080}),
        ("process_query_tool", {"mode": "pid", "limit": 10, "keyword": None, "pid": 456}),
    ]
    assert [item["intent"] for item in result["timeline"]] == [
        "query_port",
        "query_process",
    ]
    assert_timeline_entries_are_readable(result["timeline"])


def test_confirmation_mismatch_keeps_pending_and_resume_does_not_rerun_prior_steps() -> None:
    mocks = ContinuousToolMocks(sudo_available=True)
    orchestrator = make_orchestrator(mocks)

    orchestrator.run("先查询环境，如果权限足够，创建普通用户 demo_temp")
    mismatch = orchestrator.run("确认创建普通用户 other_user")

    assert mismatch["result"]["status"] == "pending_confirmation"
    assert mismatch["result"]["error"] == "confirmation_text_mismatch"
    assert [item["intent"] for item in mismatch["timeline"]] == [
        "env_probe",
        "checkpoint_saved",
        "create_user",
    ]
    assert [item["status"] for item in mismatch["timeline"]] == [
        "success",
        "success",
        "pending_confirmation",
    ]
    assert_timeline_entries_are_readable(mismatch["timeline"])
    assert [name for name, _args in mocks.calls] == ["env_probe_tool"]

    resumed = orchestrator.run("确认创建普通用户 demo_temp")

    assert resumed["result"]["status"] == "success"
    assert [name for name, _args in mocks.calls] == [
        "env_probe_tool",
        "env_probe_tool",
        "create_user_tool",
    ]
    assert [item["intent"] for item in resumed["timeline"]] == [
        "env_probe",
        "checkpoint_saved",
        "contract_revalidated",
        "create_user",
        "verify_user_exists",
    ]
    assert_timeline_entries_are_readable(resumed["timeline"])


def test_cancel_continuous_pending_step_clears_memory_and_does_not_run_write_tool() -> None:
    mocks = ContinuousToolMocks(sudo_available=True)
    memory = AgentMemory()
    orchestrator = make_orchestrator(mocks, memory)

    pending = orchestrator.run("先查询环境，如果权限足够，创建普通用户 demo_temp")

    assert pending["result"]["status"] == "pending_confirmation"
    assert memory.pending_action is not None
    assert [item["intent"] for item in pending["timeline"]] == [
        "env_probe",
        "checkpoint_saved",
        "create_user",
    ]
    assert_timeline_entries_are_readable(pending["timeline"])
    assert [name for name, _args in mocks.calls] == ["env_probe_tool"]

    cancelled = orchestrator.run("取消")

    assert cancelled["result"]["status"] == "cancelled"
    assert cancelled["execution"]["status"] == "skipped"
    assert memory.pending_action is None
    assert [name for name, _args in mocks.calls] == ["env_probe_tool"]


def test_previous_failure_aborts_dependent_write_step() -> None:
    mocks = ContinuousToolMocks(env_error=RuntimeError("env unavailable"))
    orchestrator = make_orchestrator(mocks)

    result = orchestrator.run("先查询环境，如果权限足够，创建普通用户 demo_temp")

    assert result["result"]["status"] == "failed"
    assert [name for name, _args in mocks.calls] == ["env_probe_tool"]
    assert [item["status"] for item in result["timeline"]] == ["failed", "aborted"]
    assert result["timeline"][1]["intent"] == "create_user"
    assert_timeline_entries_are_readable(result["timeline"])
    assert "写操作不会盲目继续" in result["timeline"][1]["result_summary"]


def test_timeline_entries_have_required_structure() -> None:
    mocks = ContinuousToolMocks()
    result = make_orchestrator(mocks).run("先查 8080 端口，再告诉我对应的进程")

    assert_timeline_entries_are_readable(result["timeline"])
