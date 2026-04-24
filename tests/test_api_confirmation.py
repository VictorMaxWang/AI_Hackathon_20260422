from __future__ import annotations

import pytest
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


CREATE_USER_LOOKUP = ["getent", "passwd", "demo_guest"]
CREATE_USER_COMMAND = [
    "bash",
    "scripts/guardedops_create_user.sh",
    "--create-home",
    "demo_guest",
]


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
    pending_action = payload["result"]["pending_action"]
    assert pending_action["tool_name"] == "create_user_tool"
    assert pending_action["confirmation_text"] == "确认创建普通用户 demo_guest"
    assert pending_action["confirmation_token"]["risk_level"] == "S1"
    assert pending_action["confirmation_token"]["plan_hash"]
    assert pending_action["confirmation_token"]["target_fingerprint"]
    assert pending_action["confirmation_token"]["policy_version"]
    assert CREATE_USER_LOOKUP not in executor.calls
    assert CREATE_USER_COMMAND not in executor.calls


@pytest.mark.parametrize(
    "raw_user_input",
    [
        "创建一个普通用户 guardedops_demo，不要给 sudo 权限",
        "创建普通用户 guardedops_demo，不加入 sudo",
        "创建普通用户 guardedops_demo，不要管理员权限",
        "创建普通用户 guardedops_demo，无 sudo 权限",
        "创建普通用户 guardedops_demo，不给管理员权限",
    ],
)
def test_api_no_sudo_create_user_pending_confirmation(raw_user_input: str) -> None:
    executor = MockExecutor()
    client = _client_with_executor(executor)

    response = client.post("/api/chat", json={"raw_user_input": raw_user_input})

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"]["intent"] == "create_user"
    assert payload["intent"]["target"]["username"] == "guardedops_demo"
    assert payload["intent"]["constraints"]["groups"] == []
    assert payload["intent"]["constraints"]["no_sudo"] is True
    assert payload["risk"]["risk_level"] == "S1"
    assert payload["risk"]["requires_confirmation"] is True
    assert payload["plan"]["status"] == "pending_confirmation"
    assert payload["result"]["status"] == "pending_confirmation"
    assert "guardedops_demo" in payload["result"]["confirmation_text"]
    assert ["getent", "passwd", "guardedops_demo"] not in executor.calls
    assert not any(
        call[:2] == ["bash", "scripts/guardedops_create_user.sh"]
        for call in executor.calls
    )


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
    assert executor.calls[-3:] == [
        CREATE_USER_LOOKUP,
        CREATE_USER_COMMAND,
        CREATE_USER_LOOKUP,
    ]
    assert executor.calls.count(CREATE_USER_LOOKUP) == 2
    assert executor.calls.count(CREATE_USER_COMMAND) == 1


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
