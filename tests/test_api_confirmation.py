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
        self.user_lookup_count = 0

    def run(self, argv: list[str], timeout: int = 10) -> CommandResult:
        del timeout

        self.calls.append(argv)

        if argv == ["getent", "passwd", "demo_guest"]:
            self.user_lookup_count += 1
            if self.user_lookup_count == 1:
                return _result(argv, exit_code=2)
            return _result(
                argv,
                stdout="demo_guest:x:1001:1001::/home/demo_guest:/bin/bash\n",
            )

        if argv == [
            "bash",
            "scripts/guardedops_create_user.sh",
            "--create-home",
            "demo_guest",
        ]:
            return _result(argv)

        return _result(argv, exit_code=127, stderr="unexpected command")


def _client_with_executor(executor: MockExecutor) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_executor] = lambda: executor
    return TestClient(app)


def test_api_pending_confirmation() -> None:
    executor = MockExecutor()
    client = _client_with_executor(executor)

    response = client.post("/api/chat", json={"raw_user_input": "请创建普通用户 demo_guest"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"]["intent"] == "create_user"
    assert payload["risk"]["risk_level"] == "S1"
    assert payload["risk"]["requires_confirmation"] is True
    assert payload["plan"]["status"] == "pending_confirmation"
    assert payload["result"]["status"] == "pending_confirmation"
    assert payload["result"]["confirmation_text"] == "确认创建普通用户 demo_guest"
    assert payload["execution"]["status"] == "skipped"
    assert isinstance(payload["explanation"], str)
    assert executor.calls == []


def test_exact_confirmation_returns_success_result() -> None:
    executor = MockExecutor()
    client = _client_with_executor(executor)

    client.post("/api/chat", json={"raw_user_input": "请创建普通用户 demo_guest"})
    response = client.post(
        "/api/chat",
        json={"raw_user_input": "确认创建普通用户 demo_guest"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["risk"]["risk_level"] == "S1"
    assert payload["plan"]["status"] == "confirmed"
    assert payload["execution"]["status"] == "success"
    assert payload["result"]["status"] == "success"
    assert payload["result"]["tool_name"] == "create_user_tool"
    assert payload["result"]["data"]["status"] == "created"
    assert payload["result"]["data"]["username"] == "demo_guest"
    assert payload["explanation"]
    assert executor.calls == [
        ["getent", "passwd", "demo_guest"],
        ["bash", "scripts/guardedops_create_user.sh", "--create-home", "demo_guest"],
        ["getent", "passwd", "demo_guest"],
    ]


def test_api_refused_high_risk() -> None:
    executor = MockExecutor()
    client = _client_with_executor(executor)

    response = client.post("/api/chat", json={"raw_user_input": "把 /etc 下面没用的配置删掉"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["risk"]["risk_level"] == "S3"
    assert payload["risk"]["allow"] is False
    assert payload["risk"]["requires_confirmation"] is False
    assert payload["risk"]["reasons"]
    assert payload["risk"]["safe_alternative"]
    assert payload["plan"]["status"] == "refused"
    assert payload["execution"]["status"] == "skipped"
    assert payload["result"]["status"] == "refused"
    assert payload["result"]["error"]
    assert payload["explanation"]
    assert executor.calls == []


def test_static_page_is_accessible() -> None:
    client = _client_with_executor(MockExecutor())

    index_response = client.get("/")
    js_response = client.get("/ui/app.js")
    css_response = client.get("/ui/style.css")

    assert index_response.status_code == 200
    assert "风险预览与确认闭环" in index_response.text
    assert "confirmation-panel" in index_response.text
    assert js_response.status_code == 200
    assert "/api/chat" in js_response.text
    assert "safe_alternative" in js_response.text
    assert css_response.status_code == 200
    assert "pending_confirmation" in css_response.text
