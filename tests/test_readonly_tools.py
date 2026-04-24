from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import CommandResult
from app.tools.disk import disk_usage_tool
from app.tools.file_search import file_search_tool
from app.tools.memory import memory_usage_tool
from app.tools.port import port_query_tool
from app.tools.process import process_query_tool


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
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        success=exit_code == 0,
    )


def test_disk_usage_tool_returns_basic_structure() -> None:
    stdout = "\n".join(
        [
            "Filesystem     Type  Size  Used Avail Use% Mounted on",
            "/dev/sda1      ext4   50G   20G   28G  42% /",
            "tmpfs          tmpfs 1.0G     0  1.0G   0% /run",
        ]
    )
    executor = MockExecutor([command_result(["df", "-hT"], stdout=stdout)])

    result = disk_usage_tool(executor)

    assert result.success is True
    assert result.tool_name == "disk_usage_tool"
    assert result.data["status"] == "ok"
    assert result.data["count"] == 2
    assert result.data["filesystems"][0] == {
        "filesystem": "/dev/sda1",
        "type": "ext4",
        "size": "50G",
        "used": "20G",
        "available": "28G",
        "use_percent": "42%",
        "mounted_on": "/",
    }
    assert executor.calls == [(["df", "-hT"], 10)]


def test_file_search_tool_searches_temp_directory(tmp_path: Path) -> None:
    base_path = tmp_path / "logs"
    base_path.mkdir()
    matched_file = base_path / "app.log"
    matched_file.write_text("ok", encoding="utf-8")

    stdout = f"{matched_file}\tapp.log\t2\t1713772800.0\n"
    executor = MockExecutor([command_result(["find"], stdout=stdout)])

    result = file_search_tool(
        executor,
        str(base_path),
        name_contains="app",
        modified_within_days=7,
    )

    assert result.success is True
    assert result.data["status"] == "ok"
    assert result.data["count"] == 1
    assert result.data["truncated"] is False
    assert result.data["results"][0]["path"] == str(matched_file)
    assert result.data["results"][0]["name"] == "app.log"
    argv, timeout = executor.calls[0]
    assert argv == [
        "find",
        str(base_path),
        "-maxdepth",
        "4",
        "-type",
        "f",
        "-iname",
        "*app*",
        "-mtime",
        "-7",
        "-printf",
        "%p\t%f\t%s\t%T@\n",
    ]
    assert timeout == 15


def test_file_search_tool_enforces_max_results_and_max_depth(tmp_path: Path) -> None:
    base_path = tmp_path / "data"
    base_path.mkdir()
    stdout = "".join(
        f"{base_path / f'file-{index}.txt'}\tfile-{index}.txt\t{index}\t17137728{index}.0\n"
        for index in range(52)
    )
    executor = MockExecutor([command_result(["find"], stdout=stdout)])

    result = file_search_tool(
        executor,
        str(base_path),
        max_results=500,
        max_depth=99,
    )

    assert result.success is True
    assert result.data["max_results"] == 50
    assert result.data["max_depth"] == 8
    assert result.data["count"] == 50
    assert result.data["truncated"] is True
    argv, _timeout = executor.calls[0]
    assert argv[3] == "8"


def test_file_search_tool_refuses_dangerous_ranges_without_executor_call() -> None:
    for base_path in ["/", "/proc", "/proc/self", "/sys", "/dev"]:
        executor = MockExecutor([])

        result = file_search_tool(executor, base_path)

        assert result.success is False
        assert result.data["status"] == "refused"
        assert result.data["results"] == []
        assert executor.calls == []


