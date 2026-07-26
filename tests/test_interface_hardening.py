from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.cli as cli
from app.api.chat import (
    SessionRegistry,
    _build_operator_panel_view,
    build_internal_error_envelope,
    get_executor,
    session_registry,
)
from app.main import create_app
from app.models import CommandResult


ROOT = Path(__file__).resolve().parents[1]
APP_JS_PATH = ROOT / "app" / "ui" / "app.js"
INDEX_PATH = ROOT / "app" / "ui" / "index.html"

NODE_HARNESS = """
function makeNode(doc, tag) {
  return {
    tagName: tag,
    className: "",
    textContent: "",
    hidden: false,
    disabled: false,
    value: "",
    focused: false,
    dataset: {},
    attributes: {},
    childNodes: [],
    listeners: {},
    appendChild: function (child) { this.childNodes.push(child); return child; },
    replaceChildren: function () {
      this.childNodes = Array.prototype.slice.call(arguments);
    },
    setAttribute: function (name, value) { this.attributes[name] = value; },
    getAttribute: function (name) { return this.attributes[name]; },
    addEventListener: function (type, handler) {
      this.listeners[type] = this.listeners[type] || [];
      this.listeners[type].push(handler);
    },
    focus: function () { this.focused = true; doc.activeElement = this; },
    querySelector: function () { return null; }
  };
}

function makeDoc(ids) {
  const doc = { nodes: {}, activeElement: null };
  doc.createElement = function (tag) { return makeNode(doc, tag); };
  doc.querySelector = function (selector) {
    if (selector.charAt(0) !== "#") { return null; }
    return doc.nodes[selector.slice(1)] || null;
  };
  ids.forEach(function (id) { doc.nodes[id] = makeNode(doc, id); });
  return doc;
}

function collectText(node) {
  let text = String(node.textContent || "");
  node.childNodes.forEach(function (child) { text += " " + collectText(child); });
  return text;
}

function makeStorage() {
  const values = {};
  return {
    getItem: function (key) { return Object.prototype.hasOwnProperty.call(values, key) ? values[key] : null; },
    setItem: function (key, value) { values[key] = String(value); },
    values: values
  };
}
"""


def _result(argv: list[str], *, exit_code: int = 0, stdout: str = "") -> CommandResult:
    return CommandResult(
        argv=argv,
        exit_code=exit_code,
        stdout=stdout,
        stderr="",
        success=exit_code == 0,
    )


