from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.chat import get_executor
from app.main import create_app


ROOT = Path(__file__).resolve().parents[1]
APP_JS_PATH = ROOT / "app" / "ui" / "app.js"
INDEX_PATH = ROOT / "app" / "ui" / "index.html"


class UnexpectedExecutor:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, argv: list[str], timeout: int = 10) -> Any:
        del timeout
        self.calls.append(argv)
        raise AssertionError(f"executor should not run for this preview case: {argv!r}")


def _client_with_executor(executor: UnexpectedExecutor) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_executor] = lambda: executor
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


def test_delete_user_request_exposes_blast_radius_preview() -> None:
    executor = UnexpectedExecutor()
    client = _client_with_executor(executor)

    response = client.post(
        "/api/chat",
        json={"raw_user_input": "\u8bf7\u5220\u9664\u666e\u901a\u7528\u6237 demo_guest"},
    )

    assert response.status_code == 200
    payload = response.json()
    preview = payload["blast_radius_preview"]
    operator_panel_preview = payload["operator_panel"]["blast_radius_preview"]
    simulator = payload["policy_simulator"]

    assert preview["scenario"] == "delete_user"
    assert operator_panel_preview["scenario"] == "delete_user"
    assert any(item["label"] == "Target user" and item["value"] == "demo_guest" for item in preview["facts"])
    assert any(item["label"] == "Known or predicted home" for item in preview["facts"])
    assert any(item["label"] == "Owned files" and item["precision"] == "conservative" for item in preview["impacts"])
    assert any(
        item["label"] == "Sessions and processes" and item["precision"] == "conservative"
        for item in preview["impacts"]
    )
    assert simulator["matched_rules"][0]["rule_id"] == "user.delete_requires_confirmation"
    assert simulator["requires_confirmation"] is True
    assert not any(call[:2] == ["bash", "scripts/guardedops_delete_user.sh"] for call in executor.calls)


def test_create_user_request_exposes_scope_preview() -> None:
    executor = UnexpectedExecutor()
    client = _client_with_executor(executor)

    response = client.post(
        "/api/chat",
        json={"raw_user_input": "\u8bf7\u521b\u5efa\u666e\u901a\u7528\u6237 demo_guest"},
    )

    assert response.status_code == 200
    payload = response.json()
    preview = payload["blast_radius_preview"]
    simulator = payload["policy_simulator"]

    assert preview["scenario"] == "create_user"
    assert any(item["label"] == "Target user" and item["value"] == "demo_guest" for item in preview["facts"])
    assert any(item["label"] == "Home path" and item["value"] == "/home/demo_guest" for item in preview["facts"])
    assert any(
        item["label"] == "Privilege boundary"
        and "sudo" in item["value"]
        and item["precision"] == "bounded"
        for item in preview["impacts"]
    )
    assert simulator["matched_rules"][0]["rule_id"] == "user.create_requires_confirmation"
    assert "sudo/wheel/admin excluded" in simulator["scope_summary"]
    assert not any(call[:2] == ["bash", "scripts/guardedops_create_user.sh"] for call in executor.calls)


def test_large_file_search_preview_explains_limited_scope() -> None:
    executor = UnexpectedExecutor()
    client = _client_with_executor(executor)

    response = client.post(
        "/api/chat",
        json={
            "raw_user_input": (
                "\u8bf7\u5728 /proc \u4e0b\u641c\u7d22\u6587\u4ef6\uff0c"
                "\u6700\u591a\u8fd4\u56de 50 \u6761\uff0c\u6700\u5927\u6df1\u5ea6 8"
            )
        },
    )

    assert response.status_code == 200
    payload = response.json()
    preview = payload["blast_radius_preview"]
    simulator = payload["policy_simulator"]

    assert preview["scenario"] == "file_search"
    assert any(item["label"] == "base_path" and item["value"] == "/proc" for item in preview["facts"])
    assert any(item["label"] == "max_results" and item["value"] == "50" for item in preview["facts"])
    assert any(item["label"] == "max_depth" and item["value"] == "8" for item in preview["facts"])
    assert "/proc" in preview["protected_paths"]
    assert any("deep search" in note or "/proc" in note for note in preview["notes"])
    assert simulator["matched_rules"][0]["rule_id"] == "path.deep_search_refused"
    assert simulator["allow"] is False
    assert not any(call and call[0] == "find" for call in executor.calls)