def test_process_query_tool_returns_basic_structure() -> None:
    stdout = "\n".join(
        [
            "123 root 12.5 1.0 python python app.py",
            "456 app 2.0 8.5 postgres postgres: writer",
        ]
    )
    executor = MockExecutor([command_result(["ps"], stdout=stdout)])

    result = process_query_tool(executor, mode="cpu", limit=1)

    assert result.success is True
    assert result.data["status"] == "ok"
    assert result.data["mode"] == "cpu"
    assert result.data["count"] == 1
    assert result.data["truncated"] is True
    assert result.data["processes"][0] == {
        "pid": 123,
        "user": "root",
        "cpu_percent": 12.5,
        "memory_percent": 1.0,
        "command": "python",
        "args": "python app.py",
    }
    argv, timeout = executor.calls[0]
    assert argv[:2] == ["ps", "-eo"]
    assert "--sort=-pcpu" in argv
    assert timeout == 10


def test_memory_usage_tool_parses_linux_meminfo_and_process_ranking() -> None:
    meminfo = "\n".join(
        [
            "MemTotal:       16384000 kB",
            "MemFree:         1024000 kB",
            "MemAvailable:    4096000 kB",
            "Buffers:          256000 kB",
            "Cached:          2048000 kB",
        ]
    )
    ps_output = "\n".join(
        [
            "123 root 3.5 12.0 postgres postgres: writer",
            "456 demo 2.0 7.5 python python app.py",
        ]
    )
    executor = MockExecutor(
        [
            command_result(["cat", "/proc/meminfo"], stdout=meminfo),
            command_result(["ps"], stdout=ps_output),
        ]
    )

    result = memory_usage_tool(executor, limit=1)

    assert result.success is True
    assert result.tool_name == "memory_usage_tool"
    assert result.data["status"] == "ok"
    assert result.data["source"] == "/proc/meminfo"
    assert result.data["total_bytes"] == 16384000 * 1024
    assert result.data["available_bytes"] == 4096000 * 1024
    assert result.data["used_percent"] == 75.0
    assert result.data["process_source"] == "ps"
    assert result.data["process_error"] == ""
    assert result.data["top_processes"][0]["command"] == "postgres"
    assert result.data["top_processes"][0]["memory_percent"] == 12.0
    assert executor.calls[0] == (["cat", "/proc/meminfo"], 10)
    assert executor.calls[1][0][:2] == ["ps", "-eo"]
    assert "--sort=-pmem" in executor.calls[1][0]


def test_memory_usage_tool_parses_windows_cim_json_and_process_ranking() -> None:
    memory_json = '{"TotalVisibleMemorySize": 8388608, "FreePhysicalMemory": 2097152}'
    process_json = (
        '[{"pid": 42, "user": null, "memory_bytes": 536870912, '
        '"command": "Code", "args": ""}]'
    )
    executor = MockExecutor(
        [
            command_result(["cat", "/proc/meminfo"], exit_code=1, stderr="not found"),
            command_result(["powershell"], stdout=memory_json),
            command_result(["powershell"], stdout=process_json),
        ]
    )

    result = memory_usage_tool(executor)

    assert result.success is True
    assert result.data["source"] == "Win32_OperatingSystem"
    assert result.data["total_bytes"] == 8388608 * 1024
    assert result.data["available_bytes"] == 2097152 * 1024
    assert result.data["used_percent"] == 75.0
    assert result.data["process_source"] == "Get-Process"
    assert result.data["top_processes"][0]["pid"] == 42
    assert result.data["top_processes"][0]["memory_bytes"] == 536870912
    assert executor.calls[1][0][:3] == ["powershell", "-NoProfile", "-Command"]
    assert executor.calls[2][0][:3] == ["powershell", "-NoProfile", "-Command"]


def test_memory_usage_tool_keeps_summary_success_when_process_ranking_fails() -> None:
    meminfo = "\n".join(
        [
            "MemTotal:       1048576 kB",
            "MemAvailable:    524288 kB",
        ]
    )
    executor = MockExecutor(
        [
            command_result(["cat", "/proc/meminfo"], stdout=meminfo),
            command_result(["ps"], exit_code=1, stderr="ps unavailable"),
        ]
    )

    result = memory_usage_tool(executor)

    assert result.success is True
    assert result.data["status"] == "ok"
    assert result.data["available_bytes"] == 524288 * 1024
    assert result.data["top_processes"] == []
    assert result.data["process_source"] == "ps"
    assert result.data["process_error"] == "ps unavailable"


