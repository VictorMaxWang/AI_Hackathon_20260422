from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.orchestrator import ReadonlyOrchestrator
from app.agent.recovery import build_recovery_suggestion
from app.models import ToolResult


EXPECTED_KEYS = {
    "failure_type",
    "why_it_failed",
    "safe_next_steps",
    "requires_confirmation_for_recovery",
    "suggested_readonly_diagnostics",
    "can_retry_safely",
}


def _base_context() -> dict[str, Any]:
    return {
        "parsed_intent": {
            "intent": "query_disk_usage",
            "target": {},
            "constraints": {},
            "requires_write": False,
            "raw_user_input": "show disk usage",
        },
        "environment": {"status": "ok", "snapshot": {"current_user": "operator", "sudo_available": False}},
        "risk": {"risk_level": "S0", "allow": True, "requires_confirmation": False, "reasons": []},
        "plan": {"status": "ready", "steps": [{"tool_name": "disk_usage_tool", "args": {}}]},
        "execution": {"status": "failed", "steps": [], "results": []},
        "result": {"status": "failed", "data": None, "error": "generic failure"},
        "timeline": [],
    }


def _build_recovery(**overrides: Any) -> dict[str, Any] | None:
    payload = _base_context()
    payload.update(overrides)
    return build_recovery_suggestion(
        parsed_intent=payload["parsed_intent"],
        environment=payload["environment"],
        risk=payload["risk"],
        plan=payload["plan"],
        execution=payload["execution"],
        result=payload["result"],
        timeline=payload["timeline"],
    )


def test_recovery_builder_returns_none_for_success_and_normal_pending_confirmation() -> None:
    success = _build_recovery(
        execution={"status": "success", "steps": [], "results": []},
        result={"status": "success", "data": {"status": "ok"}, "error": None},
    )
    pending = _build_recovery(
        parsed_intent={
            "intent": "create_user",
            "target": {"username": "demo_guest"},
            "constraints": {},
            "requires_write": True,
            "raw_user_input": "create normal user demo_guest",
        },
        risk={"risk_level": "S1", "allow": True, "requires_confirmation": True, "reasons": []},
        plan={"status": "pending_confirmation", "steps": [{"tool_name": "create_user_tool", "args": {}}]},
        execution={"status": "skipped", "steps": [], "results": []},
        result={"status": "pending_confirmation", "data": None, "error": None},
    )

    assert success is None
    assert pending is None