class RecordingExecutor:
    """Executor stub that answers the probes used by the guarded read-only flow."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[list[str]] = []

    def run(self, argv: list[str], timeout: int = 10) -> CommandResult:
        del timeout
        self.calls.append(list(argv))
        if argv == ["hostname"]:
            return _result(argv, stdout=f"{self.name}\n")
        if argv == ["df", "-hT"]:
            return _result(
                argv,
                stdout="\n".join(
                    [
                        "Filesystem     Type  Size  Used Avail Use% Mounted on",
                        "/dev/sda1      ext4   50G   20G   28G  42% /",
                    ]
                ),
            )
        if argv == ["getent", "passwd", "demo_guest"]:
            return _result(
                argv,
                stdout="demo_guest:x:1001:1001::/home/demo_guest:/bin/bash\n",
            )
        return _result(argv, exit_code=127)


def _node(script: str, payload: dict[str, Any]) -> Any:
    completed = subprocess.run(
        ["node", "-e", NODE_HARNESS + script, str(APP_JS_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return json.loads(completed.stdout)


def _panel_element_ids() -> list[str]:
    html = INDEX_PATH.read_text(encoding="utf-8")
    return sorted(set(re.findall(r'id="([^"]+)"', html)))


def _success_envelope() -> dict[str, Any]:
    executor = RecordingExecutor("demo-host")
    app = create_app()
    app.dependency_overrides[get_executor] = lambda: executor
    client = TestClient(app)
    return client.post("/api/chat", json={"raw_user_input": "帮我查看当前磁盘使用情况"}).json()


def _refusal_envelope() -> dict[str, Any]:
    executor = RecordingExecutor("demo-host")
    app = create_app()
    app.dependency_overrides[get_executor] = lambda: executor
    client = TestClient(app)
    return client.post("/api/chat", json={"raw_user_input": "把 /etc 下面没用的配置删掉"}).json()


class BoomOrchestrator:
    def run(self, raw_user_input: str) -> dict[str, Any]:
        raise RuntimeError(f"boom: {raw_user_input}")


def test_pending_confirmation_never_leaks_into_another_session() -> None:
    app = create_app()
    app.dependency_overrides[get_executor] = lambda: RecordingExecutor("demo-host")
    operator_a = TestClient(app)
    operator_b = TestClient(app)

    pending = operator_a.post(
        "/api/chat",
        json={"raw_user_input": "请删除普通用户 demo_guest", "session_id": "operator-a"},
    ).json()
    assert pending["result"]["status"] == "pending_confirmation"
    assert pending["result"]["pending_action"]["tool_name"] == "delete_user_tool"

    other = operator_b.post(
        "/api/chat",
        json={"raw_user_input": "帮我查看当前磁盘使用情况", "session_id": "operator-b"},
    )
    other_payload = other.json()
    serialized = json.dumps(other_payload, ensure_ascii=False)

    assert other.status_code == 200
    assert other_payload["session_id"] == "operator-b"
    assert other_payload["result"]["status"] == "success"
    assert other_payload["result"].get("error") in (None, "")
    assert "pending_action" not in serialized
    assert "确认删除普通用户 demo_guest" not in serialized


def test_context_slots_do_not_bleed_between_sessions() -> None:
    app = create_app()
    app.dependency_overrides[get_executor] = lambda: RecordingExecutor("demo-host")
    client = TestClient(app)

    client.post(
        "/api/chat",
        json={"raw_user_input": "请删除普通用户 demo_guest", "session_id": "operator-a"},
    )
    other = client.post(
        "/api/chat",
        json={"raw_user_input": "查一下刚才那个用户", "session_id": "operator-b"},
    ).json()

    assert other["result"]["status"] == "refused"
    assert "demo_guest" not in json.dumps(other, ensure_ascii=False)


def test_missing_session_id_gets_its_own_cookie_backed_session() -> None:
    app = create_app()
    app.dependency_overrides[get_executor] = lambda: RecordingExecutor("demo-host")
    operator_a = TestClient(app)
    operator_b = TestClient(app)

    first = operator_a.post("/api/chat", json={"raw_user_input": "帮我查看当前磁盘使用情况"})
    second = operator_b.post("/api/chat", json={"raw_user_input": "帮我查看当前磁盘使用情况"})

    session_a = first.json()["session_id"]
    session_b = second.json()["session_id"]

    assert session_a and session_b
    assert session_a != session_b
    assert operator_a.cookies.get("guardedops_session") == session_a
    assert operator_a.post(
        "/api/chat", json={"raw_user_input": "帮我查看当前磁盘使用情况"}
    ).json()["session_id"] == session_a


def test_session_id_can_come_from_a_header() -> None:
    app = create_app()
    app.dependency_overrides[get_executor] = lambda: RecordingExecutor("demo-host")
    client = TestClient(app)

    payload = client.post(
        "/api/chat",
        json={"raw_user_input": "帮我查看当前磁盘使用情况"},
        headers={"X-GuardedOps-Session": "header-session"},
    ).json()

    assert payload["session_id"] == "header-session"


def test_requests_sharing_a_session_are_serialized() -> None:
    class SerializationProbe:
        def __init__(self) -> None:
            self.lock = threading.Lock()
            self.active = 0
            self.max_active = 0
            self.calls = 0

        def run(self, raw_user_input: str) -> dict[str, Any]:
            with self.lock:
                self.active += 1
                self.calls += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.05)
            with self.lock:
                self.active -= 1
            return {"result": {"status": "success", "data": None, "error": None},
                    "explanation": raw_user_input}

    probe = SerializationProbe()
    app = create_app()
    app.state.chat_orchestrator = probe
    client = TestClient(app)

    def post(index: int) -> None:
        client.post("/api/chat", json={"raw_user_input": f"帮我查看当前磁盘使用情况 {index}"})

    threads = [threading.Thread(target=post, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert probe.calls == 4
    assert probe.max_active == 1


def test_injected_executor_takes_effect_for_a_new_session() -> None:
    first_executor = RecordingExecutor("host-a")
    second_executor = RecordingExecutor("host-b")

    app = create_app()
    app.dependency_overrides[get_executor] = lambda: first_executor
    client = TestClient(app)
    client.post(
        "/api/chat",
        json={"raw_user_input": "帮我查看当前磁盘使用情况", "session_id": "session-1"},
    )

    app.dependency_overrides[get_executor] = lambda: second_executor
    payload = client.post(
        "/api/chat",
        json={"raw_user_input": "帮我查看当前磁盘使用情况", "session_id": "session-2"},
    ).json()

    assert second_executor.calls
    assert payload["environment"]["snapshot"]["hostname"] == "host-b"


def test_session_registry_evicts_by_ttl_and_capacity() -> None:
    now = {"value": 0.0}
    registry = SessionRegistry(
        ttl_seconds=10.0,
        max_sessions=2,
        clock=lambda: now["value"],
    )

    registry.acquire("a", lambda: object())
    registry.acquire("b", lambda: object())
    registry.acquire("c", lambda: object())
    assert registry.active_session_ids() == ["b", "c"]

    now["value"] = 100.0
    registry.acquire("d", lambda: object())
    assert registry.active_session_ids() == ["d"]


def test_session_registry_reuses_one_orchestrator_per_session() -> None:
    registry = SessionRegistry()
    created: list[object] = []

    def factory() -> object:
        instance = object()
        created.append(instance)
        return instance

    first = registry.acquire("same", factory)
    second = registry.acquire("same", factory)

    assert first is second
    assert len(created) == 1


def test_app_state_creates_a_bounded_session_registry() -> None:
    app = create_app()

    assert isinstance(session_registry(app), SessionRegistry)
    assert session_registry(app) is app.state.chat_sessions


def test_internal_error_returns_auditable_json_envelope() -> None:
    app = create_app()
    app.state.chat_orchestrator = BoomOrchestrator()
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/api/chat", json={"raw_user_input": "帮我查看当前磁盘使用情况"})
    payload = response.json()

    assert response.status_code == 500
    assert "application/json" in response.headers["content-type"].lower()
    correlation_id = response.headers["X-Correlation-Id"]
    assert payload["correlation_id"] == correlation_id
    assert payload["result"]["status"] == "failed"
    assert payload["operator_panel"]["status"] == "failed"
    assert payload["operator_panel"]["user_input"] == "帮我查看当前磁盘使用情况"
    assert len(payload["operator_panel"]["explanation_sections"]) == 8
    assert payload["operator_panel"]["recovery"]["available"] is True
    assert payload["operator_panel"]["recovery"]["can_retry_safely"] is False

    events = payload["evidence_chain"]["events"]
    assert [event["severity"] for event in events] == ["critical"]
    assert events[0]["details"]["correlation_id"] == correlation_id

    body = json.dumps(payload, ensure_ascii=False)
    assert "Traceback" not in body
    assert "RuntimeError" not in body
    assert "boom" not in body


def test_internal_error_envelope_hides_empty_preview_panels() -> None:
    envelope = build_internal_error_envelope(correlation_id="abc123", raw_user_input="x")
    panel = envelope["operator_panel"]

    assert panel["blast_radius_preview"]["summary"] == ""
    assert panel["blast_radius_preview"]["scenario"] == ""
    assert panel["policy_simulator"]["policy_version"] == ""
    assert panel["policy_simulator"]["scope_summary"] == ""
    assert panel["tool_calls"] == []

    view_model = _node(
        """