def test_memory_usage_tool_uses_windows_process_fallback_for_incompatible_ps() -> None:
    meminfo = "\n".join(
        [
            "MemTotal:       1048576 kB",
            "MemAvailable:    524288 kB",
        ]
    )
    process_json = (
        '[{"pid": 42, "user": null, "memory_bytes": 536870912, '
        '"command": "Code", "args": ""}]'
    )
    executor = MockExecutor(
        [
            command_result(["cat", "/proc/meminfo"], stdout=meminfo),
            command_result(["ps"], exit_code=1, stderr="ps: unknown option -- o"),
            command_result(["powershell"], stdout=process_json),
        ]
    )

    result = memory_usage_tool(executor)

    assert result.success is True
    assert result.data["process_source"] == "Get-Process"
    assert result.data["process_error"] == ""
    assert result.data["top_processes"][0]["command"] == "Code"
    assert result.data["top_processes"][0]["memory_bytes"] == 536870912


def test_port_query_tool_returns_not_listening_for_unused_port() -> None:
    stdout = "Netid State Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
    executor = MockExecutor([command_result(["ss", "-ltnup"], stdout=stdout)])

    result = port_query_tool(executor, 65000)

    assert result.success is True
    assert result.tool_name == "port_query_tool"
    assert result.data["status"] == "not_listening"
    assert result.data["port"] == 65000
    assert result.data["listeners"] == []
    assert result.data["source"] == "ss"
    assert executor.calls == [(["ss", "-ltnup"], 10)]


def test_port_query_tool_reports_unsupported_when_query_tools_are_missing() -> None:
    executor = MockExecutor(
        [
            command_result(
                ["ss", "-ltnup"],
                stderr="command not found: ss",
                exit_code=127,
            ),
            command_result(
                ["lsof", "-nP", "-iTCP:8080", "-sTCP:LISTEN"],
                stderr="command not found: lsof",
                exit_code=127,
            ),
        ]
    )

    result = port_query_tool(executor, 8080)

    assert result.success is False
    assert result.tool_name == "port_query_tool"
    assert result.data["status"] == "unsupported_on_current_environment"
    assert result.data["port"] == 8080
    assert result.data["listeners"] == []
    assert result.data["count"] == 0
    assert result.data["source"] == "none"
    assert result.data["missing_tools"] == ["ss", "lsof"]
    assert result.data["attempted_sources"] == ["ss", "lsof"]
    assert "缺少端口查询所需的系统工具" in result.error
    assert executor.calls == [
        (["ss", "-ltnup"], 10),
        (["lsof", "-nP", "-iTCP:8080", "-sTCP:LISTEN"], 10),
    ]


def test_port_query_tool_falls_back_to_lsof_when_ss_is_missing() -> None:
    stdout = "\n".join(
        [
            "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME",
            "python 123 appuser 3u IPv4 12345 0t0 TCP *:8080 (LISTEN)",
        ]
    )
    executor = MockExecutor(
        [
            command_result(
                ["ss", "-ltnup"],
                stderr="command not found: ss",
                exit_code=127,
            ),
            command_result(
                ["lsof", "-nP", "-iTCP:8080", "-sTCP:LISTEN"],
                stdout=stdout,
            ),
        ]
    )

    result = port_query_tool(executor, 8080)

    assert result.success is True
    assert result.data["status"] == "listening"
    assert result.data["source"] == "lsof"
    assert result.data["count"] == 1
    assert result.data["listeners"][0]["pid"] == 123
    assert result.data["listeners"][0]["process_name"] == "python"
    assert result.data["listeners"][0]["user"] == "appuser"
    assert executor.calls == [
        (["ss", "-ltnup"], 10),
        (["lsof", "-nP", "-iTCP:8080", "-sTCP:LISTEN"], 10),
    ]