def test_recovery_builder_covers_all_required_failure_taxonomy() -> None:
    cases = {
        "confirmation_mismatch": _build_recovery(
            parsed_intent={
                "intent": "create_user",
                "target": {"username": "demo_guest"},
                "constraints": {},
                "requires_write": True,
                "raw_user_input": "create normal user demo_guest",
            },
            risk={"risk_level": "S1", "allow": False, "requires_confirmation": True, "reasons": []},
            plan={"status": "pending_confirmation", "steps": [{"tool_name": "create_user_tool", "args": {}}]},
            execution={"status": "skipped", "steps": [], "results": []},
            result={
                "status": "pending_confirmation",
                "data": None,
                "error": "confirmation_text_mismatch",
                "confirmation_text": "Confirm creating normal user demo_guest",
            },
        ),
        "environment_drift": _build_recovery(
            parsed_intent={
                "intent": "create_user",
                "target": {"username": "demo_guest"},
                "constraints": {},
                "requires_write": True,
                "raw_user_input": "create normal user demo_guest",
            },
            risk={"risk_level": "S1", "allow": False, "requires_confirmation": True, "reasons": []},
            plan={"status": "refused", "steps": [{"tool_name": "create_user_tool", "args": {}}]},
            execution={"status": "failed", "steps": [], "results": []},
            result={"status": "refused", "data": None, "error": "confirmation_token_host_mismatch"},
            timeline=[
                {
                    "step_id": "step_2_drift",
                    "intent": "contract_drift",
                    "risk": "S1",
                    "status": "refused",
                    "result_summary": "contract drift detected: env.current_user changed",
                }
            ],
        ),
        "permission_denied": _build_recovery(
            parsed_intent={
                "intent": "create_user",
                "target": {"username": "demo_guest"},
                "constraints": {},
                "requires_write": True,
                "raw_user_input": "create normal user demo_guest",
            },
            execution={
                "status": "failed",
                "steps": [{"tool_name": "create_user_tool"}],
                "results": [
                    {
                        "tool_name": "create_user_tool",
                        "success": False,
                        "data": {"status": "error", "reason": "permission denied"},
                        "error": "permission denied",
                    }
                ],
            },
            result={"status": "failed", "data": None, "error": "permission denied"},
        ),
        "target_not_found": _build_recovery(
            parsed_intent={
                "intent": "query_process",
                "target": {"pid": 4321},
                "constraints": {},
                "requires_write": False,
                "raw_user_input": "show process 4321",
            },
            execution={
                "status": "failed",
                "steps": [{"tool_name": "process_query_tool"}],
                "results": [
                    {
                        "tool_name": "process_query_tool",
                        "success": False,
                        "data": {"status": "error", "reason": "process 4321 not found"},
                        "error": "process 4321 not found",
                    }
                ],
            },
            result={"status": "failed", "data": None, "error": "process 4321 not found"},
        ),
        "transport_interrupted": _build_recovery(
            execution={
                "status": "failed",
                "steps": [{"tool_name": "disk_usage_tool"}],
                "results": [
                    {
                        "tool_name": "disk_usage_tool",
                        "success": False,
                        "data": {"status": "error", "timed_out": True},
                        "error": "executor failed: ssh connection dropped",
                    }
                ],
            },
            result={"status": "failed", "data": None, "error": "executor failed: ssh connection dropped"},
        ),
        "unsupported_request": _build_recovery(
            parsed_intent={
                "intent": "unknown",
                "target": {},
                "constraints": {},
                "requires_write": False,
                "raw_user_input": "do something unsupported",
            },
            plan={"status": "unsupported", "steps": [], "reason": "request is not supported"},
            execution={"status": "skipped", "steps": [], "results": []},
            result={"status": "unsupported", "data": None, "error": "request is not supported"},
        ),
        "precondition_failed": _build_recovery(
            parsed_intent={
                "intent": "query_port",
                "target": {},
                "constraints": {},
                "requires_write": False,
                "raw_user_input": "query a port",
            },
            plan={"status": "failed", "steps": [{"tool_name": "port_query_tool", "args": {}}], "reason": "port is required"},
            execution={"status": "skipped", "steps": [], "results": []},
            result={"status": "failed", "data": None, "error": "port is required"},
        ),
        "partial_success": _build_recovery(
            parsed_intent={
                "intent": "continuous_task",
                "target": {},
                "constraints": {},
                "requires_write": True,
                "raw_user_input": "create a user and verify it",
            },
            risk={"risk_level": "S1", "allow": True, "requires_confirmation": True, "reasons": []},
            execution={
                "status": "failed",
                "steps": [{"tool_name": "env_probe_tool"}, {"tool_name": "create_user_tool"}],
                "results": [
                    {"tool_name": "env_probe_tool", "success": True, "data": {"status": "ok"}, "error": None},
                    {
                        "tool_name": "create_user_tool",
                        "success": True,
                        "data": {"status": "created", "verified": True},
                        "error": None,
                    },
                    {
                        "tool_name": "verify_user_exists",
                        "success": False,
                        "data": {"status": "error"},
                        "error": "post-check failed",
                    },
                ],
            },
            result={"status": "failed", "data": None, "error": "post-check failed"},
            timeline=[
                {
                    "step_id": "step_1",
                    "intent": "env_probe",
                    "risk": "S0",
                    "status": "success",
                    "result_summary": "environment captured",
                },
                {
                    "step_id": "step_2",
                    "intent": "create_user",
                    "risk": "S1",
                    "status": "success",
                    "result_summary": "user created",
                },
                {
                    "step_id": "step_3_verify",
                    "intent": "verify_user_exists",
                    "risk": "S0",
                    "status": "failed",
                    "result_summary": "post-check failed",
                },
            ],
        ),
    }

    for failure_type, recovery in cases.items():
        assert recovery is not None
        assert recovery["failure_type"] == failure_type
        assert set(recovery) == EXPECTED_KEYS
        assert recovery["why_it_failed"]
        assert recovery["safe_next_steps"]
        assert recovery["suggested_readonly_diagnostics"]