const fs = require("fs");
const panel = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(panel.createViewModel(input.payload, "x")));
""",
        {"payload": envelope},
    )
    assert view_model["blastRadius"]["visible"] is False
    assert view_model["policySimulator"]["visible"] is False


def test_oversized_input_is_rejected_before_the_orchestrator() -> None:
    executor = RecordingExecutor("demo-host")
    app = create_app()
    app.dependency_overrides[get_executor] = lambda: executor
    client = TestClient(app)

    response = client.post("/api/chat", json={"raw_user_input": "x" * 200000})
    detail = response.json()["detail"]

    assert response.status_code == 422
    assert len(response.content) < 4096
    assert "xxxx" not in response.text
    assert detail[0]["loc"] == ["body", "raw_user_input"]
    assert executor.calls == []


def test_whitespace_only_input_is_rejected_like_the_cli() -> None:
    app = create_app()
    app.dependency_overrides[get_executor] = lambda: RecordingExecutor("demo-host")
    client = TestClient(app)

    response = client.post("/api/chat", json={"raw_user_input": "   "})
    detail = response.json()["detail"]

    assert response.status_code == 422
    assert detail[0]["loc"] == ["body", "raw_user_input"]
    assert "空白字符" in detail[0]["msg"]


def test_validation_errors_render_as_a_readable_chinese_summary() -> None:
    app = create_app()
    app.dependency_overrides[get_executor] = lambda: RecordingExecutor("demo-host")
    client = TestClient(app)
    detail = client.post("/api/chat", json={"raw_user_input": "x" * 200000}).json()["detail"]

    summary = _node(
        """
