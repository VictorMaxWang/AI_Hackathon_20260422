from __future__ import annotations

import json
import logging
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.cli as cli
import app.main as main_module
from app.api.chat import (
    SESSION_CAPACITY_MESSAGE,
    SessionCapacityExceeded,
    SessionRegistry,
    _build_operator_panel_view,
    get_executor,
    session_registry,
)
from app.main import create_app
from app.models import CommandResult
from app.tools.file_search import file_search_tool


ROOT = Path(__file__).resolve().parents[1]
APP_JS_PATH = ROOT / "app" / "ui" / "app.js"
INDEX_PATH = ROOT / "app" / "ui" / "index.html"

NO_PENDING_ACTION_ERROR = "no_pending_action_to_confirm"

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


def _command_result(argv: list[str], *, exit_code: int = 0, stdout: str = "") -> CommandResult:
    return CommandResult(
        argv=argv,
        exit_code=exit_code,
        stdout=stdout,
        stderr="",
        success=exit_code == 0,
    )


class ReadonlyExecutor:
    """Answers the probes of the guarded read-only flow and refuses everything else."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, argv: list[str], timeout: int = 10) -> CommandResult:
        del timeout
        self.calls.append(list(argv))
        if argv == ["hostname"]:
            return _command_result(argv, stdout="demo-host\n")
        if argv == ["df", "-hT"]:
            return _command_result(
                argv,
                stdout="\n".join(
                    [
                        "Filesystem     Type  Size  Used Avail Use% Mounted on",
                        "/dev/sda1      ext4   50G   20G   28G  42% /",
                    ]
                ),
            )
        return _command_result(argv, exit_code=127)


class ExplodingExecutor:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, argv: list[str], timeout: int = 10) -> CommandResult:
        del timeout
        self.calls.append(list(argv))
        raise AssertionError(f"executor must not be called: {argv!r}")


class FakeToken:
    def __init__(self, expired: bool) -> None:
        self.expired = expired

    def is_expired(self, now: Any = None) -> bool:
        del now
        return self.expired


class FakeAction:
    def __init__(self, *, token_expired: bool | None) -> None:
        self.confirmation_token = None if token_expired is None else FakeToken(token_expired)


class FakePending:
    """Minimal stand-in for the orchestrator surface the registry inspects."""

    def __init__(self, pending: bool, *, token_expired: bool | None = None) -> None:
        action = FakeAction(token_expired=token_expired) if pending else None
        self.memory = type("Memory", (), {"pending_action": action})()


def _client(*, max_sessions: int = 8) -> tuple[TestClient, ReadonlyExecutor, Any]:
    executor = ReadonlyExecutor()
    app = create_app()
    app.state.chat_sessions = SessionRegistry(max_sessions=max_sessions)
    app.dependency_overrides[get_executor] = lambda: executor
    return TestClient(app), executor, app


def test_capacity_pressure_walks_past_a_session_holding_a_pending_action() -> None:
    registry = SessionRegistry(max_sessions=3)

    registry.acquire("pending", lambda: FakePending(True))
    registry.acquire("idle-a", lambda: FakePending(False))
    registry.acquire("idle-b", lambda: FakePending(False))
    registry.acquire("newcomer", lambda: FakePending(False))

    assert registry.active_session_ids() == ["pending", "idle-b", "newcomer"]
    assert registry.pending_session_ids() == ["pending"]


def test_new_session_is_rejected_when_every_cached_session_is_pending() -> None:
    registry = SessionRegistry(max_sessions=2)
    registry.acquire("pending-a", lambda: FakePending(True))
    registry.acquire("pending-b", lambda: FakePending(True))

    with pytest.raises(SessionCapacityExceeded):
        registry.acquire("newcomer", lambda: FakePending(False))

    assert registry.active_session_ids() == ["pending-a", "pending-b"]


def test_a_pending_session_still_resolves_while_the_registry_is_full() -> None:
    registry = SessionRegistry(max_sessions=2)
    pending = registry.acquire("pending-a", lambda: FakePending(True))
    registry.acquire("pending-b", lambda: FakePending(True))

    assert registry.acquire("pending-a", lambda: FakePending(False)) is pending


def test_an_unconfirmable_pending_action_no_longer_holds_the_cache(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = SessionRegistry(max_sessions=2)
    registry.acquire("stale", lambda: FakePending(True, token_expired=True))
    registry.acquire("live", lambda: FakePending(True, token_expired=False))

    with caplog.at_level(logging.WARNING, logger="guardedops.api.sessions"):
        registry.acquire("newcomer", lambda: FakePending(False))

    assert registry.active_session_ids() == ["live", "newcomer"]
    assert any("stale" in record.getMessage() for record in caplog.records)


def test_a_live_confirmation_token_still_pins_the_session() -> None:
    registry = SessionRegistry(max_sessions=1)
    registry.acquire("live", lambda: FakePending(True, token_expired=False))

    with pytest.raises(SessionCapacityExceeded):
        registry.acquire("newcomer", lambda: FakePending(False))


def test_ttl_expiry_of_guarded_state_leaves_an_audit_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = {"value": 0.0}
    registry = SessionRegistry(ttl_seconds=10.0, max_sessions=4, clock=lambda: now["value"])
    registry.acquire("pending", lambda: FakePending(True))

    now["value"] = 100.0
    with caplog.at_level(logging.WARNING, logger="guardedops.api.sessions"):
        registry.acquire("other", lambda: FakePending(False))

    assert registry.active_session_ids() == ["other"]
    assert any(
        record.levelno == logging.WARNING and "pending" in record.getMessage()
        for record in caplog.records
    )


def test_a_flood_of_readonly_requests_cannot_void_a_pending_confirmation() -> None:
    client, _executor, app = _client(max_sessions=4)

    pending = client.post(
        "/api/chat",
        json={"raw_user_input": "请创建普通用户 alice", "session_id": "victim"},
    ).json()
    assert pending["result"]["status"] == "pending_confirmation"

    for index in range(32):
        flood = client.post(
            "/api/chat",
            json={"raw_user_input": "看一下磁盘使用情况", "session_id": f"atk{index:03d}"},
        )
        assert flood.status_code == 200

    assert "victim" in session_registry(app).active_session_ids()

    confirmed = client.post(
        "/api/chat",
        json={"raw_user_input": "确认创建普通用户 alice", "session_id": "victim"},
    ).json()

    assert confirmed["result"].get("error") != NO_PENDING_ACTION_ERROR
    assert confirmed["result"]["status"] != "pending_confirmation"


def test_saturated_confirmation_queue_rejects_new_work_instead_of_dropping_it() -> None:
    client, _executor, app = _client(max_sessions=1)

    pending = client.post(
        "/api/chat",
        json={"raw_user_input": "请创建普通用户 alice", "session_id": "victim"},
    ).json()
    assert pending["result"]["status"] == "pending_confirmation"

    rejected = client.post(
        "/api/chat",
        json={"raw_user_input": "看一下磁盘使用情况", "session_id": "newcomer"},
    )

    assert rejected.status_code == 503
    assert rejected.headers["retry-after"] == "30"
    assert rejected.json()["detail"] == SESSION_CAPACITY_MESSAGE
    assert session_registry(app).active_session_ids() == ["victim"]

    still_pending = client.post(
        "/api/chat",
        json={"raw_user_input": "确认创建普通用户 alice", "session_id": "victim"},
    ).json()
    assert still_pending["result"].get("error") != NO_PENDING_ACTION_ERROR


def test_capacity_refusal_never_reaches_any_executor() -> None:
    executor = ExplodingExecutor()
    app = create_app()
    app.state.chat_sessions = SessionRegistry(max_sessions=1)
    app.state.chat_sessions.acquire("pinned", lambda: FakePending(True))
    app.dependency_overrides[get_executor] = lambda: executor
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        json={"raw_user_input": "看一下磁盘使用情况", "session_id": "newcomer"},
    )

    assert response.status_code == 503
    assert executor.calls == []


def test_browser_session_rides_an_httponly_cookie_that_is_refreshed() -> None:
    client, _executor, _app = _client()

    first = client.post("/api/chat", json={"raw_user_input": "帮我查看当前磁盘使用情况"})
    issued = first.cookies.get("guardedops_session")
    assert issued
    assert "httponly" in first.headers["set-cookie"].lower()

    second = client.post("/api/chat", json={"raw_user_input": "帮我查看当前磁盘使用情况"})

    assert second.json()["session_id"] == issued
    assert "guardedops_session" in second.headers.get("set-cookie", "")


def test_body_session_id_does_not_overwrite_the_browser_cookie() -> None:
    client, _executor, _app = _client()

    client.post("/api/chat", json={"raw_user_input": "帮我查看当前磁盘使用情况"})
    issued = client.cookies.get("guardedops_session")

    scripted = client.post(
        "/api/chat",
        json={"raw_user_input": "帮我查看当前磁盘使用情况", "session_id": "cli-script"},
    )

    assert scripted.json()["session_id"] == "cli-script"
    assert "set-cookie" not in scripted.headers
    assert client.cookies.get("guardedops_session") == issued


def test_openapi_states_the_session_trust_model_without_overclaiming() -> None:
    schema = TestClient(create_app()).get("/openapi.json").json()
    description = schema["paths"]["/api/chat"]["post"]["description"]

    assert "未经认证的持有者标识" in description
    assert "单操作员" in description
    assert "503" in schema["paths"]["/api/chat"]["post"]["responses"]


def test_frontend_bundle_keeps_no_session_identifier_of_its_own() -> None:
    source = APP_JS_PATH.read_text(encoding="utf-8")

    assert "sessionStorage" not in source
    assert "localStorage" not in source
    assert "session_id" not in source
    assert "innerHTML" not in source


def test_incomplete_is_not_reported_as_an_internal_failure() -> None:
    assert cli.EXIT_GUARDED_STEP_NOT_RUN not in {
        cli.EXIT_SUCCESS,
        cli.EXIT_INTERNAL_FAILURE,
        cli.EXIT_USAGE,
        cli.EXIT_REFUSED_BY_POLICY,
        cli.EXIT_PENDING_CONFIRMATION,
    }
    assert cli.exit_code_for({"result": {"status": "incomplete"}}) == cli.EXIT_GUARDED_STEP_NOT_RUN
    assert cli.exit_code_for({"result": {"status": "skipped"}}) == cli.EXIT_GUARDED_STEP_NOT_RUN
    assert cli.exit_code_for({"result": {"status": "failed"}}) == cli.EXIT_INTERNAL_FAILURE
    assert "5 " in cli.EXIT_CODE_EPILOG


def test_cli_reports_incomplete_with_its_own_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class IncompleteOrchestrator:
        def __init__(self, _executor: object) -> None:
            pass

        def run(self, _raw_user_input: str) -> dict[str, Any]:
            return {
                "result": {"status": "incomplete", "data": None, "error": None},
                "explanation": "连续任务未全部完成：受保护的写步骤没有执行。",
            }

    monkeypatch.setattr(cli, "ReadonlyOrchestrator", IncompleteOrchestrator)

    exit_code = cli.main(["先查磁盘再创建用户 demo_guest"])
    captured = capsys.readouterr()

    assert exit_code == cli.EXIT_GUARDED_STEP_NOT_RUN
    assert "受保护的写步骤没有执行" in captured.out
    assert captured.err == ""


def test_cli_json_output_is_strict_rfc_8259(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class NonFiniteOrchestrator:
        def __init__(self, _executor: object) -> None:
            pass

        def run(self, _raw_user_input: str) -> dict[str, Any]:
            return {
                "result": {
                    "status": "success",
                    "data": {
                        "use_percent": float("nan"),
                        "read_bytes_per_second": float("inf"),
                        "drift": float("-inf"),
                        "healthy": [1.5, float("nan")],
                    },
                    "error": None,
                },
                "explanation": "已完成只读查询。",
            }

    monkeypatch.setattr(cli, "ReadonlyOrchestrator", NonFiniteOrchestrator)

    exit_code = cli.main(["--json", "帮我查看当前磁盘使用情况"])
    captured = capsys.readouterr()

    assert exit_code == cli.EXIT_SUCCESS
    assert "NaN," not in captured.out
    assert "Infinity," not in captured.out

    payload = json.loads(captured.out, parse_constant=_reject_json_constant)
    data = payload["result"]["data"]
    assert data["use_percent"] == "NaN"
    assert data["read_bytes_per_second"] == "Infinity"
    assert data["drift"] == "-Infinity"
    assert data["healthy"] == [1.5, "NaN"]


def _reject_json_constant(name: str) -> Any:
    raise AssertionError(f"non-RFC-8259 constant in --json output: {name}")


def test_dump_json_keeps_finite_numbers_untouched() -> None:
    payload = {"a": 1, "b": 2.5, "c": [None, True, "x"]}

    assert json.loads(cli.dump_json(payload)) == payload
    assert math.isfinite(json.loads(cli.dump_json({"n": 3.25}))["n"])


def test_api_stays_up_when_the_ui_package_data_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "no-ui"
    monkeypatch.setattr(main_module, "UI_DIR", missing)
    monkeypatch.setattr(main_module, "UI_INDEX_FILE", missing / "index.html")

    app = create_app()
    app.dependency_overrides[get_executor] = lambda: ReadonlyExecutor()
    client = TestClient(app)

    health = client.get("/health")
    chat = client.post("/api/chat", json={"raw_user_input": "帮我查看当前磁盘使用情况"})
    index = client.get("/")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert chat.status_code == 200
    assert chat.json()["result"]["status"] == "success"
    assert index.status_code == 503
    assert "API 仍然可用" in index.json()["detail"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_depth": float("inf")},
        {"max_depth": float("-inf")},
        {"max_depth": float("nan")},
        {"max_results": float("inf")},
        {"max_results": float("-inf")},
        {"max_results": float("nan")},
        {"modified_within_days": float("inf")},
        {"modified_within_days": float("-inf")},
        {"modified_within_days": float("nan")},
    ],
)
def test_file_search_refuses_non_finite_limits_without_raising(kwargs: dict[str, float]) -> None:
    executor = ExplodingExecutor()

    result = file_search_tool(executor, "/var/log", **kwargs)

    assert result.success is False
    assert result.data["status"] == "refused"
    assert "finite" in result.error
    assert "OverflowError" not in result.error
    assert executor.calls == []


def test_file_search_still_clamps_huge_but_finite_limits() -> None:
    executor = ReadonlyExecutor()

    file_search_tool(executor, "/var/log", max_depth=10**400, max_results=10**400)

    assert executor.calls
    argv = executor.calls[-1]
    assert argv[0] == "find"
    assert argv[argv.index("-maxdepth") + 1] == "8"


def test_file_search_json_boundary_floats_stay_refusals() -> None:
    executor = ExplodingExecutor()
    limits = json.loads('{"max_depth": 1e999, "max_results": -1e999}')

    result = file_search_tool(executor, "/var/log", **limits)

    assert result.success is False
    assert result.data["status"] == "refused"
    assert executor.calls == []


def _partial_file_search_envelope() -> dict[str, Any]:
    return {
        "intent": {"intent": "search_file", "raw_user_input": "在 /var/log 里找 nginx 日志"},
        "risk": {"risk_level": "S0", "allow": True, "requires_confirmation": False, "reasons": []},
        "plan": {"status": "ready", "steps": []},
        "execution": {
            "status": "success",
            "steps": [
                {
                    "tool_name": "file_search_tool",
                    "args": {"base_path": "/var/log", "max_results": 20},
                }
            ],
            "results": [
                {
                    "tool_name": "file_search_tool",
                    "success": True,
                    "data": {
                        "status": "ok",
                        "source": "find /var/log -maxdepth 4 -type f",
                        "base_path": "/var/log",
                        "count": 2,
                        "truncated": False,
                        "partial": True,
                        "warnings": [
                            "find: '/var/log/private': Permission denied",
                            "find: '/var/log/audit': Permission denied",
                        ],
                        "results": [],
                    },
                }
            ],
        },
        "result": {"status": "success", "data": None, "error": None},
        "explanation_card": {},
        "evidence_chain": {"events": []},
    }


def test_partial_file_search_is_visible_in_the_tool_call_layer() -> None:
    panel = _build_operator_panel_view(
        _partial_file_search_envelope(),
        raw_user_input="在 /var/log 里找 nginx 日志",
    )
    call = panel["tool_calls"][0]

    assert call["partial"] is True
    assert len(call["warnings"]) == 2
    assert "Permission denied" in call["warnings"][0]


def test_tool_call_warnings_are_bounded() -> None:
    envelope = _partial_file_search_envelope()
    envelope["execution"]["results"][0]["data"]["warnings"] = [f"warn-{index}" for index in range(40)]
    envelope["execution"]["results"][0]["data"]["warnings"].append("x" * 5000)

    call = _build_operator_panel_view(envelope, raw_user_input="x")["tool_calls"][0]

    assert len(call["warnings"]) == 5
    assert all(len(item) <= 201 for item in call["warnings"])


def test_ui_renders_partial_file_search_warnings_as_text() -> None:
    envelope = _partial_file_search_envelope()
    envelope["operator_panel"] = _build_operator_panel_view(envelope, raw_user_input="x")

    rendered = _node(
        """
