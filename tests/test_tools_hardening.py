from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import CommandResult
from app.tools import safe_run
from app.tools.disk import disk_usage_tool
from app.tools.file_search import file_search_tool
from app.tools.memory import memory_usage_tool
from app.tools.port import port_query_tool
from app.tools.process import process_query_tool
from app.tools.user import (
    CREATE_USER_WRAPPER,
    DELETE_USER_WRAPPER,
    WRAPPER_DIR,
    create_user_tool,
    delete_user_tool,
)


class MockExecutor:
    def __init__(self, responses: list[CommandResult] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[tuple[list[str], int]] = []

    def run(self, argv: list[str], timeout: int = 10) -> CommandResult:
        self.calls.append((argv, timeout))
        if not self.responses:
            raise AssertionError(f"unexpected executor call: {argv}")
        return self.responses.pop(0)


class RaisingExecutor:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, argv: list[str], timeout: int = 10) -> CommandResult:
        self.calls.append(argv)
        raise RuntimeError("connection reset by peer")


def command_result(
    argv: list[str],
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
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


def passwd_line(username: str, uid: int = 1001) -> str:
    return f"{username}:x:{uid}:{uid}::/home/{username}:/bin/bash\n"


HOSTILE_BASE_PATHS = [
    "-delete",
    "-L",
    "-exec",
    "--help",
    "-delete /tmp",
    "../../etc",
    "relative/path",
    "var/log",
    ".",
    "..",
    "",
    "   ",
    "//",
    "///",
    "/",
    "/../",
    "/var/log/../..",
    "/proc",
    "/proc//self",
    "/proc/../proc/self",
    "/var/log/../../proc/1",
    "/sys/kernel",
    "/dev",
    "/dev/../dev/shm",
]


@pytest.mark.parametrize("base_path", HOSTILE_BASE_PATHS)
def test_file_search_refuses_option_like_and_unbounded_base_paths(base_path: str) -> None:
    executor = MockExecutor()

    result = file_search_tool(executor, base_path)

    assert result.success is False
    assert result.data["status"] == "refused"
    assert result.data["results"] == []
    assert executor.calls == []


def test_file_search_refuses_non_string_base_path() -> None:
    executor = MockExecutor()

    for base_path in [None, 5, ["/var/log"], {"path": "/var/log"}]:
        result = file_search_tool(executor, base_path)  # type: ignore[arg-type]

        assert result.success is False
        assert result.data["status"] == "refused"

    assert executor.calls == []


def test_file_search_argv_never_starts_with_an_option() -> None:
    for base_path in ["/var/log", "/home/demo", "/opt/app//logs", "/var/log/./nginx"]:
        executor = MockExecutor([command_result(["find"], stdout="")])

        file_search_tool(executor, base_path)

        argv = executor.calls[0][0]
        assert argv[0] == "find"
        assert argv[1].startswith("/")
        assert not argv[1].startswith("-")


def test_file_search_normalizes_base_path_before_argv() -> None:
    executor = MockExecutor([command_result(["find"], stdout="")])

    result = file_search_tool(executor, "/var//log/nginx/../")

    assert executor.calls[0][0][1] == "/var/log"
    assert result.data["base_path"] == "/var/log"


def test_file_search_rejects_control_characters_in_inputs() -> None:
    executor = MockExecutor()

    path_result = file_search_tool(executor, "/var/log\nrm -rf /")
    name_result = file_search_tool(executor, "/var/log", name_contains="a\x00b")

    assert path_result.success is False
    assert name_result.success is False
    assert executor.calls == []


def test_file_search_keeps_results_when_find_reports_permission_denied() -> None:
    stdout = "\n".join(
        [
            "/var/log/nginx/access.log\taccess.log\t120\t1713772800.0",
            "/var/log/nginx/error.log\terror.log\t64\t1713772900.0",
            "",
        ]
    )
    stderr = "find: '/var/log/private': Permission denied"
    executor = MockExecutor([command_result(["find"], stdout=stdout, stderr=stderr, exit_code=1)])

    result = file_search_tool(executor, "/var/log", name_contains="nginx")

    assert result.success is True
    assert result.data["status"] == "ok"
    assert result.data["count"] == 2
    assert result.data["partial"] is True
    assert result.data["warnings"] == [stderr]
    assert result.error is None


def test_file_search_reports_error_when_exit_one_produced_no_rows() -> None:
    executor = MockExecutor(
        [command_result(["find"], stderr="find: '/var/log': Permission denied", exit_code=1)]
    )

    result = file_search_tool(executor, "/var/log")

    assert result.success is False
    assert result.data["status"] == "error"
    assert result.data["partial"] is False


def test_file_search_reports_error_for_hard_find_failures() -> None:
    stdout = "/var/log/app.log\tapp.log\t10\t1713772800.0\n"
    executor = MockExecutor([command_result(["find"], stdout=stdout, stderr="boom", exit_code=2)])

    result = file_search_tool(executor, "/var/log")

    assert result.success is False
    assert result.data["status"] == "error"
    assert result.data["exit_code"] == 2


def test_file_search_reports_unsupported_environment_for_non_gnu_find() -> None:
    executor = MockExecutor(
        [command_result(["find"], stderr="find: unknown predicate `-printf'", exit_code=1)]
    )

    result = file_search_tool(executor, "/var/log")

    assert result.success is False
    assert result.data["status"] == "unsupported_on_current_environment"
    assert result.data["attempted_sources"] == ["find"]
    assert "find" in result.error


def test_file_search_survives_executor_exception() -> None:
    executor = RaisingExecutor()

    result = file_search_tool(executor, "/var/log")

    assert result.success is False
    assert result.data["status"] == "error"
    assert "executor failed" in result.error


def test_disk_usage_filters_pseudo_filesystems_out_of_the_summary() -> None:
    stdout = "\n".join(
        [
            "Filesystem     Type     Size  Used Avail Use% Mounted on",
            "/dev/sda1      ext4      50G   20G   28G  42% /",
            "/dev/loop3     squashfs  64M   64M     0 100% /snap/core20/1974",
            "tmpfs          tmpfs    1.0G  1.0G     0 100% /run/user/1000",
            "udev           devtmpfs 3.9G     0  3.9G   0% /dev",
            "overlay        overlay   50G   45G  2.5G  95% /var/lib/docker/overlay2/x",
        ]
    )
    executor = MockExecutor([command_result(["df", "-hT"], stdout=stdout)])

    result = disk_usage_tool(executor)

    mount_points = [row["mounted_on"] for row in result.data["filesystems"]]
    assert mount_points == ["/", "/var/lib/docker/overlay2/x"]
    assert result.data["count"] == 2
    assert result.data["pseudo_count"] == 3
    assert result.data["source"] == "df -hT"


def test_disk_usage_falls_back_to_plain_df_when_type_flag_is_unsupported() -> None:
    stdout = "\n".join(
        [
            "Filesystem      Size  Used Avail Use% Mounted on",
            "/dev/disk1s1    50G    20G   28G  42% /",
            "devfs          190K   190K     0 100% /dev",
        ]
    )
    executor = MockExecutor(
        [
            command_result(["df", "-hT"], stderr="df: invalid option -- 'T'", exit_code=1),
            command_result(["df", "-h"], stdout=stdout),
        ]
    )

    result = disk_usage_tool(executor)

    assert result.success is True
    assert result.data["source"] == "df -h"
    assert result.data["count"] == 1
    assert result.data["filesystems"][0]["mounted_on"] == "/"
    assert result.data["filesystems"][0]["type"] == "unknown"
    assert result.data["pseudo_count"] == 1
    assert [call[0] for call in executor.calls] == [["df", "-hT"], ["df", "-h"]]


def test_disk_usage_reports_unsupported_environment_without_df() -> None:
    executor = MockExecutor(
        [
            command_result(["df", "-hT"], stderr="command not found: df", exit_code=127),
            command_result(["df", "-h"], stderr="command not found: df", exit_code=127),
        ]
    )

    result = disk_usage_tool(executor)

    assert result.success is False
    assert result.data["status"] == "unsupported_on_current_environment"
    assert result.data["attempted_sources"] == ["df -hT", "df -h"]
    assert "df" in result.data["missing_tools"]


def test_disk_usage_survives_executor_exception() -> None:
    executor = RaisingExecutor()

    result = disk_usage_tool(executor)

    assert result.success is False
    assert result.data["status"] == "error"
    assert "executor failed" in result.error


def test_process_query_reports_executor_truncation() -> None:
    stdout = (
        "123 root 1.0 1.0 4096 python python app.py\n"
        "...[truncated 4096 chars]"
    )
    executor = MockExecutor([command_result(["ps"], stdout=stdout)])

    result = process_query_tool(executor, mode="cpu", limit=10)

    assert result.success is True
    assert result.data["count"] == 1
    assert result.data["truncated"] is True


def test_process_query_returns_memory_bytes_on_linux() -> None:
    stdout = "999 postgres 0.5 22.0 2097152 postgres postgres: checkpointer\n"
    executor = MockExecutor([command_result(["ps"], stdout=stdout)])

    result = process_query_tool(executor, mode="memory", limit=5)

    process = result.data["processes"][0]
    assert process["memory_bytes"] == 2097152 * 1024
    assert process["memory_percent"] == 22.0
    assert process["command"] == "postgres"
    assert process["args"] == "postgres: checkpointer"
    assert "rss=" in executor.calls[0][0]


def test_process_query_refuses_non_integer_pid_without_executor_call() -> None:
    executor = MockExecutor()

    result = process_query_tool(executor, mode="pid", pid="7; rm -rf /")  # type: ignore[arg-type]

    assert result.success is False
    assert result.data["status"] == "refused"
    assert executor.calls == []


def test_process_query_forwards_validated_pid_to_windows_fallback() -> None:
    class HostilePid:
        def __int__(self) -> int:
            return 42

        def __str__(self) -> str:
            return "42; Remove-Item -Recurse C:\\"

    executor = MockExecutor(
        [
            command_result(["ps"], stderr="ps: unknown option -- o", exit_code=1),
            command_result(["powershell"], stdout='[{"pid": 42, "command": "Code"}]'),
        ]
    )

    result = process_query_tool(executor, mode="pid", pid=HostilePid())  # type: ignore[arg-type]

    script = executor.calls[1][0][3]
    assert "Get-Process -Id 42 " in script
    assert "Remove-Item" not in script
    assert result.data["pid"] == 42


def test_process_query_survives_executor_exception() -> None:
    executor = RaisingExecutor()

    result = process_query_tool(executor, mode="cpu")

    assert result.success is False
    assert result.data["status"] == "error"
    assert "executor failed" in result.error


def test_port_query_falls_back_to_lsof_when_ss_hides_the_owner() -> None:
    ss_stdout = "\n".join(
        [
            "Netid State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process",
            "tcp   LISTEN 0      511    0.0.0.0:8080       0.0.0.0:*",
        ]
    )
    lsof_stdout = "\n".join(
        [
            "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME",
            "python 123 appuser 3u IPv4 12345 0t0 TCP *:8080 (LISTEN)",
        ]
    )
    executor = MockExecutor(
        [
            command_result(["ss", "-ltnup"], stdout=ss_stdout),
            command_result(["lsof"], stdout=lsof_stdout),
        ]
    )

    result = port_query_tool(executor, 8080)

    assert result.success is True
    assert result.data["status"] == "listening"
    assert result.data["source"] == "lsof"
    assert result.data["listeners"][0]["pid"] == 123
    assert result.data["listeners"][0]["user"] == "appuser"
    assert result.data["attempted_sources"] == ["ss", "lsof"]
    assert [call[0][0] for call in executor.calls] == ["ss", "lsof"]


def test_port_query_keeps_ss_rows_when_lsof_cannot_resolve_owner() -> None:
    ss_stdout = "\n".join(
        [
            "Netid State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process",
            "tcp   LISTEN 0      511    0.0.0.0:8080       0.0.0.0:*",
        ]
    )
    executor = MockExecutor(
        [
            command_result(["ss", "-ltnup"], stdout=ss_stdout),
            command_result(["lsof"], stderr="lsof: command not found", exit_code=127),
        ]
    )

    result = port_query_tool(executor, 8080)

    assert result.success is True
    assert result.data["status"] == "listening"
    assert result.data["source"] == "ss"
    assert result.data["count"] == 1
    assert result.data["listeners"][0]["pid"] is None


def test_port_query_does_not_call_lsof_when_ss_resolved_the_owner() -> None:
    ss_stdout = "\n".join(
        [
            "Netid State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process",
            'tcp   LISTEN 0 511 0.0.0.0:8080 0.0.0.0:* users:(("gunicorn",pid=77,fd=6))',
        ]
    )
    executor = MockExecutor(
        [
            command_result(["ss", "-ltnup"], stdout=ss_stdout),
            command_result(["ps"], stdout="appuser gunicorn\n"),
        ]
    )

    result = port_query_tool(executor, 8080)

    assert result.data["source"] == "ss"
    assert result.data["listeners"][0]["pid"] == 77
    assert result.data["listeners"][0]["user"] == "appuser"
    assert [call[0][0] for call in executor.calls] == ["ss", "ps"]


def test_port_query_records_every_process_sharing_a_listening_socket() -> None:
    ss_stdout = "\n".join(
        [
            "Netid State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process",
            'tcp LISTEN 0 511 0.0.0.0:80 0.0.0.0:* '
            'users:(("nginx",pid=1234,fd=6),("nginx",pid=1235,fd=6),("nginx",pid=1236,fd=6))',
        ]
    )
    executor = MockExecutor(
        [
            command_result(["ss", "-ltnup"], stdout=ss_stdout),
            command_result(["ps"], stdout="root nginx\n"),
            command_result(["ps"], stdout="www-data nginx\n"),
            command_result(["ps"], stdout="www-data nginx\n"),
        ]
    )

    result = port_query_tool(executor, 80)

    listener = result.data["listeners"][0]
    assert listener["pid"] == 1234
    assert [entry["pid"] for entry in listener["processes"]] == [1234, 1235, 1236]
    assert [entry["user"] for entry in listener["processes"]] == [
        "root",
        "www-data",
        "www-data",
    ]


def test_port_query_does_not_mistake_permission_errors_for_missing_tools() -> None:
    executor = MockExecutor(
        [
            command_result(
                ["ss", "-ltnup"],
                stderr="cannot access netlink socket: Operation not permitted",
                exit_code=127,
            ),
            command_result(
                ["lsof"],
                stderr="lsof: WARNING: cannot access /proc, exiting",
                exit_code=127,
            ),
        ]
    )

    result = port_query_tool(executor, 8080)

    assert result.data["status"] != "unsupported_on_current_environment"
    assert result.success is False
    assert result.data["status"] == "error"


def test_port_query_survives_executor_exception() -> None:
    executor = RaisingExecutor()

    result = port_query_tool(executor, 8080)

    assert result.success is False
    assert result.data["status"] == "error"


def test_memory_usage_survives_executor_exception() -> None:
    executor = RaisingExecutor()

    result = memory_usage_tool(executor)

    assert result.success is False
    assert result.data["status"] == "error"


def test_user_wrappers_are_anchored_to_the_repository_scripts_directory() -> None:
    assert WRAPPER_DIR.is_dir()
    for wrapper in (CREATE_USER_WRAPPER, DELETE_USER_WRAPPER):
        path = Path(wrapper)
        assert path.is_absolute()
        assert path.is_file()


def test_create_user_invokes_an_absolute_wrapper_path_from_any_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    username = "demo_user"
    executor = MockExecutor(
        [
            command_result(["getent", "passwd", username], exit_code=2),
            command_result(["bash"]),
            command_result(["getent", "passwd", username], stdout=passwd_line(username)),
        ]
    )

    result = create_user_tool(executor, username)

    assert result.success is True
    wrapper_argv = executor.calls[1][0]
    assert wrapper_argv[0] == "bash"
    wrapper_path = Path(wrapper_argv[1])
    assert wrapper_path.is_absolute()
    assert wrapper_path.is_file()
    assert wrapper_path.name == "guardedops_create_user.sh"


def test_delete_user_invokes_an_absolute_wrapper_path_from_any_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    username = "demo_user"
    executor = MockExecutor(
        [
            command_result(["getent", "passwd", username], stdout=passwd_line(username)),
            command_result(["id", "-un"], stdout="operator\n"),
            command_result(["bash"]),
            command_result(["getent", "passwd", username], exit_code=2),
        ]
    )

    result = delete_user_tool(executor, username)

    assert result.success is True
    wrapper_argv = executor.calls[2][0]
    wrapper_path = Path(wrapper_argv[1])
    assert wrapper_path.is_absolute()
    assert wrapper_path.is_file()
    assert wrapper_path.name == "guardedops_delete_user.sh"


def test_create_user_refuses_when_wrapper_script_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.tools.user.CREATE_USER_WRAPPER",
        str(WRAPPER_DIR / "guardedops_create_user_missing.sh"),
    )
    username = "demo_user"
    executor = MockExecutor([command_result(["getent", "passwd", username], exit_code=2)])

    result = create_user_tool(executor, username)

    assert result.success is False
    assert result.data["status"] == "refused"
    assert "wrapper script is missing" in result.error
    assert [call[0][0] for call in executor.calls] == ["getent"]