const fs = require("fs");
const panel = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(panel.describeResponseFailure(422, { detail: input.detail })));
""",
        {"detail": detail},
    )

    assert summary.startswith("raw_user_input：")
    assert "[object Object]" not in summary


def test_large_responses_are_compressed() -> None:
    app = create_app()
    app.dependency_overrides[get_executor] = lambda: RecordingExecutor("demo-host")
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        json={"raw_user_input": "帮我查看当前磁盘使用情况"},
        headers={"Accept-Encoding": "gzip"},
    )

    assert response.status_code == 200
    assert response.headers.get("content-encoding") == "gzip"


def test_health_endpoint_reports_runtime_without_leaking_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-super-secret")
    client = TestClient(create_app())

    response = client.get("/health")
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["version"]
    assert len(payload["policy_version"]) == 64
    assert payload["llm_enable"] is False
    assert payload["dashscope_api_key_present"] is True
    assert "sk-super-secret" not in response.text


def test_openapi_documents_the_guarded_chat_contract() -> None:
    schema = TestClient(create_app()).get("/openapi.json").json()
    operation = schema["paths"]["/api/chat"]["post"]

    assert schema["info"]["description"]
    assert operation["summary"]
    assert "S3" in operation["description"]
    assert operation["tags"] == ["chat"]
    assert {"200", "422", "500"} <= set(operation["responses"])
    assert "/health" in schema["paths"]


def test_tool_call_layer_is_exposed_to_the_operator_panel() -> None:
    payload = _success_envelope()
    tool_calls = payload["operator_panel"]["tool_calls"]

    assert [item["tool_name"] for item in tool_calls] == ["env_probe_tool", "disk_usage_tool"]
    assert tool_calls[1]["status"] == "success"
    assert tool_calls[1]["command"] == "df -hT"
    assert tool_calls[1]["output_excerpt"]
    assert len(tool_calls[1]["output_excerpt"]) <= 401
    assert tool_calls[1]["evidence_refs"]


def test_ui_renders_tool_calls_and_keeps_them_text_only() -> None:
    payload = _success_envelope()
    rendered = _node(
        """
const fs = require("fs");
const panel = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const doc = makeDoc(input.ids);
panel.renderViewModel(doc, panel.createViewModel(input.payload, "帮我查看当前磁盘使用情况"));
process.stdout.write(JSON.stringify({
  hidden: doc.nodes["tool-call-panel"].hidden,
  summary: doc.nodes["tool-call-summary"].textContent,
  text: collectText(doc.nodes["tool-call-list"]),
  items: doc.nodes["tool-call-list"].childNodes.length
}));
""",
        {"payload": payload, "ids": _panel_element_ids()},
    )

    assert rendered["hidden"] is False
    assert rendered["items"] == 2
    assert "白名单工具" in rendered["summary"]
    assert "disk_usage_tool" in rendered["text"]
    assert "df -hT" in rendered["text"]


@pytest.mark.parametrize("case", ["success", "refusal", "partial", "internal_error"])
def test_render_path_never_throws_and_hides_empty_panels(case: str) -> None:
    if case == "success":
        payload: dict[str, Any] = _success_envelope()
    elif case == "refusal":
        payload = _refusal_envelope()
    elif case == "internal_error":
        payload = build_internal_error_envelope(correlation_id="deadbeef", raw_user_input="x")
    else:
        payload = {"operator_panel": {"user_input": "x", "status": "unknown"}}

    rendered = _node(
        """
