from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import ReadonlyOrchestrator
import app.cli as cli


def _response(
    *,
    status: str = "success",
    explanation: str = "已完成只读查询。",
    intent: str = "query_disk_usage",
) -> dict[str, Any]:
    return {
        "intent": {"intent": intent, "raw_user_input": "帮我查看当前磁盘使用情况"},
        "environment": {"status": "ok", "snapshot": {"connection_mode": "local"}},
        "risk": {"risk_level": "S0", "allow": status == "success"},
        "plan": {"status": "ready", "reason": None, "steps": []},
        "execution": {"status": status, "steps": [], "results": []},
        "result": {"status": status, "data": {"ok": True}, "error": None},
        "explanation": explanation,
    }


def test_cli_accepts_natural_language_input(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    executors: list[FakeExecutor] = []

    class FakeExecutor:
        pass

    class FakeOrchestrator:
        def __init__(self, executor: FakeExecutor) -> None:
            executors.append(executor)

        def run(self, raw_user_input: str) -> dict[str, Any]:
            calls.append(raw_user_input)
            return _response()

    monkeypatch.setattr(cli, "LocalExecutor", FakeExecutor)
    monkeypatch.setattr(cli, "ReadonlyOrchestrator", FakeOrchestrator)

    exit_code = cli.main(["帮我查看当前磁盘使用情况"])

    assert exit_code == 0
    assert len(executors) == 1
    assert isinstance(executors[0], FakeExecutor)
    assert calls == ["帮我查看当前磁盘使用情况"]


def test_cli_default_output_is_readable_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeOrchestrator:
        def __init__(self, _executor: object) -> None:
            pass

        def run(self, _raw_user_input: str) -> dict[str, Any]:
            return _response(explanation="端口 8080 当前没有监听记录。")

    monkeypatch.setattr(cli, "ReadonlyOrchestrator", FakeOrchestrator)

    exit_code = cli.main(["8080 端口现在是谁在占用"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "端口 8080 当前没有监听记录" in captured.out
    assert captured.err == ""


def test_cli_json_outputs_structured_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeOrchestrator:
        def __init__(self, _executor: object) -> None:
            pass

        def run(self, _raw_user_input: str) -> dict[str, Any]:
            return _response(
                explanation="已完成 CPU 进程查询。",
                intent="query_process",
            )

    monkeypatch.setattr(cli, "ReadonlyOrchestrator", FakeOrchestrator)

    exit_code = cli.main(["--json", "帮我看当前 CPU 占用最高的 10 个进程"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["intent"]["intent"] == "query_process"
    assert payload["result"]["status"] == "success"
    assert payload["explanation"] == "已完成 CPU 进程查询。"
    assert "已完成 CPU 进程查询" in captured.out


def test_unknown_write_like_request_does_not_execute_any_command(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class ExplodingExecutor:
        def run(self, argv: list[str], timeout: int = 10) -> object:
            raise AssertionError(f"executor must not be called: {argv}, {timeout}")

    monkeypatch.setattr(cli, "LocalExecutor", ExplodingExecutor)
    monkeypatch.setattr(cli, "ReadonlyOrchestrator", ReadonlyOrchestrator)

    exit_code = cli.main(["帮我创建一个用户 demo"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "当前只支持只读基础能力" in captured.out
    assert captured.err == ""


def test_cli_has_no_raw_shell_parameters() -> None:
    parser = cli.build_parser()
    option_strings = {
        option
        for action in parser._actions
        for option in action.option_strings
    }

    assert "--json" in option_strings
    assert "--shell" not in option_strings
    assert "--exec" not in option_strings
    assert "--bash" not in option_strings

    for flag in ["--shell", "--exec", "--bash"]:
        with pytest.raises(SystemExit) as exc_info:
            cli.main([flag, "whoami"])
        assert exc_info.value.code == 2