def test_dangerous_refusal_exposes_policy_simulator_details() -> None:
    executor = UnexpectedExecutor()
    client = _client_with_executor(executor)

    response = client.post("/api/chat", json={"raw_user_input": "把 /etc 下面没用的配置删掉"})

    assert response.status_code == 200
    payload = response.json()
    preview = payload["blast_radius_preview"]
    simulator = payload["policy_simulator"]

    assert payload["risk"]["risk_level"] == "S3"
    assert preview["scenario"] == "dangerous_request"
    assert "/etc" in preview["protected_paths"]
    assert simulator["matched_rules"][0]["rule_id"] == "path.protected_write_denied"
    assert simulator["denied_because"]
    assert simulator["safe_alternative"]
    assert len(simulator["policy_version"]) == 64
    assert len(simulator["target_fingerprint"]) == 64
    assert executor.calls == []


def test_frontend_does_not_participate_in_allow_or_deny_decisions() -> None:
    payload = {
        "operator_panel": {
            "user_input": "show disk",
            "status": "success",
            "risk_level": "S3",
            "risk_reasons": ["server-side deny state"],
            "confidence": 0.5,
            "confidence_source": "test",
            "blast_radius_preview": {
                "scenario": "dangerous_request",
                "summary": "blocked by server policy",
                "facts": [{"label": "Intent", "value": "delete_path"}],
                "impacts": [],
                "protected_paths": ["/etc"],
                "notes": ["server generated"],
            },
            "policy_simulator": {
                "risk_level": "S3",
                "allow": False,
                "requires_confirmation": False,
                "policy_version": "policy-version",
                "matched_rules": [
                    {
                        "rule_id": "path.protected_write_denied",
                        "outcome": "deny",
                        "summary": "server blocked the request",
                    }
                ],
                "denied_because": ["policy engine denied the scope"],
                "requires_confirmation_because": [],
                "scope_summary": "server-side scope summary",
                "target_fingerprint": "target-fingerprint",
                "safe_alternative": "use a bounded read-only request",
            },
            "explanation_sections": [],
            "timeline_entries": [],
            "preflight_items": [],
            "confirmation": {
                "required": False,
                "status": "not_required",
                "text": "",
                "summary": "",
                "evidence_refs": [],
            },
            "refusal": {
                "is_refused": False,
                "reason": "",
                "safe_alternative": "",
                "evidence_refs": [],
            },
            "recovery": {
                "available": False,
                "failure_type": "",
                "why_it_failed": "",
                "safe_next_steps": [],
                "suggested_readonly_diagnostics": [],
                "requires_confirmation_for_recovery": False,
                "can_retry_safely": False,
            },
            "residual_next_step": {"summary": "-", "evidence_refs": []},
        }
    }

    view_model = _view_model(deepcopy(payload), raw_user_input="帮我看当前磁盘")

    assert view_model["status"] == "success"
    assert view_model["refusal"]["visible"] is False
    assert view_model["policySimulator"]["allow"] is False
    assert view_model["policySimulator"]["matchedRules"][0]["ruleId"] == "path.protected_write_denied"
    assert view_model["blastRadius"]["protectedPaths"] == ["/etc"]


def test_page_still_has_no_raw_shell_input() -> None:
    html = INDEX_PATH.read_text(encoding="utf-8").lower()

    assert 'id="operator-request"' in html
    assert 'id="blast-radius-panel"' in html
    assert 'id="policy-simulator-panel"' in html
    assert "<textarea" not in html
    assert "raw shell" not in html