const fs = require("fs");
const panel = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const doc = makeDoc(input.ids);
panel.renderViewModel(doc, panel.createViewModel(input.payload, "x"));
process.stdout.write(JSON.stringify({
  panel: doc.nodes["operator-panel"].hidden,
  blast: doc.nodes["blast-radius-panel"].hidden,
  policy: doc.nodes["policy-simulator-panel"].hidden,
  confirmation: doc.nodes["confirmation-panel"].hidden,
  refusal: doc.nodes["refusal-panel"].hidden,
  toolCalls: doc.nodes["tool-call-panel"].hidden,
  answer: doc.nodes["answer-summary-panel"].hidden
}));
""",
        {"payload": payload, "ids": _panel_element_ids()},
    )

    assert rendered["panel"] is False
    if case == "partial":
        assert rendered["blast"] is True
        assert rendered["policy"] is True
        assert rendered["toolCalls"] is True
        assert rendered["confirmation"] is True
        assert rendered["refusal"] is True
    if case == "internal_error":
        assert rendered["blast"] is True
        assert rendered["policy"] is True
        assert rendered["toolCalls"] is True
        assert rendered["answer"] is True
    if case == "refusal":
        assert rendered["refusal"] is False
        assert rendered["policy"] is False


def test_frontend_never_uses_innerhtml() -> None:
    source = APP_JS_PATH.read_text(encoding="utf-8")

    assert "innerHTML" not in source
    assert "outerHTML" not in source
    assert "insertAdjacentHTML" not in source


def test_frontend_sends_session_id_and_a_request_timeout() -> None:
    observed = _node(
        """
const fs = require("fs");
const panel = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const doc = makeDoc(input.ids);
const requests = [];
const scope = {
  fetch: function (url, init) {
    requests.push({ url: url, init: init });
    return Promise.resolve({
      ok: true,
      status: 200,
      json: function () { return Promise.resolve(input.payload); }
    });
  },
  AbortSignal: { timeout: function (ms) { return { timeoutMs: ms }; } },
  sessionStorage: makeStorage(),
  crypto: { randomUUID: function () { return "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"; } }
};

panel.boot(doc, scope);
doc.nodes["operator-request"].value = "帮我查看当前磁盘使用情况";
doc.nodes["operator-form"].listeners.submit[0]({ preventDefault: function () {} }).then(function () {
  process.stdout.write(JSON.stringify({
    url: requests[0].url,
    body: JSON.parse(requests[0].init.body),
    timeout: requests[0].init.signal.timeoutMs,
    status: doc.nodes["request-status"].textContent,
    disabled: doc.nodes["operator-request"].disabled,
    focused: doc.nodes["operator-request"].focused,
    ariaBusy: doc.nodes["operator-panel"].attributes["aria-busy"],
    stored: scope.sessionStorage.values
  }));
});
""",
        {"payload": _success_envelope(), "ids": _panel_element_ids()},
    )

    assert observed["url"] == "/api/chat"
    assert observed["body"]["session_id"] == "aaaaaaaabbbbccccddddeeeeeeeeeeee"
    assert observed["body"]["raw_user_input"] == "帮我查看当前磁盘使用情况"
    assert observed["timeout"] == 60000
    assert observed["status"] == "控制面已更新"
    assert observed["disabled"] is False
    assert observed["focused"] is True
    assert observed["ariaBusy"] == "false"
    assert observed["stored"]["guardedops.session_id"] == "aaaaaaaabbbbccccddddeeeeeeeeeeee"


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (500, "Internal Server Error", "服务端内部错误。"),
        (
            422,
            json.dumps(
                {
                    "detail": [
                        {
                            "type": "string_too_long",
                            "loc": ["body", "raw_user_input"],
                            "msg": "String should have at most 2000 characters",
                        }
                    ]
                }
            ),
            "raw_user_input：String should have at most 2000 characters",
        ),
        (404, "<html>not found</html>", "接口不存在，请确认服务已启动。"),
    ],
)
def test_http_failures_render_a_chinese_diagnostic(status: int, body: str, expected: str) -> None:
    observed = _node(
        """
const fs = require("fs");
const panel = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const doc = makeDoc(input.ids);
const scope = {
  fetch: function () {
    return Promise.resolve({
      ok: false,
      status: input.status,
      json: function () { return Promise.reject(new SyntaxError("Unexpected token")); },
      text: function () { return Promise.resolve(input.body); }
    });
  },
  AbortSignal: { timeout: function (ms) { return { timeoutMs: ms }; } },
  sessionStorage: makeStorage(),
  crypto: {}
};

