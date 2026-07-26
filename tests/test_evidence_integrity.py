from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.confirmation import (
    UNAVAILABLE_FINGERPRINT,
    fingerprint_is_available,
    stable_file_content_hash,
)
from app.agent.previews import build_blast_radius_preview, build_policy_simulator
from app.agent.recovery import FAILURE_UNKNOWN_STATE, build_recovery_suggestion
from app.agent.summarizer import ReadonlySummarizer
from app.evolution.regression import _run_probe_disclosure_invariant
from app.models import IntentTarget, ParsedIntent, PolicyDecision, RiskLevel
from app.models.evidence import (
    BINDING_PROBE_REASON,
    EvidenceChain,
    NO_SIDE_EFFECTS,
    STATE_UNKNOWN,
    binding_probe_disclosure,
    crash_state_disposition,
    final_outcome_assertion_summary,
    is_state_unknown,
    tool_call_records,
)
from tests.test_evidence_layer import EvidenceToolMocks, make_orchestrator


def _unknown_state_envelope() -> dict[str, Any]:
    """Envelope shape for a crash that happened after the write already ran."""

    return {
        "parsed_intent": {
            "intent": "create_user",
            "target": {"username": "demo_guest"},
            "constraints": {},
            "requires_write": True,
            "raw_user_input": "确认创建普通用户 demo_guest",
        },
        "environment": {"status": "ok", "snapshot": {"hostname": "demo-host"}},
        "risk": {
            "risk_level": "S1",
            "allow": True,
            "requires_confirmation": True,
            "reasons": ["create one normal user"],
        },
        "plan": {
            "status": "confirmed",
            "steps": [{"tool_name": "create_user_tool", "args": {"username": "demo_guest"}}],
        },
        "execution": {
            "status": "unknown",
            "steps": [
                {
                    "tool_name": "create_user_tool",
                    "args": {"username": "demo_guest"},
                    "success": True,
                    "error": None,
                }
            ],
            "results": [
                {
                    "tool_name": "create_user_tool",
                    "success": True,
                    "data": {"status": "created"},
                    "error": None,
                }
            ],
        },
        "result": {
            "status": "unknown",
            "data": None,
            "error": "RuntimeError: crashed after the write wrapper returned",
            "state_unknown": True,
        },
        "timeline": [],
    }


def _card_summaries(envelope: dict[str, Any], recovery: dict[str, Any] | None = None) -> dict[str, str]:
    card = ReadonlySummarizer().build_explanation_card(
        parsed_intent=envelope["parsed_intent"],
        environment=envelope["environment"],
        risk=envelope["risk"],
        plan=envelope["plan"],
        execution=envelope["execution"],
        result=envelope["result"],
        evidence_chain=EvidenceChain(),
        recovery=recovery,
        timeline=envelope.get("timeline"),
    )
    return {key: str(value["summary"]) for key, value in card.model_dump(mode="json").items()}


def test_crash_before_any_tool_ran_claims_no_side_effects() -> None:
    assert crash_state_disposition({"status": "skipped", "steps": [], "results": []}) == NO_SIDE_EFFECTS
    assert crash_state_disposition(None) == NO_SIDE_EFFECTS
    assert (
        crash_state_disposition(
            {"steps": [{"tool_name": "env_probe_tool"}, {"tool_name": "disk_usage_tool"}]}
        )
        == NO_SIDE_EFFECTS
    )


def test_crash_after_a_write_tool_was_entered_claims_unknown_state() -> None:
    entered_without_result = {
        "steps": [{"tool_name": "env_probe_tool"}, {"tool_name": "create_user_tool"}],
        "results": [{"tool_name": "env_probe_tool", "success": True}],
    }
    failed_write = {
        "steps": [{"tool_name": "delete_user_tool"}],
        "results": [{"tool_name": "delete_user_tool", "success": False, "error": "boom"}],
    }

    assert crash_state_disposition(entered_without_result) == STATE_UNKNOWN
    assert crash_state_disposition(failed_write) == STATE_UNKNOWN


def test_state_unknown_is_detected_from_status_flag_and_execution_record() -> None:
    assert is_state_unknown({"status": "unknown"}) is True
    assert is_state_unknown({"status": "failed", "state_unknown": True}) is True
    assert is_state_unknown({"status": "failed"}, {"status": "unknown"}) is True
    assert is_state_unknown({"status": "refused"}) is False
    assert is_state_unknown(None) is False


def test_unknown_state_outcome_assertion_never_claims_a_refusal() -> None:
    summary = final_outcome_assertion_summary(_unknown_state_envelope()["result"])

    assert "未知" in summary
    assert "refused" not in summary
    assert final_outcome_assertion_summary({"status": "refused"}) == "最终结果为 refused。"
    assert final_outcome_assertion_summary({"status": "success"}) == "最终结果为 success。"