def test_delete_user_refuses_when_wrapper_script_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.tools.user.DELETE_USER_WRAPPER",
        str(WRAPPER_DIR / "guardedops_delete_user_missing.sh"),
    )
    username = "demo_user"
    executor = MockExecutor(
        [
            command_result(["getent", "passwd", username], stdout=passwd_line(username)),
            command_result(["id", "-un"], stdout="operator\n"),
        ]
    )

    result = delete_user_tool(executor, username)

    assert result.success is False
    assert result.data["status"] == "refused"
    assert "wrapper script is missing" in result.error
    assert [call[0][0] for call in executor.calls] == ["getent", "id"]


def test_create_user_refuses_relative_wrapper_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.tools.user.CREATE_USER_WRAPPER",
        "scripts/guardedops_create_user.sh",
    )
    username = "demo_user"
    executor = MockExecutor([command_result(["getent", "passwd", username], exit_code=2)])

    result = create_user_tool(executor, username)

    assert result.success is False
    assert result.data["status"] == "refused"
    assert "must be absolute" in result.error


def test_safe_run_normalizes_executor_failures() -> None:
    class BrokenExecutor:
        def run(self, argv: list[str], timeout: int = 10) -> Any:
            return "not a command result"

    raising = safe_run(RaisingExecutor(), ["df", "-hT"], timeout=5)
    broken = safe_run(BrokenExecutor(), ["df", "-hT"], timeout=5)

    assert raising.success is False
    assert raising.exit_code == -1
    assert "executor failed" in raising.stderr
    assert broken.success is False
    assert "unsupported result type" in broken.stderr