const fs = require("fs");
const panel = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const doc = makeDoc(input.ids);
panel.renderViewModel(doc, panel.createViewModel(input.payload, "x"));
process.stdout.write(JSON.stringify({
  hidden: doc.nodes["tool-call-panel"].hidden,
  summary: doc.nodes["tool-call-summary"].textContent,
  text: collectText(doc.nodes["tool-call-list"])
}));
""",
        {"payload": envelope, "ids": _panel_element_ids()},
    )

    assert rendered["hidden"] is False
    assert "不完整结果" in rendered["summary"]
    assert "部分结果" in rendered["text"]
    assert "Permission denied" in rendered["text"]


def test_ui_renders_incomplete_as_a_chinese_pending_outcome() -> None:
    envelope = {
        "intent": {"intent": "continuous_task", "raw_user_input": "x"},
        "risk": {"risk_level": "S1", "allow": True, "requires_confirmation": False, "reasons": []},
        "plan": {"status": "ready", "steps": []},
        "execution": {"status": "incomplete", "steps": [], "results": []},
        "result": {"status": "incomplete", "data": None, "error": None},
        "explanation_card": {
            "result_assertion": {
                "summary": "最终结果为 incomplete：受保护的写步骤没有执行。",
                "evidence_refs": [],
            }
        },
        "evidence_chain": {"events": []},
        "timeline": [
            {"intent": "query_disk_usage", "status": "success", "result_summary": "已完成"},
            {"intent": "create_user", "status": "skipped", "result_summary": "受保护的写步骤没有执行"},
        ],
    }
    envelope["operator_panel"] = _build_operator_panel_view(envelope, raw_user_input="x")

    rendered = _node(
        """
