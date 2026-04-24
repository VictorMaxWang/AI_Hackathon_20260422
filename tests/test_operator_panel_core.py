from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import create_app


ROOT = Path(__file__).resolve().parents[1]
APP_JS_PATH = ROOT / "app" / "ui" / "app.js"
INDEX_PATH = ROOT / "app" / "ui" / "index.html"

SECTION_KEYS = [
    "intent_normalized",
    "plan_summary",
    "risk_hits",
    "scope_preview",
    "confirmation_basis",
    "execution_evidence",
    "result_assertion",
    "residual_risks_or_next_step",
]


class StubOrchestrator:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def run(self, raw_user_input: str) -> dict[str, Any]:
        self.calls.append(raw_user_input)
        return deepcopy(self.payload)


def _client_with_payload(payload: dict[str, Any]) -> TestClient:
    app = create_app()
    app.state.chat_orchestrator = StubOrchestrator(payload)
    return TestClient(app)


def _view_model(payload: dict[str, Any], *, raw_user_input: str = "") -> dict[str, Any]:
    script = """
const fs = require("fs");
const panel = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const viewModel = panel.createViewModel(input.payload, input.rawUserInput || "");
process.stdout.write(JSON.stringify(viewModel));
"""
    completed = subprocess.run(
        ["node", "-e", script, str(APP_JS_PATH)],
        input=json.dumps({"payload": payload, "rawUserInput": raw_user_input}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return json.loads(completed.stdout)


def _explanation_card() -> dict[str, Any]:
    labels = {
        "intent_normalized": "请求被归一化为受控 intent。",
        "plan_summary": "计划保持在 orchestrator 边界内。",
        "risk_hits": "风险命中来自现有 policy 决策。",
        "scope_preview": "范围被限制在单次请求上下文中。",
        "confirmation_basis": "确认门状态来自已有 confirmation evidence。",
        "execution_evidence": "执行证据来自 trace 与 state assertions。",
        "result_assertion": "最终结果与 evidence chain 对齐。",
        "residual_risks_or_next_step": "保留残余风险与下一步建议。",
    }
    return {
        key: {"summary": summary, "evidence_refs": [f"ev-{index:03d}"]}
        for index, (key, summary) in enumerate(labels.items(), start=1)
    }


def _evidence_chain(*, confirmation_status: str = "not_required") -> dict[str, Any]:
    return {
        "events": [
            {
                "event_id": "ev-001",
                "stage": "parse",
                "title": "intent_parsed",
                "details": {"intent": "query_disk_usage"},
                "severity": "info",
                "refs": ["intent:query_disk_usage"],
                "timestamp": "2026-04-23T09:00:00Z",
            },
            {
                "event_id": "ev-002",
                "stage": "plan",
                "title": "plan_evaluated",
                "details": {"status": "ready"},
                "severity": "info",
                "refs": ["ev-001"],
                "timestamp": "2026-04-23T09:00:01Z",
            },
            {
                "event_id": "ev-003",
                "stage": "policy",
                "title": "policy_decision",
                "details": {"risk_level": "S0", "allow": True},
                "severity": "info",
                "refs": ["ev-001", "ev-002"],
                "timestamp": "2026-04-23T09:00:02Z",
            },
            {
                "event_id": "ev-004",
                "stage": "confirmation",
                "title": "confirmation_state",
                "details": {"status": confirmation_status},
                "severity": "warning" if confirmation_status != "not_required" else "info",
                "refs": ["ev-003"],
                "timestamp": "2026-04-23T09:00:03Z",
            },
            {
                "event_id": "ev-005",
                "stage": "result",
                "title": "final_result",
                "details": {"status": "success"},
                "severity": "info",
                "refs": ["ev-003", "ev-004"],
                "timestamp": "2026-04-23T09:00:04Z",
            },
        ],
        "state_assertions": [
            {
                "assertion_id": "as-001",
                "name": "final_outcome",
                "passed": True,
                "evidence_refs": ["ev-005"],
                "summary": "最终结果为 success。",
            }
        ],
    }


def _success_payload() -> dict[str, Any]:
    disk_data = {
        "status": "ok",
        "count": 3,
        "filesystems": [
            {
                "filesystem": "/dev/sda1",
                "type": "ext4",
                "size": "340G",
                "used": "268G",
                "available": "72G",
                "use_percent": "79%",
                "mounted_on": "/",
            },
            {
                "filesystem": "tmpfs",
                "type": "tmpfs",
                "size": "16G",
                "used": "1G",
                "available": "15G",
                "use_percent": "7%",
                "mounted_on": "/run",
            },
            {
                "filesystem": "/dev/sdb1",
                "type": "ext4",
                "size": "1T",
                "used": "200G",
                "available": "824G",
                "use_percent": "20%",
                "mounted_on": "/data",
            },
        ],
    }
    return {
        "intent": {
            "intent": "query_disk_usage",
            "raw_user_input": "帮我看看当前磁盘使用情况",
            "confidence": 0.82,
            "confidence_source": "parser_stub",
        },
        "environment": {
            "status": "ok",
            "snapshot": {"hostname": "demo-host", "connection_mode": "local"},
        },
        "risk": {
            "risk_level": "S0",
            "allow": True,
            "requires_confirmation": False,
            "reasons": ["只读诊断请求"],
            "safe_alternative": "",
        },
        "plan": {
            "status": "ready",
            "reason": "bounded readonly plan",
            "steps": [{"tool_name": "disk_usage_tool", "args": {}}],
        },
        "execution": {
            "status": "success",
            "steps": [{"tool_name": "disk_usage_tool", "args": {}}],
            "results": [{"tool_name": "disk_usage_tool", "success": True, "data": disk_data}],
        },
        "result": {
            "status": "success",
            "tool_name": "disk_usage_tool",
            "data": disk_data,
            "error": None,
        },
        "recovery": None,
        "explanation": "当前共检测到 3 个挂载点；最紧张的是 /，使用率 79%，可用空间 72G。",
        "evidence_chain": _evidence_chain(),
        "explanation_card": _explanation_card(),
    }


def _memory_payload() -> dict[str, Any]:
    payload = _success_payload()
    memory_data = {
        "status": "ok",
        "total_bytes": 16 * 1024 * 1024 * 1024,
        "used_bytes": 10 * 1024 * 1024 * 1024,
        "available_bytes": 6 * 1024 * 1024 * 1024,
        "used_percent": 62.5,
        "source": "/proc/meminfo",
        "top_processes": [
            {
                "pid": 123,
                "user": "root",
                "memory_percent": 11.5,
                "memory_bytes": 1024 * 1024 * 1024,
                "command": "postgres",
                "args": "postgres: writer",
            }
        ],
        "process_source": "ps",
        "process_error": "",
    }
    payload["intent"].update(
        {
            "intent": "query_memory_usage",
            "raw_user_input": "帮我查看当前内存使用情况",
        }
    )
    payload["plan"]["steps"] = [{"tool_name": "memory_usage_tool", "args": {"limit": 10}}]
    payload["execution"]["steps"] = [{"tool_name": "memory_usage_tool", "args": {"limit": 10}}]
    payload["execution"]["results"] = [
        {"tool_name": "memory_usage_tool", "success": True, "data": memory_data}
    ]
    payload["result"] = {
        "status": "success",
        "tool_name": "memory_usage_tool",
        "data": memory_data,
        "error": None,
    }
    payload["explanation"] = (
        "当前内存总量 16.0 GB，已用 10.0 GB（62.5%），可用 6.0 GB；"
        "内存占用最高的进程是 postgres（PID 123，占用 1.0 GB）。"
    )
    return payload


def _pending_payload() -> dict[str, Any]:
    payload = _success_payload()
    payload["intent"].update(
        {
            "intent": "create_user",
            "raw_user_input": "请创建普通用户 demo_guest",
            "confidence": 0.91,
        }
    )
    payload["risk"] = {
        "risk_level": "S1",
        "allow": True,
        "requires_confirmation": True,
        "confirmation_text": "确认创建普通用户 demo_guest",
        "reasons": ["写操作需要确认"],
        "safe_alternative": "先检查当前用户列表。",
    }
    payload["plan"] = {
        "status": "pending_confirmation",
        "reason": "awaiting exact confirmation",
        "steps": [{"tool_name": "create_user_tool", "args": {"username": "demo_guest"}}],
    }
    payload["execution"] = {"status": "skipped", "steps": [], "results": []}
    payload["result"] = {
        "status": "pending_confirmation",
        "data": None,
        "error": None,
        "confirmation_text": "确认创建普通用户 demo_guest",
    }
    payload["explanation_card"]["confirmation_basis"]["summary"] = "待用户提供精确确认文本。"
    payload["evidence_chain"] = _evidence_chain(confirmation_status="pending")
    return payload


def _refused_payload() -> dict[str, Any]:
    payload = _success_payload()
    payload["intent"].update(
        {
            "intent": "delete_user",
            "raw_user_input": "请删除普通用户 demo_guest",
            "confidence": 0.88,
        }
    )
    payload["risk"] = {
        "risk_level": "S3",
        "allow": False,
        "requires_confirmation": False,
        "reasons": ["当前示例将请求视为禁止执行"],
        "safe_alternative": "先做只读核查，再提交更窄范围的请求。",
    }
    payload["plan"] = {
        "status": "refused",
        "reason": "policy denied this request",
        "steps": [],
    }
    payload["execution"] = {"status": "skipped", "steps": [], "results": []}
    payload["result"] = {
        "status": "refused",
        "data": None,
        "error": "policy denied this request",
    }
    payload["recovery"] = {
        "failure_type": "unsupported_request",
        "why_it_failed": "The request stayed outside the guarded policy boundary.",
        "safe_next_steps": [
            "Rephrase the request as a bounded read-only diagnostic.",
            "Submit a fresh guarded request after narrowing the target.",
        ],
        "suggested_readonly_diagnostics": [
            "Inspect the latest evidence and risk reasons.",
            "Confirm the target still exists before trying again.",
        ],
        "requires_confirmation_for_recovery": False,
        "can_retry_safely": False,
    }
    payload["explanation_card"]["residual_risks_or_next_step"]["summary"] = (
        "Recovery guidance has been attached for the next bounded request."
    )
    return payload


def _unsupported_payload() -> dict[str, Any]:
    payload = _success_payload()
    payload["intent"].update(
        {
            "intent": "unknown",
            "raw_user_input": "请帮我安装 nginx",
            "confidence": 0.2,
        }
    )
    payload["risk"] = {
        "risk_level": "S0",
        "allow": False,
        "requires_confirmation": False,
        "reasons": ["unsupported read-only operation"],
        "safe_alternative": "",
    }
    payload["plan"] = {
        "status": "unsupported",
        "reason": "当前只支持只读基础能力",
        "steps": [],
    }
    payload["execution"] = {"status": "skipped", "steps": [], "results": []}
    payload["result"] = {
        "status": "unsupported",
        "data": None,
        "error": "当前只支持只读基础能力，未执行任何命令。",
    }
    payload["recovery"] = {
        "failure_type": "unsupported_request",
        "why_it_failed": (
            "The request could not be mapped to a supported guarded workflow "
            "with the current planner and tool boundary."
        ),
        "safe_next_steps": [
            "Rephrase the request as a bounded read-only diagnostic.",
            "Submit a fresh guarded request after narrowing the target.",
        ],
        "suggested_readonly_diagnostics": [
            "Review the parsed intent and plan status to see which part of the request remained unsupported."
        ],
        "requires_confirmation_for_recovery": False,
        "can_retry_safely": False,
    }
    payload["explanation_card"]["confirmation_basis"]["summary"] = "当前请求无确认依据。"
    payload["explanation_card"]["residual_risks_or_next_step"]["summary"] = (
        "Recovery guidance has been attached for the next bounded request."
    )
    return payload


def _timeline_payload() -> dict[str, Any]:
    payload = _success_payload()
    payload["timeline"] = [
        {
            "step_id": "step-1",
            "intent": "create_user",
            "status": "success",
            "risk": "S1",
            "result_summary": "普通用户 demo_guest 已创建。",
            "timestamp": "2026-04-23T09:00:05Z",
            "refs": ["ev-003", "ev-005"],
        },
        {
            "step_id": "step-2",
            "intent": "verify_user_exists",
            "status": "success",
            "risk": "S0",
            "result_summary": "后置校验确认用户存在。",
            "timestamp": "2026-04-23T09:00:06Z",
            "refs": ["ev-005"],
        },
    ]
    payload["recovery"] = {
        "failure_type": "partial_success",
        "why_it_failed": "Earlier steps completed; inspect the latest state before follow-up.",
        "safe_next_steps": ["Review the evidence timeline before any follow-up action."],
        "suggested_readonly_diagnostics": ["Use a bounded lookup to confirm current target state."],
        "requires_confirmation_for_recovery": True,
        "can_retry_safely": False,
    }
    return payload


def test_api_chat_returns_explanation_card_and_operator_panel_projection() -> None:
    client = _client_with_payload(_success_payload())

    response = client.post("/api/chat", json={"raw_user_input": "帮我看看当前磁盘使用情况"})

    assert response.status_code == 200
    payload = response.json()
    assert "explanation_card" in payload
    assert "operator_panel" in payload

    operator_panel = payload["operator_panel"]
    assert [item["key"] for item in operator_panel["explanation_sections"]] == SECTION_KEYS
    assert operator_panel["confidence"] == 0.82
    assert operator_panel["confidence_source"] == "parser_stub"
    assert {item["key"] for item in operator_panel["preflight_items"]} == {
        "intent_parsed",
        "policy_bound",
        "plan_ready",
        "confirmation_gate",
        "environment_ready",
    }
    assert operator_panel["timeline_entries"]
    assert operator_panel["timeline_entries"][0]["source"] == "evidence"

    view_model = _view_model(payload, raw_user_input="帮我看看当前磁盘使用情况")
    assert view_model["answerSummary"]["visible"] is True
    assert "当前共检测到" in view_model["answerSummary"]["text"]
    assert "可用空间" in view_model["answerSummary"]["text"]
    assert view_model["answerSummary"]["meta"] == ["成功", "S0", "只读查询"]
    assert view_model["confirmation"]["visible"] is False
    assert view_model["confirmation"]["summary"] == ""
    assert view_model["confirmation"]["text"] == ""


def test_memory_response_generates_visible_answer_summary() -> None:
    client = _client_with_payload(_memory_payload())

    response = client.post("/api/chat", json={"raw_user_input": "帮我查看当前内存使用情况"})
    payload = response.json()
    view_model = _view_model(payload, raw_user_input="帮我查看当前内存使用情况")

    assert view_model["answerSummary"]["visible"] is True
    assert "当前内存总量" in view_model["answerSummary"]["text"]
    assert "可用" in view_model["answerSummary"]["text"]
    assert "postgres" in view_model["answerSummary"]["text"]
    assert view_model["answerSummary"]["meta"] == ["成功", "S0", "只读查询"]


def test_pending_confirmation_state_is_exposed_to_operator_panel_view_model() -> None:
    client = _client_with_payload(_pending_payload())

    response = client.post("/api/chat", json={"raw_user_input": "请创建普通用户 demo_guest"})
    payload = response.json()
    view_model = _view_model(payload, raw_user_input="请创建普通用户 demo_guest")

    assert payload["operator_panel"]["status"] == "pending_confirmation"
    assert view_model["status"] == "pending_confirmation"
    assert view_model["confirmation"]["visible"] is True
    assert view_model["confirmation"]["status"] == "pending_confirmation"
    assert view_model["confirmation"]["text"] == "确认创建普通用户 demo_guest"
    assert view_model["refusal"]["visible"] is False
    assert view_model["answerSummary"]["visible"] is False


def test_refused_state_and_recovery_block_are_renderable() -> None:
    client = _client_with_payload(_refused_payload())

    response = client.post("/api/chat", json={"raw_user_input": "请删除普通用户 demo_guest"})
    payload = response.json()
    view_model = _view_model(payload, raw_user_input="请删除普通用户 demo_guest")

    assert payload["operator_panel"]["refusal"]["is_refused"] is True
    assert view_model["refusal"]["visible"] is True
    assert "受控策略边界" in view_model["recovery"]["why"]
    assert view_model["recovery"]["visible"] is True
    assert "暂不适合重试" in view_model["recovery"]["flags"]
    assert view_model["residualNextStep"]["summary"]
    assert view_model["answerSummary"]["visible"] is False


def test_unsupported_state_does_not_show_confirmation_placeholder() -> None:
    client = _client_with_payload(_unsupported_payload())

    response = client.post("/api/chat", json={"raw_user_input": "请帮我安装 nginx"})
    payload = response.json()
    view_model = _view_model(payload, raw_user_input="请帮我安装 nginx")

    assert payload["operator_panel"]["confirmation"]["status"] == "not_required"
    assert view_model["status"] == "unsupported"
    assert view_model["confirmation"]["visible"] is False
    assert view_model["confirmation"]["summary"] == ""
    assert view_model["confirmation"]["text"] == ""
    assert "当前没有确认文本" not in json.dumps(view_model, ensure_ascii=False)
    assert view_model["recovery"]["visible"] is True
    assert view_model["answerSummary"]["visible"] is False


def test_evidence_timeline_prefers_narrative_timeline_when_available() -> None:
    client = _client_with_payload(_timeline_payload())

    response = client.post("/api/chat", json={"raw_user_input": "请创建普通用户 demo_guest"})
    payload = response.json()
    view_model = _view_model(payload, raw_user_input="请创建普通用户 demo_guest")

    assert payload["operator_panel"]["timeline_entries"][0]["source"] == "timeline"
    assert view_model["timelineEntries"][0]["title"] == "create_user"
    assert view_model["timelineEntries"][1]["title"] == "verify_user_exists"
    assert view_model["recovery"]["visible"] is True


def test_page_has_no_raw_shell_input_and_keeps_natural_language_entry() -> None:
    html = INDEX_PATH.read_text(encoding="utf-8").lower()

    assert "可信控制面" in html
    assert "运维请求" in html
    assert "<header" not in html
    assert 'class="hero"' not in html
    assert "<textarea" not in html
    assert "raw shell" not in html
    assert "answer-summary-panel" in html
    assert "答案摘要" in html
    assert 'id="operator-request"' in html