panel.boot(doc, scope);
doc.nodes["operator-request"].value = "帮我查看当前磁盘使用情况";
doc.nodes["operator-form"].listeners.submit[0]({ preventDefault: function () {} }).then(function () {
  process.stdout.write(JSON.stringify({
    status: doc.nodes["request-status"].textContent,
    timeline: collectText(doc.nodes["timeline-list"]),
    residual: doc.nodes["residual-summary"].textContent,
    disabled: doc.nodes["operator-request"].disabled
  }));
});
""",
        {"status": status, "body": body, "ids": _panel_element_ids()},
    )

    assert observed["status"] == "请求失败"
    assert expected in observed["timeline"]
    assert expected in observed["residual"]
    assert "is not valid JSON" not in observed["timeline"]
    assert "Unexpected token" not in observed["timeline"]
    assert observed["disabled"] is False


def test_server_failure_envelope_is_rendered_as_a_full_control_panel() -> None:
    envelope = build_internal_error_envelope(
        correlation_id="feedface1234",
        raw_user_input="帮我查看当前磁盘使用情况",
        path="/api/chat",
    )
    observed = _node(
        """
const fs = require("fs");
const panel = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const doc = makeDoc(input.ids);
const scope = {
  fetch: function () {
    return Promise.resolve({
      ok: false,
      status: 500,
      json: function () { return Promise.reject(new Error("should not be called")); },
      text: function () { return Promise.resolve(JSON.stringify(input.payload)); }
    });
  },
  AbortSignal: { timeout: function (ms) { return { timeoutMs: ms }; } },
  sessionStorage: makeStorage(),
  crypto: {}
};

panel.boot(doc, scope);
doc.nodes["operator-request"].value = "帮我查看当前磁盘使用情况";
doc.nodes["operator-form"].listeners.submit[0]({ preventDefault: function () {} }).then(function () {
  process.stdout.write(JSON.stringify({
    status: doc.nodes["request-status"].textContent,
    badge: doc.nodes["status-badge"].textContent,
    recoveryHidden: doc.nodes["recovery-panel"].hidden,
    recovery: collectText(doc.nodes["recovery-steps"]),
    timeline: collectText(doc.nodes["timeline-list"])
  }));
});
""",
        {"payload": envelope, "ids": _panel_element_ids()},
    )

    assert observed["status"] == "服务端返回失败信封"
    assert observed["badge"] == "失败"
    assert observed["recoveryHidden"] is False
    assert "feedface1234" in observed["recovery"]
    assert "feedface1234" in observed["timeline"]


def test_request_timeout_is_reported_in_chinese() -> None:
    observed = _node(
        """
const fs = require("fs");
const panel = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const doc = makeDoc(input.ids);
const scope = {
  fetch: function () {
    const error = new Error("signal timed out");
    error.name = "TimeoutError";
    return Promise.reject(error);
  },
  AbortSignal: { timeout: function (ms) { return { timeoutMs: ms }; } },
  sessionStorage: makeStorage(),
  crypto: {}
};

panel.boot(doc, scope);
doc.nodes["operator-request"].value = "帮我查看当前磁盘使用情况";
doc.nodes["operator-form"].listeners.submit[0]({ preventDefault: function () {} }).then(function () {
  process.stdout.write(JSON.stringify({
    timeline: collectText(doc.nodes["timeline-list"]),
    disabled: doc.nodes["operator-request"].disabled,
    button: doc.nodes["submit-request"].disabled
  }));
});
""",
        {"ids": _panel_element_ids()},
    )

    assert "请求超时或被中止" in observed["timeline"]
    assert observed["disabled"] is False
    assert observed["button"] is False


def test_fallback_panel_matches_server_confirmation_ladder_on_mismatch() -> None:
    envelope = {
        "risk": {
            "risk_level": "S1",
            "requires_confirmation": True,
            "confirmation_text": "确认创建普通用户 demo_guest",
        },
        "plan": {"status": "pending_confirmation"},
        "execution": {"status": "skipped", "steps": [], "results": []},
        "result": {
            "status": "pending_confirmation",
            "error": "confirmation_text_mismatch",
            "confirmation_text": "确认创建普通用户 demo_guest",
        },
    }
    fallback = _node(
        """
