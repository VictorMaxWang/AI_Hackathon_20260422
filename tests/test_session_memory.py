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


class ToolMocks:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def env_probe(self, executor: Any) -> EnvironmentSnapshot:
        self.calls.append(("env_probe_tool", {}))
        return EnvironmentSnapshot(
            hostname="demo-host",
            distro="Ubuntu 24.04",
            kernel="6.8.0",
            current_user="demo",
            is_root=False,
            sudo_available=False,
            available_commands=["df", "find", "ps", "ss"],
            connection_mode="local",
        )

    def disk_usage(self, executor: Any) -> ToolResult:
        self.calls.append(("disk_usage_tool", {}))
        return ToolResult(tool_name="disk_usage_tool", success=True, data={"filesystems": []})

    def file_search(self, executor: Any, **kwargs: Any) -> ToolResult:
        self.calls.append(("file_search_tool", kwargs))
        return ToolResult(
            tool_name="file_search_tool",
            success=True,
            data={
                "status": "ok",
                **kwargs,
                "results": [],
                "count": 0,
                "truncated": False,
            },
        )

    def process_query(self, executor: Any, **kwargs: Any) -> ToolResult:
        self.calls.append(("process_query_tool", kwargs))
        return ToolResult(
            tool_name="process_query_tool",
            success=True,
            data={"status": "ok", **kwargs, "processes": [], "count": 0},
        )

    def port_query(self, executor: Any, **kwargs: Any) -> ToolResult:
        self.calls.append(("port_query_tool", kwargs))
        return ToolResult(
            tool_name="port_query_tool",
            success=True,
            data={"status": "listening", "port": kwargs["port"], "listeners": []},
        )

    def create_user(self, executor: Any, **kwargs: Any) -> ToolResult:
        self.calls.append(("create_user_tool", kwargs))
        return ToolResult(
            tool_name="create_user_tool",
            success=True,
            data={"status": "created", **kwargs},
        )

    def delete_user(self, executor: Any, **kwargs: Any) -> ToolResult:
        self.calls.append(("delete_user_tool", kwargs))
        return ToolResult(
            tool_name="delete_user_tool",
            success=True,
            data={"status": "deleted", **kwargs},
        )


def make_orchestrator(
    mocks: ToolMocks,
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


def test_successfully_records_last_username() -> None:
    mocks = ToolMocks()
    memory = AgentMemory(session_id="session-a")
    orchestrator = make_orchestrator(mocks, memory)

    result = orchestrator.run("请创建普通用户 demo_guest")

    assert result["result"]["status"] == "pending_confirmation"
    assert memory.last_username == "demo_guest"
    assert memory.last_intent == "create_user"
    assert memory.last_risk_level == "S1"
    assert memory.to_dict()["session_id"] == "session-a"
    assert memory.to_dict()["last_username"] == "demo_guest"


def test_successfully_records_last_port() -> None:
    mocks = ToolMocks()
    memory = AgentMemory()
    orchestrator = make_orchestrator(mocks, memory)

    result = orchestrator.run("查一下 8080 端口")

    assert result["result"]["status"] == "success"
    assert memory.last_port == 8080
    assert memory.last_intent == "query_port"
    assert memory.last_risk_level == "S0"
    assert mocks.calls == [("env_probe_tool", {}), ("port_query_tool", {"port": 8080})]


def test_successfully_records_last_path() -> None:
    mocks = ToolMocks()
    memory = AgentMemory()
    orchestrator = make_orchestrator(mocks, memory)

    result = orchestrator.run("在 /var/log 里找文件名包含 nginx 的文件，最多返回 20 条")

    assert result["result"]["status"] == "success"
    assert memory.last_path == "/var/log"
    assert memory.last_intent == "search_files"
    assert mocks.calls[1] == (
        "file_search_tool",
        {
            "base_path": "/var/log",
            "name_contains": "nginx",
            "modified_within_days": None,
            "max_results": 20,
            "max_depth": 4,
        },
    )


def test_delete_contextual_user_resolves_to_last_username_and_requires_confirmation() -> None:
    mocks = ToolMocks()
    memory = AgentMemory()
    orchestrator = make_orchestrator(mocks, memory)

    orchestrator.run("请创建普通用户 demo_guest")
    orchestrator.run("确认创建普通用户 demo_guest")
    result = orchestrator.run("删除刚才那个用户")

    assert result["intent"]["intent"] == "delete_user"
    assert result["intent"]["target"]["username"] == "demo_guest"
    assert result["intent"]["context_refs"] == ["刚才那个用户"]
    assert result["risk"]["risk_level"] == "S2"
    assert result["result"]["status"] == "pending_confirmation"
    assert result["result"]["confirmation_text"] == "确认删除普通用户 demo_guest"
    assert memory.pending_action is not None
    assert memory.pending_action.tool_name == "delete_user_tool"
    assert memory.pending_action.tool_args["username"] == "demo_guest"
    assert ("delete_user_tool", {"username": "demo_guest", "remove_home": False}) not in mocks.calls


def test_delete_contextual_user_without_memory_does_not_guess_or_execute() -> None:
    mocks = ToolMocks()
    memory = AgentMemory()
    orchestrator = make_orchestrator(mocks, memory)

    result = orchestrator.run("删除刚才那个用户")

    assert result["intent"]["intent"] == "unknown"
    assert result["intent"]["constraints"]["unresolved_context_ref"] == "username"
    assert result["intent"]["context_refs"] == ["刚才那个用户"]
    assert result["result"]["status"] == "refused"
    assert "无法解析该引用" in result["result"]["error"]
    assert result["execution"]["status"] == "skipped"
    assert mocks.calls == []
    assert memory.last_username is None
    assert memory.pending_action is None


def test_missing_context_ref_does_not_guess_or_execute() -> None:
    mocks = ToolMocks()
    memory = AgentMemory()
    orchestrator = make_orchestrator(mocks, memory)

    result = orchestrator.run("查一下刚才那个端口")

    assert result["intent"]["intent"] == "unknown"
    assert result["intent"]["constraints"]["unresolved_context_ref"] == "port"
    assert result["result"]["status"] == "refused"
    assert "无法解析该引用" in result["result"]["error"]
    assert "无法解析该引用" in result["explanation"]
    assert result["execution"]["status"] == "skipped"
    assert mocks.calls == []
    assert memory.last_port is None


def test_pending_action_is_not_overwritten_by_later_request() -> None:
    mocks = ToolMocks()
    memory = AgentMemory()
    orchestrator = make_orchestrator(mocks, memory)

    orchestrator.run("请创建普通用户 first_user")
    result = orchestrator.run("请创建普通用户 second_user")

    assert result["result"]["status"] == "pending_confirmation"
    assert result["result"]["error"] == "confirmation_text_mismatch"
    assert memory.pending_action is not None
    assert memory.pending_action.target["username"] == "first_user"
    assert memory.last_username == "first_user"
    assert mocks.calls == []


def test_s3_refusal_does_not_pollute_context() -> None:
    mocks = ToolMocks()
    memory = AgentMemory()
    orchestrator = make_orchestrator(mocks, memory)

    result = orchestrator.run("请创建普通用户 root")

    assert result["risk"]["risk_level"] == "S3"
    assert result["result"]["status"] == "refused"
    assert result["execution"]["status"] == "skipped"
    assert mocks.calls == []
    assert memory.pending_action is None
    assert memory.last_username is None
    assert memory.last_intent is None
    assert memory.last_risk_level is None