def test_crash_after_a_write_produces_an_unknown_state_recovery_suggestion() -> None:
    envelope = _unknown_state_envelope()

    recovery = build_recovery_suggestion(
        parsed_intent=envelope["parsed_intent"],
        environment=envelope["environment"],
        risk=envelope["risk"],
        plan=envelope["plan"],
        execution=envelope["execution"],
        result=envelope["result"],
        timeline=envelope["timeline"],
    )

    assert recovery is not None
    assert recovery["failure_type"] == FAILURE_UNKNOWN_STATE
    assert recovery["can_retry_safely"] is False
    assert recovery["requires_confirmation_for_recovery"] is True
    assert "create_user_tool" in recovery["why_it_failed"]
    assert any("Verify" in step for step in recovery["safe_next_steps"])


def test_unknown_state_card_reports_the_tool_calls_that_did_happen() -> None:
    envelope = _unknown_state_envelope()

    summaries = _card_summaries(envelope)

    assert "create_user_tool" in summaries["execution_evidence"]
    assert "没有工具调用记录" not in summaries["execution_evidence"]
    assert "未知" in summaries["execution_evidence"]
    assert "未知" in summaries["result_assertion"]
    assert "人工核对" in summaries["residual_risks_or_next_step"]


def test_unknown_state_card_keeps_its_story_when_recovery_is_attached() -> None:
    envelope = _unknown_state_envelope()
    recovery = build_recovery_suggestion(
        parsed_intent=envelope["parsed_intent"],
        environment=envelope["environment"],
        risk=envelope["risk"],
        plan=envelope["plan"],
        execution=envelope["execution"],
        result=envelope["result"],
        timeline=envelope["timeline"],
    )

    summaries = _card_summaries(envelope, recovery=recovery)

    assert FAILURE_UNKNOWN_STATE in summaries["residual_risks_or_next_step"]


def test_tool_call_records_keep_a_step_whose_result_never_arrived() -> None:
    execution = {
        "status": "failed",
        "steps": [
            {"tool_name": "env_probe_tool", "args": {}, "success": True, "error": None},
            {
                "tool_name": "disk_usage_tool",
                "args": {"path": "/"},
                "success": False,
                "error": "boom",
            },
        ],
        "results": [{"tool_name": "env_probe_tool", "success": True, "data": {}, "error": None}],
    }

    records = tool_call_records(execution)

    assert [record.tool_name for record in records] == ["env_probe_tool", "disk_usage_tool"]
    assert [record.order for record in records] == [1, 2]
    assert records[0].result_recorded is True
    assert records[1].result_recorded is False
    assert records[1].error == "boom"
    assert records[1].args == {"path": "/"}


def test_tool_call_records_are_empty_only_when_nothing_was_invoked() -> None:
    assert tool_call_records({"status": "skipped", "steps": [], "results": []}) == []
    assert tool_call_records(None) == []
    assert len(tool_call_records({"steps": [], "results": [{"tool_name": "disk_usage_tool"}]})) == 1


def test_executed_step_without_a_result_is_still_reported_to_the_operator() -> None:
    mocks = EvidenceToolMocks()
    orchestrator = make_orchestrator(mocks)

    def failing_probe(executor: Any) -> Any:
        raise RuntimeError("probe exploded")

    orchestrator.env_probe = failing_probe
    envelope = orchestrator.run("帮我看看当前磁盘使用情况")

    assert envelope["execution"]["steps"]
    assert envelope["execution"]["results"] == []

    execution_summary = envelope["explanation_card"]["execution_evidence"]["summary"]
    assert "env_probe_tool" in execution_summary
    assert "没有工具调用记录" not in execution_summary


def test_binding_probe_disclosure_only_fires_for_the_confirmation_probe() -> None:
    assert binding_probe_disclosure({"status": "ok", "reason": BINDING_PROBE_REASON}) is not None
    assert binding_probe_disclosure({"status": "ok"}) is None
    assert binding_probe_disclosure({"reason": "internal_error"}) is None
    assert binding_probe_disclosure(None) is None


def test_pending_confirmation_turn_discloses_the_readonly_binding_probe() -> None:
    mocks = EvidenceToolMocks()
    envelope = make_orchestrator(mocks).run("请创建普通用户 demo_guest")

    assert envelope["result"]["status"] == "pending_confirmation"
    assert envelope["environment"]["reason"] == BINDING_PROBE_REASON
    assert envelope["execution"]["results"] == []

    execution_summary = envelope["explanation_card"]["execution_evidence"]["summary"]
    assert "env_probe_tool" in execution_summary
    assert "不在执行记录内" in execution_summary