def test_permission_denied_recommends_checking_current_user_and_sudo_state() -> None:
    recovery = _build_recovery(
        parsed_intent={
            "intent": "create_user",
            "target": {"username": "demo_guest"},
            "constraints": {},
            "requires_write": True,
            "raw_user_input": "create normal user demo_guest",
        },
        execution={
            "status": "failed",
            "steps": [{"tool_name": "create_user_tool"}],
            "results": [
                {
                    "tool_name": "create_user_tool",
                    "success": False,
                    "data": {"status": "error", "reason": "permission denied"},
                    "error": "permission denied",
                }
            ],
        },
        result={"status": "failed", "data": None, "error": "permission denied"},
    )

    assert recovery is not None
    assert recovery["failure_type"] == "permission_denied"
    combined = " ".join(recovery["safe_next_steps"] + recovery["suggested_readonly_diagnostics"]).lower()
    assert "current user" in combined
    assert "sudo" in combined
    assert recovery["can_retry_safely"] is False


def test_environment_drift_recommends_reprobe_and_fresh_confirmation() -> None:
    recovery = _build_recovery(
        parsed_intent={
            "intent": "create_user",
            "target": {"username": "demo_guest"},
            "constraints": {},
            "requires_write": True,
            "raw_user_input": "create normal user demo_guest",
        },
        risk={"risk_level": "S1", "allow": False, "requires_confirmation": True, "reasons": []},
        plan={"status": "refused", "steps": [{"tool_name": "create_user_tool", "args": {}}]},
        execution={"status": "failed", "steps": [], "results": []},
        result={"status": "refused", "data": None, "error": "confirmation_token_target_mismatch"},
        timeline=[
            {
                "step_id": "step_2_drift",
                "intent": "contract_drift",
                "risk": "S1",
                "status": "refused",
                "result_summary": "contract drift detected: target.user_exists changed",
            }
        ],
    )

    assert recovery is not None
    assert recovery["failure_type"] == "environment_drift"
    combined = " ".join(recovery["safe_next_steps"] + recovery["suggested_readonly_diagnostics"]).lower()
    assert "read-only" in combined
    assert "fresh plan" in combined or "fresh request" in combined
    assert recovery["requires_confirmation_for_recovery"] is True
    assert recovery["can_retry_safely"] is False


def test_partial_success_lists_completed_steps_and_residual_impact() -> None:
    recovery = _build_recovery(
        parsed_intent={
            "intent": "continuous_task",
            "target": {},
            "constraints": {},
            "requires_write": True,
            "raw_user_input": "create a user and verify it",
        },
        risk={"risk_level": "S1", "allow": True, "requires_confirmation": True, "reasons": []},
        execution={
            "status": "failed",
            "steps": [{"tool_name": "env_probe_tool"}, {"tool_name": "create_user_tool"}],
            "results": [
                {"tool_name": "env_probe_tool", "success": True, "data": {"status": "ok"}, "error": None},
                {
                    "tool_name": "create_user_tool",
                    "success": True,
                    "data": {"status": "created", "verified": True},
                    "error": None,
                },
                {
                    "tool_name": "verify_user_exists",
                    "success": False,
                    "data": {"status": "error"},
                    "error": "post-check failed",
                },
            ],
        },
        result={"status": "failed", "data": None, "error": "post-check failed"},
        timeline=[
            {
                "step_id": "step_1",
                "intent": "env_probe",
                "risk": "S0",
                "status": "success",
                "result_summary": "environment captured",
            },
            {
                "step_id": "step_2",
                "intent": "create_user",
                "risk": "S1",
                "status": "success",
                "result_summary": "user created",
            },
            {
                "step_id": "step_3_verify",
                "intent": "verify_user_exists",
                "risk": "S0",
                "status": "failed",
                "result_summary": "post-check failed",
            },
        ],
    )

    assert recovery is not None
    assert recovery["failure_type"] == "partial_success"
    assert "Completed steps:" in recovery["why_it_failed"]
    assert "Residual impact:" in recovery["why_it_failed"]
    assert "create_user_tool" in recovery["why_it_failed"]
    assert recovery["can_retry_safely"] is False


