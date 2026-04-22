from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import ReadonlyOrchestrator
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
        return ToolResult(
            tool_name="disk_usage_tool",
            success=True,
            data={
                "status": "ok",
                "count": 2,
                "filesystems": [
                    {
                        "filesystem": "/dev/sda1",
                        "type": "ext4",
                        "size": "50G",
                        "used": "20G",
                        "available": "28G",
                        "use_percent": "42%",
                        "mounted_on": "/",
                    },
                    {
                        "filesystem": "/dev/sdb1",
                        "type": "ext4",
                        "size": "100G",
                        "used": "91G",
                        "available": "9G",
                        "use_percent": "91%",
                        "mounted_on": "/data",
                    },
                ],
            },
        )

    def file_search(self, executor: Any, **kwargs: Any) -> ToolResult:
        self.calls.append(("file_search_tool", kwargs))
        return ToolResult(
            tool_name="file_search_tool",
            success=True,
            data={
                "status": "ok",
                **kwargs,
                "results": [{"path": "/var/log/nginx/access.log", "name": "access.log"}],
                "count": 1,
                "truncated": False,
            },
        )

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
                        "pid": 123,
                        "user": "root",
                        "cpu_percent": 12.5,
                        "memory_percent": 1.0,
                        "command": "python",
                        "args": "python app.py",
                    }
                ],
                "count": 1,
                "truncated": False,
            },
        )

    def port_query(self, executor: Any, **kwargs: Any) -> ToolResult:
        self.calls.append(("port_query_tool", kwargs))
        return ToolResult(
            tool_name="port_query_tool",
            success=True,
            data={
                "status": "listening",
                "port": kwargs["port"],
                "listeners": [
                    {
                        "protocol": "tcp",
                        "state": "LISTEN",
                        "local_address": f"0.0.0.0:{kwargs['port']}",
                        "pid": 456,
                        "process_name": "nginx",
                        "user": "www-data",
                    }
                ],
                "count": 1,
                "source": "ss",
            },
        )


def make_orchestrator(mocks: ToolMocks) -> ReadonlyOrchestrator:
    return ReadonlyOrchestrator(
        DummyExecutor(),
        env_probe=mocks.env_probe,
        disk_tool=mocks.disk_usage,
        file_search_tool_fn=mocks.file_search,
        process_query_tool_fn=mocks.process_query,
        port_query_tool_fn=mocks.port_query,
    )


def test_disk_query_request_closes_readonly_loop() -> None:
    mocks = ToolMocks()
    result = make_orchestrator(mocks).run("帮我查看当前磁盘使用情况")

    assert result["intent"]["intent"] == "query_disk_usage"
    assert result["risk"]["risk_level"] == "S0"
    assert result["risk"]["allow"] is True
    assert result["plan"]["steps"][1]["tool_name"] == "disk_usage_tool"
    assert result["result"]["status"] == "success"
    assert "最紧张" in result["explanation"]
    assert [name for name, _args in mocks.calls] == ["env_probe_tool", "disk_usage_tool"]


def test_file_search_request_parses_constraints_and_executes_tool() -> None:
    mocks = ToolMocks()
    result = make_orchestrator(mocks).run(
        "在 /var/log 里找最近 3 天修改过、文件名包含 nginx 的文件，最多返回 20 条"
    )

    assert result["intent"]["intent"] == "search_files"
    assert result["intent"]["target"]["path"] == "/var/log"
    assert result["intent"]["target"]["keyword"] == "nginx"
    assert result["result"]["status"] == "success"
    assert mocks.calls[1] == (
        "file_search_tool",
        {
            "base_path": "/var/log",
            "name_contains": "nginx",
            "modified_within_days": 3,
            "max_results": 20,
            "max_depth": 4,
        },
    )
    assert "文件检索" in result["explanation"]


def test_process_query_request_executes_cpu_top_plan() -> None:
    mocks = ToolMocks()
    result = make_orchestrator(mocks).run("帮我看当前 CPU 占用最高的 10 个进程")

    assert result["intent"]["intent"] == "query_process"
    assert mocks.calls[1] == (
        "process_query_tool",
        {"mode": "cpu", "limit": 10, "keyword": None, "pid": None},
    )
    assert result["result"]["status"] == "success"
    assert "PID 123" in result["explanation"]


def test_port_query_request_executes_port_tool() -> None:
    mocks = ToolMocks()
    result = make_orchestrator(mocks).run("8080 端口现在是谁在占用")

    assert result["intent"]["intent"] == "query_port"
    assert result["intent"]["target"]["port"] == 8080
    assert mocks.calls[1] == ("port_query_tool", {"port": 8080})
    assert result["result"]["status"] == "success"
    assert "nginx" in result["explanation"]


def test_unknown_request_does_not_execute_any_tool() -> None:
    mocks = ToolMocks()
    result = make_orchestrator(mocks).run("帮我创建一个用户 demo")

    assert result["intent"]["intent"] == "unknown"
    assert result["risk"]["allow"] is False
    assert result["result"]["status"] == "refused"
    assert result["execution"]["status"] == "skipped"
    assert mocks.calls == []
    assert "当前只支持只读基础能力" in result["explanation"]


def test_response_contains_stable_top_level_sections() -> None:
    mocks = ToolMocks()
    result = make_orchestrator(mocks).run("查一下 3306 端口")

    assert {
        "intent",
        "environment",
        "risk",
        "plan",
        "execution",
        "result",
        "explanation",
    } <= set(result)