def test_probe_disclosure_invariant_fails_when_the_probe_is_hidden() -> None:
    hidden = {
        "environment": {"status": "ok", "reason": BINDING_PROBE_REASON},
        "execution": {"status": "skipped", "steps": [], "results": []},
        "explanation": "确认语已发出。",
        "explanation_card": {"execution_evidence": {"summary": "执行证据：当前没有工具调用记录。"}},
    }

    checks = _run_probe_disclosure_invariant(hidden, check_prefix="final")

    assert len(checks) == 1
    assert checks[0]["passed"] is False


def test_probe_disclosure_invariant_passes_on_a_real_pending_envelope() -> None:
    mocks = EvidenceToolMocks()
    envelope = make_orchestrator(mocks).run("请创建普通用户 demo_guest")

    checks = _run_probe_disclosure_invariant(envelope, check_prefix="final")

    assert len(checks) == 1
    assert checks[0]["passed"] is True


def test_probe_disclosure_invariant_is_silent_without_an_out_of_band_probe() -> None:
    mocks = EvidenceToolMocks()
    envelope = make_orchestrator(mocks).run("帮我看看当前磁盘使用情况")

    assert _run_probe_disclosure_invariant(envelope, check_prefix="final") == []


def _search_intent(path: str) -> ParsedIntent:
    return ParsedIntent(
        intent="search_files",
        target=IntentTarget(path=path, keyword="conf"),
        constraints={"base_path": path, "max_depth": 4, "max_results": 20},
        raw_user_input=f"在 {path} 里找 conf 文件",
    )


def test_traversal_scope_preview_reports_the_directory_that_is_really_searched() -> None:
    parsed_intent = _search_intent("/var/log/../../etc")
    risk = PolicyDecision(risk_level=RiskLevel.S0, allow=True)

    preview = build_blast_radius_preview(parsed_intent=parsed_intent, risk=risk)
    facts = {item["label"]: item["value"] for item in preview["facts"]}

    assert facts["base_path"] == "/etc"
    assert facts["requested base_path"] == "/var/log/../../etc"
    assert "/etc" in preview["summary"]
    assert "/var/log/../../etc" in preview["summary"]
    assert "/etc" in preview["protected_paths"]


def test_policy_simulator_scope_summary_shows_the_normalized_scope() -> None:
    parsed_intent = _search_intent("/var/log/../../etc")
    risk = PolicyDecision(risk_level=RiskLevel.S0, allow=True)

    simulator = build_policy_simulator(
        parsed_intent=parsed_intent,
        risk=risk,
        policy_version="test-policy",
    )

    assert "under /etc" in simulator["scope_summary"]
    assert "/var/log/../../etc" in simulator["scope_summary"]


def test_plain_scope_preview_is_unchanged_when_no_rewrite_happens() -> None:
    parsed_intent = _search_intent("/var/log")
    risk = PolicyDecision(risk_level=RiskLevel.S0, allow=True)

    preview = build_blast_radius_preview(parsed_intent=parsed_intent, risk=risk)
    facts = {item["label"]: item["value"] for item in preview["facts"]}

    assert facts["base_path"] == "/var/log"
    assert "requested base_path" not in facts
    assert "resolves to actual scope" not in preview["summary"]


def test_policy_fingerprint_over_zero_files_is_reported_as_unavailable(tmp_path: Path) -> None:
    empty_dir = tmp_path / "policy"
    empty_dir.mkdir()

    fingerprint = stable_file_content_hash(sorted(empty_dir.glob("*.py")))

    assert fingerprint == UNAVAILABLE_FINGERPRINT
    assert fingerprint_is_available(fingerprint) is False


def test_policy_fingerprint_over_real_files_stays_a_content_hash(tmp_path: Path) -> None:
    policy_dir = tmp_path / "policy"
    policy_dir.mkdir()
    (policy_dir / "rules.py").write_text("PROTECTED = ('/etc',)\n", encoding="utf-8")

    fingerprint = stable_file_content_hash(sorted(policy_dir.glob("*.py")))

    assert fingerprint_is_available(fingerprint) is True
    assert len(fingerprint) == 64

    (policy_dir / "rules.py").write_text("PROTECTED = ()\n", encoding="utf-8")
    assert stable_file_content_hash(sorted(policy_dir.glob("*.py"))) != fingerprint


def test_unreadable_policy_files_do_not_produce_a_confident_fingerprint(tmp_path: Path) -> None:
    missing = tmp_path / "policy" / "rules.py"

    assert stable_file_content_hash([missing]) == UNAVAILABLE_FINGERPRINT