def test_target_not_found_in_readonly_flow_has_safe_retry_and_readonly_diagnostics() -> None:
    recovery = _build_recovery(
        parsed_intent={
            "intent": "query_process",
            "target": {"pid": 4321},
            "constraints": {},
            "requires_write": False,
            "raw_user_input": "show process 4321",
        },
        execution={
            "status": "failed",
            "steps": [{"tool_name": "process_query_tool"}],
            "results": [
                {
                    "tool_name": "process_query_tool",
                    "success": False,
                    "data": {"status": "error", "reason": "process 4321 not found"},
                    "error": "process 4321 not found",
                }
            ],
        },
        result={"status": "failed", "data": None, "error": "process 4321 not found"},
    )

    assert recovery is not None
    assert recovery["failure_type"] == "target_not_found"
    assert recovery["can_retry_safely"] is True
    assert any("read-only" in item.lower() for item in recovery["suggested_readonly_diagnostics"])
    assert any("retry" in item.lower() for item in recovery["safe_next_steps"])


def test_confirmation_mismatch_requires_fresh_request_not_old_confirmation_replay() -> None:
    recovery = _build_recovery(
        parsed_intent={
            "intent": "create_user",
            "target": {"username": "demo_guest"},
            "constraints": {},
            "requires_write": True,
            "raw_user_input": "create normal user demo_guest",
        },
        risk={"risk_level": "S1", "allow": False, "requires_confirmation": True, "reasons": []},
        plan={"status": "pending_confirmation", "steps": [{"tool_name": "create_user_tool", "args": {}}]},
        execution={"status": "skipped", "steps": [], "results": []},
        result={
            "status": "pending_confirmation",
            "data": None,
            "error": "confirmation_text_mismatch",
            "confirmation_text": "Confirm creating normal user demo_guest",
        },
    )

    assert recovery is not None
    assert recovery["failure_type"] == "confirmation_mismatch"
    combined = " ".join(recovery["safe_next_steps"]).lower()
    assert "re-submit" in combined
    assert "replaying the old confirmation" in combined
    assert recovery["requires_confirmation_for_recovery"] is True
    assert recovery["can_retry_safely"] is False


def test_recovery_suggestions_never_cross_boundaries() -> None:
    recoveries = [
        _build_recovery(
            execution={
                "status": "failed",
                "steps": [{"tool_name": "create_user_tool"}],
                "results": [
                    {
                        "tool_name": "create_user_tool",
                        "success": False,
                        "data": {"status": "error", "reason": "permission denied"},
                        "error": "permission denied",
                    }
                ],
            },
            result={"status": "failed", "data": None, "error": "permission denied"},
        ),
        _build_recovery(
            plan={"status": "unsupported", "steps": [], "reason": "request is not supported"},
            execution={"status": "skipped", "steps": [], "results": []},
            result={"status": "unsupported", "data": None, "error": "request is not supported"},
        ),
        _build_recovery(
            execution={
                "status": "failed",
                "steps": [{"tool_name": "disk_usage_tool"}],
                "results": [
                    {
                        "tool_name": "disk_usage_tool",
                        "success": False,
                        "data": {"status": "error", "timed_out": True},
                        "error": "executor failed: ssh connection dropped",
                    }
                ],
            },
            result={"status": "failed", "data": None, "error": "executor failed: ssh connection dropped"},
        ),
    ]

    forbidden_fragments = (
        "bash",
        "powershell",
        "cmd /c",
        "run sudo",
        "useradd",
        "userdel",
        "bypass policy",
        "elevate privileges",
        "automatically execute",
    )

    for recovery in recoveries:
        assert recovery is not None
        combined = " ".join(
            [recovery["why_it_failed"], *recovery["safe_next_steps"], *recovery["suggested_readonly_diagnostics"]]
        ).lower()
        for fragment in forbidden_fragments:
            assert fragment not in combined


class DummyExecutor:
    pass


class UserToolMocks:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

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


def test_orchestrator_envelope_includes_recovery_and_residual_section_uses_it() -> None:
    mocks = UserToolMocks()
    orchestrator = ReadonlyOrchestrator(
        DummyExecutor(),
        create_user_tool_fn=mocks.create_user,
        delete_user_tool_fn=mocks.delete_user,
    )

    orchestrator.run("请创建普通用户 demo_guest")
    result = orchestrator.run("确认创建普通用户 other_user")

    assert result["result"]["status"] == "pending_confirmation"
    assert result["result"]["error"] == "confirmation_text_mismatch"
    assert result["recovery"]["failure_type"] == "confirmation_mismatch"
    assert result["recovery"]["requires_confirmation_for_recovery"] is True
    assert "confirmation_mismatch" in result["explanation_card"]["residual_risks_or_next_step"]["summary"]
    assert "fresh request" in result["explanation_card"]["residual_risks_or_next_step"]["summary"]
    assert mocks.calls == []