const fs = require("fs");
const panel = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const doc = makeDoc(input.ids);
panel.renderViewModel(doc, panel.createViewModel(input.payload, "x"));
process.stdout.write(JSON.stringify({
  badge: doc.nodes["status-badge"].textContent,
  tone: doc.nodes["status-badge"].dataset.tone,
  explanation: collectText(doc.nodes["explanation-list"]),
  timeline: collectText(doc.nodes["timeline-list"])
}));
""",
        {"payload": envelope, "ids": _panel_element_ids()},
    )

    assert rendered["badge"] == "未完成"
    assert rendered["tone"] == "pending"
    assert "incomplete" not in rendered["explanation"]
    assert "未完成" in rendered["explanation"]
    assert "已跳过" in rendered["timeline"]


def test_empty_preview_panels_stay_hidden_end_to_end() -> None:
    client, _executor, _app = _client()
    payload = client.post(
        "/api/chat",
        json={"raw_user_input": "帮我查看当前磁盘使用情况"},
    ).json()

    rendered = _node(
        """
const fs = require("fs");
const panel = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const doc = makeDoc(input.ids);
panel.renderViewModel(doc, panel.createViewModel(input.payload, "帮我查看当前磁盘使用情况"));
const stripped = JSON.parse(JSON.stringify(input.payload));
delete stripped.operator_panel;
stripped.blast_radius_preview = {};
stripped.policy_simulator = {};
const bare = makeDoc(input.ids);
panel.renderViewModel(bare, panel.createViewModel(stripped, "x"));
process.stdout.write(JSON.stringify({
  fullBlast: doc.nodes["blast-radius-panel"].hidden,
  fullPolicy: doc.nodes["policy-simulator-panel"].hidden,
  fullTools: doc.nodes["tool-call-panel"].hidden,
  bareBlast: bare.nodes["blast-radius-panel"].hidden,
  barePolicy: bare.nodes["policy-simulator-panel"].hidden
}));
""",
        {"payload": payload, "ids": _panel_element_ids()},
    )

    assert rendered["fullBlast"] is False
    assert rendered["fullPolicy"] is False
    assert rendered["fullTools"] is False
    assert rendered["bareBlast"] is True
    assert rendered["barePolicy"] is True


def test_correlation_id_reaches_the_status_strip_on_the_500_path() -> None:
    class BoomOrchestrator:
        def run(self, raw_user_input: str) -> dict[str, Any]:
            raise RuntimeError(f"boom: {raw_user_input}")

    app = create_app()
    app.state.chat_orchestrator = BoomOrchestrator()
    server = TestClient(app, raise_server_exceptions=False)
    response = server.post("/api/chat", json={"raw_user_input": "帮我查看当前磁盘使用情况"})
    correlation_id = response.headers["X-Correlation-Id"]

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
      json: function () { return Promise.reject(new Error("unused")); },
      text: function () { return Promise.resolve(JSON.stringify(input.payload)); }
    });
  },
  AbortSignal: { timeout: function (ms) { return { timeoutMs: ms }; } },
  crypto: {}
};

panel.boot(doc, scope);
doc.nodes["operator-request"].value = "帮我查看当前磁盘使用情况";
doc.nodes["operator-form"].listeners.submit[0]({ preventDefault: function () {} }).then(function () {
  process.stdout.write(JSON.stringify({
    status: doc.nodes["request-status"].textContent,
    recovery: collectText(doc.nodes["recovery-steps"])
  }));
});
""",
        {"payload": response.json(), "ids": _panel_element_ids()},
    )

    assert correlation_id in observed["status"]
    assert correlation_id in observed["recovery"]
    assert "RuntimeError" not in observed["status"]


def test_service_unavailable_body_renders_as_a_chinese_diagnostic() -> None:
    summary = _node(
        """
const fs = require("fs");
const panel = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(panel.describeResponseFailure(503, input.payload)));
""",
        {"payload": {"detail": SESSION_CAPACITY_MESSAGE}},
    )

    assert summary == SESSION_CAPACITY_MESSAGE