const fs = require("fs");
const panel = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(panel.buildFallbackPanel(input.payload, "x")));
""",
        {"payload": envelope},
    )

    gate = [item for item in fallback["preflight_items"] if item["key"] == "confirmation_gate"][0]
    assert fallback["confirmation"]["status"] == "mismatch"
    assert gate["status"] == "blocked"


def _pending_envelope() -> dict[str, Any]:
    executor = RecordingExecutor("demo-host")
    app = create_app()
    app.dependency_overrides[get_executor] = lambda: executor
    client = TestClient(app)
    return client.post("/api/chat", json={"raw_user_input": "请创建普通用户 demo_guest"}).json()


def test_fallback_panel_agrees_with_the_server_panel() -> None:
    pending = _pending_envelope()

    mismatch = deepcopy(pending)
    mismatch["result"]["error"] = "confirmation_text_mismatch"

    cancelled = deepcopy(pending)
    cancelled["plan"]["status"] = "cancelled"
    cancelled["result"]["status"] = "cancelled"

    confirmed = deepcopy(pending)
    confirmed["plan"]["status"] = "confirmed"
    confirmed["result"]["status"] = "success"

    envelopes = {
        "success": _success_envelope(),
        "refusal": _refusal_envelope(),
        "pending": pending,
        "mismatch": mismatch,
        "cancelled": cancelled,
        "confirmed": confirmed,
    }

    for name, envelope in envelopes.items():
        source = {key: value for key, value in envelope.items() if key != "operator_panel"}
        server = _build_operator_panel_view(source, raw_user_input="x")
        fallback = _node(
            """
const fs = require("fs");
const panel = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(panel.buildFallbackPanel(input.payload, "x")));
""",
            {"payload": source},
        )

        assert fallback["status"] == server["status"], name
        assert fallback["risk_level"] == server["risk_level"], name
        assert fallback["confirmation"]["status"] == server["confirmation"]["status"], name
        assert fallback["confirmation"]["required"] == server["confirmation"]["required"], name
        assert fallback["refusal"]["is_refused"] == server["refusal"]["is_refused"], name
        assert [(item["key"], item["status"]) for item in fallback["preflight_items"]] == [
            (item["key"], item["status"]) for item in server["preflight_items"]
        ], name
        assert [item["key"] for item in fallback["explanation_sections"]] == [
            item["key"] for item in server["explanation_sections"]
        ], name


def test_cli_exit_codes_separate_refusal_from_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cases = {
        "success": cli.EXIT_SUCCESS,
        "cancelled": cli.EXIT_SUCCESS,
        "refused": cli.EXIT_REFUSED_BY_POLICY,
        "unsupported": cli.EXIT_REFUSED_BY_POLICY,
        "pending_confirmation": cli.EXIT_PENDING_CONFIRMATION,
        "failed": cli.EXIT_INTERNAL_FAILURE,
        "weird": cli.EXIT_INTERNAL_FAILURE,
    }

    for status, expected in cases.items():
        class FakeOrchestrator:
            def __init__(self, _executor: object) -> None:
                pass

            def run(self, _raw_user_input: str) -> dict[str, Any]:
                return {
                    "result": {"status": status, "data": None, "error": None},
                    "explanation": "测试摘要。",
                }

        monkeypatch.setattr(cli, "ReadonlyOrchestrator", FakeOrchestrator)
        assert cli.main(["帮我查看当前磁盘使用情况"]) == expected

    capsys.readouterr()


def test_cli_rejects_whitespace_only_request_with_usage_exit_code() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["   "])

    assert exc_info.value.code == cli.EXIT_USAGE


def test_cli_forces_utf8_on_redirected_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeStream:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        def reconfigure(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    stdout = FakeStream()
    stderr = FakeStream()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    cli._force_utf8_streams()

    assert stdout.kwargs == {"encoding": "utf-8", "errors": "replace"}
    assert stderr.kwargs == {"encoding": "utf-8", "errors": "replace"}


def test_cli_survives_a_non_cjk_console_codepage() -> None:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp1252"

    completed = subprocess.run(
        [sys.executable, "-m", "app.cli", "帮我创建一个用户 demo"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
    )

    assert completed.returncode == cli.EXIT_REFUSED_BY_POLICY
    assert b"UnicodeEncodeError" not in completed.stderr
    assert "当前只支持只读基础能力" in completed.stdout.decode("utf-8")
