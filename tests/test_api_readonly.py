from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.chat import get_executor
from app.main import create_app
from app.models import CommandResult


def _result(
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


class MockExecutor:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, argv: list[str], timeout: int = 10) -> CommandResult:
        self.calls.append(argv)
        responses = _responses()
        key = tuple(argv)
        if key not in responses:
            return _result(argv, exit_code=127, stderr="command not found")
        return responses[key]


def _responses() -> dict[tuple[str, ...], CommandResult]:
    responses = {
        ("hostname",): _result(["hostname"], stdout="demo-host\n"),
        ("cat", "/etc/os-release"): _result(
            ["cat", "/etc/os-release"],
            stdout='PRETTY_NAME="Ubuntu 24.04 LTS"\n',
        ),
        ("uname", "-r"): _result(["uname", "-r"], stdout="6.8.0\n"),
        ("id", "-un"): _result(["id", "-un"], stdout="demo\n"),
        ("id", "-u"): _result(["id", "-u"], stdout="1000\n"),
        ("sudo", "-n", "true"): _result(
            ["sudo", "-n", "true"],
            exit_code=1,
            stderr="a password is required",
        ),
        ("df", "-hT"): _result(
            ["df", "-hT"],
            stdout="\n".join(
                [
                    "Filesystem     Type  Size  Used Avail Use% Mounted on",
                    "/dev/sda1      ext4   50G   20G   28G  42% /",
                    "/dev/sdb1      ext4  100G   91G    9G  91% /data",
                ]
            ),
        ),
    }

    for command, argv in {
        "df": ["df", "--version"],
        "find": ["find", "--version"],
        "ps": ["ps", "--version"],
        "ss": ["ss", "-V"],
        "lsof": ["lsof", "-v"],
        "getent": ["getent", "--help"],
        "useradd": ["useradd", "--help"],
        "userdel": ["userdel", "--help"],
        "sudo": ["sudo", "-V"],
    }.items():
        responses[tuple(argv)] = _result(argv, stdout=f"{command} available\n")

    return responses


def _client_with_executor(executor: MockExecutor) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_executor] = lambda: executor
    return TestClient(app)


def test_post_chat_returns_unified_response() -> None:
    client = _client_with_executor(MockExecutor())

    response = client.post("/api/chat", json={"raw_user_input": "帮我查看当前磁盘使用情况"})

    assert response.status_code == 200
    payload = response.json()
    assert {
        "intent",
        "environment",
        "risk",
        "plan",
        "execution",
        "result",
        "explanation",
    } <= set(payload)


def test_disk_query_returns_structured_result() -> None:
    executor = MockExecutor()
    client = _client_with_executor(executor)

    response = client.post("/api/chat", json={"raw_user_input": "帮我查看当前磁盘使用情况"})

    payload = response.json()
    assert payload["intent"]["intent"] == "query_disk_usage"
    assert payload["risk"]["risk_level"] == "S0"
    assert payload["risk"]["allow"] is True
    assert payload["environment"]["snapshot"]["hostname"] == "demo-host"
    assert payload["result"]["status"] == "success"
    assert payload["result"]["data"]["filesystems"][1]["mounted_on"] == "/data"
    assert payload["result"]["data"]["count"] == 2
    assert ["df", "-hT"] in executor.calls


def test_port_query_missing_tools_returns_structured_business_failure() -> None:
    executor = MockExecutor()
    client = _client_with_executor(executor)

    response = client.post("/api/chat", json={"raw_user_input": "8080 端口现在是谁在占用"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"]["intent"] == "query_port"
    assert payload["result"]["status"] == "failed"
    assert payload["result"]["tool_name"] == "port_query_tool"
    assert payload["result"]["data"]["status"] == "unsupported_on_current_environment"
    assert payload["result"]["data"]["port"] == 8080
    assert payload["result"]["data"]["listeners"] == []
    assert payload["result"]["data"]["count"] == 0
    assert payload["result"]["data"]["source"] == "none"
    assert payload["result"]["data"]["missing_tools"] == ["ss", "lsof"]
    assert "缺少端口查询所需的系统工具" in payload["result"]["error"]
    assert "缺少端口查询所需的系统工具" in payload["explanation"]
    assert ["ss", "-ltnup"] in executor.calls
    assert ["lsof", "-nP", "-iTCP:8080", "-sTCP:LISTEN"] in executor.calls


def test_unknown_or_write_request_is_refused_without_tool_execution() -> None:
    executor = MockExecutor()
    client = _client_with_executor(executor)

    response = client.post("/api/chat", json={"raw_user_input": "帮我创建一个用户 demo"})

    payload = response.json()
    assert response.status_code == 200
    assert payload["intent"]["intent"] == "unknown"
    assert payload["risk"]["allow"] is False
    assert payload["plan"]["status"] == "refused"
    assert payload["execution"]["status"] == "skipped"
    assert payload["result"]["status"] == "refused"
    assert executor.calls == []


def test_response_contains_required_display_fields() -> None:
    client = _client_with_executor(MockExecutor())

    payload = client.post(
        "/api/chat",
        json={"raw_user_input": "帮我查看当前磁盘使用情况"},
    ).json()

    assert isinstance(payload["intent"], dict)
    assert isinstance(payload["environment"], dict)
    assert isinstance(payload["risk"], dict)
    assert isinstance(payload["plan"], dict)
    assert isinstance(payload["execution"], dict)
    assert isinstance(payload["result"], dict)
    assert isinstance(payload["explanation"], str)


def test_static_page_resources_are_accessible() -> None:
    client = _client_with_executor(MockExecutor())

    index_response = client.get("/")
    js_response = client.get("/ui/app.js")
    css_response = client.get("/ui/style.css")

    assert index_response.status_code == 200
    assert "GuardedOps" in index_response.text
    assert 'id="operator-request"' in index_response.text
    assert 'id="submit-request"' in index_response.text
    assert 'id="operator-panel"' in index_response.text
    assert 'id="request-status"' in index_response.text
    assert 'id="risk-badge"' in index_response.text
    assert 'id="status-badge"' in index_response.text
    assert 'id="confirmation-panel"' in index_response.text
    assert js_response.status_code == 200
    assert "/api/chat" in js_response.text
    assert "operator_panel" in js_response.text
    assert "safe_alternative" in js_response.text
    assert css_response.status_code == 200
    assert ".operator-panel" in css_response.text
    assert ".status-strip" in css_response.text
    assert ".status-surface" in css_response.text
