from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.orchestrator import ReadonlyOrchestrator
from app.models import EnvironmentSnapshot, ToolResult


class DummyExecutor:
    pass


class EvidenceToolMocks:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def env_probe(self, executor: Any) -> EnvironmentSnapshot:
        self.calls.append(("env_probe_tool", {}))
        return EnvironmentSnapshot(
            hostname="demo-host",
            distro="Ubuntu 24.04",
            kernel="6.8.0",
            current_user="demo",
            is_root=False,
            sudo_available=True,
            available_commands=["df", "find", "useradd", "userdel"],
            connection_mode="local",
        )

    def disk_usage(self, executor: Any) -> ToolResult:
        self.calls.append(("disk_usage_tool", {}))
        return ToolResult(
            tool_name="disk_usage_tool",
            success=True,
            data={
                "status": "ok",
                "filesystems": [
                    {
                        "filesystem": "/dev/sdb1",
                        "available": "9G",
                        "use_percent": "91%",
                        "mounted_on": "/data",
                    }
                ],
            },
        )

    def create_user(self, executor: Any, **kwargs: Any) -> ToolResult:
        self.calls.append(("create_user_tool", kwargs))
        return ToolResult(
            tool_name="create_user_tool",
            success=True,
            data={"status": "created", **kwargs},
        )

    def delete_user(self, executor: Any, **kwargs: Any) -> ToolResult:
        self.calls.append(("delete_user_tool", kwargs))
        return ToolResult(
            tool_name="delete_user_tool",
            success=True,
            data={"status": "deleted", **kwargs},
        )


def make_orchestrator(mocks: EvidenceToolMocks) -> ReadonlyOrchestrator:
    return ReadonlyOrchestrator(
        DummyExecutor(),
        env_probe=mocks.env_probe,
        disk_tool=mocks.disk_usage,
        create_user_tool_fn=mocks.create_user,
        delete_user_tool_fn=mocks.delete_user,
    )


def _all_evidence_ids(payload: dict[str, Any]) -> set[str]:
    chain = payload["evidence_chain"]
    event_ids = {
        str(item["event_id"])
        for item in chain["events"]
        if isinstance(item, dict) and "event_id" in item
    }
    assertion_ids = {
        str(item["assertion_id"])
        for item in chain["state_assertions"]
        if isinstance(item, dict) and "assertion_id" in item
    }
    return event_ids | assertion_ids


def test_s0_success_request_generates_explanation_card_and_evidence_chain() -> None:
    mocks = EvidenceToolMocks()
    result = make_orchestrator(mocks).run("帮我看看当前磁盘使用情况")

    assert result["result"]["status"] == "success"
    assert "evidence_chain" in result
    assert "explanation_card" in result
    assert "最紧张" in result["explanation"]

    card = result["explanation_card"]
    assert card["execution_evidence"]["summary"]
    assert card["execution_evidence"]["evidence_refs"]
    assert card["result_assertion"]["evidence_refs"]

    all_ids = _all_evidence_ids(result)
    for key in (
        "intent_normalized",
        "plan_summary",
        "risk_hits",
        "execution_evidence",
        "result_assertion",
    ):
        refs = card[key]["evidence_refs"]
        assert refs
        assert set(refs) <= all_ids


@pytest.mark.parametrize(
    ("raw_user_input", "expected_risk"),
    [
        ("请创建普通用户 demo_guest", "S1"),
        ("请删除普通用户 demo_guest", "S2"),
    ],
)
def test_pending_confirmation_requests_generate_confirmation_basis(
    raw_user_input: str,
    expected_risk: str,
) -> None:
    mocks = EvidenceToolMocks()
    result = make_orchestrator(mocks).run(raw_user_input)

    assert result["risk"]["risk_level"] == expected_risk
    assert result["result"]["status"] == "pending_confirmation"
    assert mocks.calls == []

    card = result["explanation_card"]
    assert "待确认" in card["confirmation_basis"]["summary"]
    assert card["confirmation_basis"]["evidence_refs"]


def test_s3_refusal_generates_risk_hits_and_residual_guidance() -> None:
    mocks = EvidenceToolMocks()
    result = make_orchestrator(mocks).run("请创建普通用户 root")

    assert result["risk"]["risk_level"] == "S3"
    assert result["result"]["status"] == "refused"
    assert mocks.calls == []

    card = result["explanation_card"]
    assert "风险等级：S3" in card["risk_hits"]["summary"]
    assert card["risk_hits"]["evidence_refs"]
    assert "安全替代方案" in result["explanation"]
    assert card["residual_risks_or_next_step"]["summary"]
    assert card["residual_risks_or_next_step"]["evidence_refs"]


def test_explanation_card_key_sections_are_backed_by_valid_evidence_refs() -> None:
    mocks = EvidenceToolMocks()
    result = make_orchestrator(mocks).run("帮我看看当前磁盘使用情况")

    all_ids = _all_evidence_ids(result)
    card = result["explanation_card"]
    for key in ("risk_hits", "confirmation_basis", "execution_evidence", "result_assertion"):
        refs = card[key]["evidence_refs"]
        assert refs
        assert set(refs) <= all_ids


def test_evidence_chain_is_json_serializable() -> None:
    mocks = EvidenceToolMocks()
    result = make_orchestrator(mocks).run("帮我看看当前磁盘使用情况")

    encoded = json.dumps(result["evidence_chain"], ensure_ascii=False)
    assert "\"events\"" in encoded
    assert "\"state_assertions\"" in encoded
