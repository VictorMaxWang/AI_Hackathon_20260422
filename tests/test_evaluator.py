from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evolution import evaluate_execution
from app.models.evolution import EvaluationResult, MemoryType


def test_s3_refusal_is_safety_success_and_experience_candidate() -> None:
    result = evaluate_execution(
        {
            "intent": {
                "intent": "delete_path",
                "target": {"path": "/etc"},
                "requires_write": True,
            },
            "risk": {
                "risk_level": "S3",
                "allow": False,
                "requires_confirmation": False,
            },
            "execution": {"status": "skipped", "steps": [], "results": []},
            "result": {"status": "refused", "data": None, "error": "protected path"},
        }
    )

    assert result.task_success is False
    assert result.safety_success is True
    assert result.experience_candidate is True
    assert result.suggested_memory_type == MemoryType.EPISODIC
    assert "s3_refusal" in result.tags


def test_s0_query_success() -> None:
    result = evaluate_execution(
        {
            "intent": {"intent": "query_disk_usage", "requires_write": False},
            "risk": {
                "risk_level": "S0",
                "allow": True,
                "requires_confirmation": False,
            },
            "execution": {
                "status": "success",
                "steps": [{"tool_name": "disk_usage_tool", "success": True}],
                "results": [{"tool_name": "disk_usage_tool", "success": True}],
            },
            "result": {"status": "success", "data": {"status": "ok"}, "error": None},
        }
    )

    assert result.task_success is True
    assert result.safety_success is True
    assert result.needs_reflection is False
    assert result.experience_candidate is False


def test_s1_and_s2_pending_confirmation_are_safe() -> None:
    for risk_level in ("S1", "S2"):
        result = evaluate_execution(
            {
                "intent": {"intent": "create_user", "requires_write": True},
                "risk": {
                    "risk_level": risk_level,
                    "allow": True,
                    "requires_confirmation": True,
                },
                "plan": {"status": "pending_confirmation"},
                "execution": {"status": "skipped", "steps": [], "results": []},
                "result": {
                    "status": "pending_confirmation",
                    "data": None,
                    "error": None,
                    "confirmation_text": "Confirm",
                },
            }
        )

        assert result.task_success is False
        assert result.safety_success is True
        assert result.confirmation_ok is True
        assert result.needs_reflection is False
        assert "pending_confirmation" in result.tags


def test_post_check_failure_needs_reflection() -> None:
    result = evaluate_execution(
        {
            "intent": {"intent": "create_user", "requires_write": True},
            "risk": {
                "risk_level": "S1",
                "allow": True,
                "requires_confirmation": True,
            },
            "plan": {"status": "confirmed"},
            "execution": {
                "status": "success",
                "steps": [{"tool_name": "create_user_tool", "success": True}],
                "results": [{"tool_name": "create_user_tool", "success": True}],
            },
            "result": {"status": "success", "data": {"username": "demo_user"}, "error": None},
            "post_check": {"passed": False, "reason": "user not found after create"},
        }
    )

    assert result.task_success is False
    assert result.post_check_passed is False
    assert result.needs_reflection is True
    assert result.experience_candidate is True
    assert result.suggested_memory_type == MemoryType.EPISODIC
    assert "post_check_failed" in result.tags


def test_wrong_confirmation_does_not_execute_and_is_safe() -> None:
    result = evaluate_execution(
        {
            "intent": {"intent": "create_user", "requires_write": True},
            "risk": {
                "risk_level": "S1",
                "allow": False,
                "requires_confirmation": True,
                "confirmation_text": "Confirm creating normal user demo_user",
            },
            "plan": {"status": "pending_confirmation"},
            "execution": {"status": "skipped", "steps": [], "results": []},
            "result": {
                "status": "pending_confirmation",
                "data": None,
                "error": "confirmation_text_mismatch",
            },
        }
    )

    assert result.task_success is False
    assert result.safety_success is True
    assert result.confirmation_ok is False
    assert result.experience_candidate is True
    assert result.needs_reflection is False
    assert "confirmation_mismatch" in result.tags


def test_evaluation_result_is_json_serializable() -> None:
    result = EvaluationResult(
        task_success=True,
        safety_success=True,
        post_check_passed=True,
        confirmation_ok=True,
        needs_reflection=False,
        experience_candidate=False,
        suggested_memory_type=MemoryType.NONE,
        reasons=["ok"],
        tags=["json"],
    )

    payload = json.loads(result.model_dump_json())

    assert payload["task_success"] is True
    assert payload["suggested_memory_type"] == "none"
    assert payload["tags"] == ["json"]
