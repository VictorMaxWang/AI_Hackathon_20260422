from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evolution.evaluator import evaluate_execution
from app.evolution.reflection import generate_reflection
from app.models.evolution import EvaluationResult, MemoryType


def test_delete_etc_refusal_generates_high_risk_reflection() -> None:
    context = {
        "intent": {
            "intent": "delete_path",
            "target": {"path": "/etc"},
            "requires_write": True,
        },
        "risk": {"risk_level": "S3", "allow": False},
        "execution": {"status": "skipped", "steps": [], "results": []},
        "result": {"status": "refused", "error": "protected path /etc"},
    }
    evaluation = evaluate_execution(context)

    reflection = generate_reflection(
        evaluation,
        source_request_id="req-delete-etc",
        execution_context=context,
    )

    assert reflection.source_request_id == "req-delete-etc"
    assert reflection.memory_type == MemoryType.EPISODIC
    assert reflection.promote_to_workflow_candidate is False
    assert "高风险" in reflection.summary
    assert "/etc" in reflection.failure_reason
    assert "只读盘点" in reflection.next_time_suggestion
    assert "high_risk_refusal" in reflection.tags


def test_full_disk_search_refusal_suggests_bounded_search() -> None:
    evaluation = EvaluationResult(
        task_success=False,
        safety_success=True,
        experience_candidate=True,
        suggested_memory_type=MemoryType.EPISODIC,
        reasons=["S3 request refused before tool execution"],
        tags=["risk:S3", "s3_refusal"],
    )
    context = {
        "intent": "file_search",
        "target": {"path": "/"},
        "risk": {"risk_level": "S3"},
        "result": {"status": "refused", "error": "base_path is too broad"},
    }

    reflection = generate_reflection(
        evaluation,
        source_request_id="req-full-disk-search",
        execution_context=context,
    )

    assert reflection.memory_type == MemoryType.PROCEDURAL
    assert reflection.promote_to_workflow_candidate is True
    assert "范围过大" in reflection.summary
    assert "base_path" in reflection.next_time_suggestion
    assert "max_results" in reflection.next_time_suggestion
    assert "max_depth" in reflection.next_time_suggestion
    assert "file_search_scope_limited" in reflection.tags


@pytest.mark.parametrize(
    ("error", "expected_reason"),
    [
        ("permission denied", "权限不足"),
        ("user demo_user already exists", "已存在"),
        ("user creation command missing", "命令缺失"),
    ],
)
def test_create_user_failure_reflection_explains_known_reasons(
    error: str,
    expected_reason: str,
) -> None:
    evaluation = EvaluationResult(
        task_success=False,
        safety_success=True,
        needs_reflection=True,
        experience_candidate=True,
        suggested_memory_type=MemoryType.EPISODIC,
        reasons=["tool result failed"],
        tags=["tool_failed", "needs_reflection"],
    )
    context = {
        "intent": "create_user",
        "tool_results": [
            {"tool_name": "create_user_tool", "success": False, "error": error}
        ],
        "result": {"status": "failed", "error": error},
    }

    reflection = generate_reflection(
        evaluation,
        source_request_id=f"req-create-user-{expected_reason}",
        execution_context=context,
    )

    assert reflection.memory_type == MemoryType.EPISODIC
    assert reflection.promote_to_workflow_candidate is False
    assert expected_reason in reflection.failure_reason
    assert "只读检查用户是否已存在" in reflection.next_time_suggestion
    assert "create_user_failed" in reflection.tags


def test_confirmation_mismatch_reflection_asks_for_exact_confirmation() -> None:
    context = {
        "intent": {"intent": "delete_user", "requires_write": True},
        "risk": {"risk_level": "S2", "requires_confirmation": True},
        "plan": {"status": "pending_confirmation"},
        "execution": {"status": "skipped", "steps": [], "results": []},
        "result": {
            "status": "pending_confirmation",
            "error": "confirmation_text_mismatch",
        },
    }
    evaluation = evaluate_execution(context)

    reflection = generate_reflection(
        evaluation,
        source_request_id="req-confirmation-mismatch",
        execution_context=context,
    )

    assert reflection.promote_to_workflow_candidate is False
    assert "确认语不匹配" in reflection.summary
    assert "精确确认语" in reflection.next_time_suggestion
    assert "confirmation_mismatch" in reflection.tags


def test_continuous_task_abort_reflection_mentions_interrupted_step() -> None:
    evaluation = EvaluationResult(
        task_success=False,
        safety_success=True,
        needs_reflection=True,
        experience_candidate=True,
        suggested_memory_type=MemoryType.EPISODIC,
        reasons=["timeline contains failed or aborted step"],
        tags=["timeline_failed", "needs_reflection"],
    )
    context = {
        "task_type": "continuous",
        "timeline": [
            {"step_id": "probe", "intent": "env_probe", "status": "success"},
            {"step_id": "create_user", "intent": "create_user", "status": "aborted"},
            {"step_id": "delete_user", "intent": "delete_user", "status": "skipped"},
        ],
    }

    reflection = generate_reflection(
        evaluation,
        source_request_id="req-continuous-abort",
        execution_context=context,
    )

    assert "中断" in reflection.summary
    assert "create_user" in reflection.summary
    assert "补全前置条件" in reflection.next_time_suggestion
    assert "continuous_task_aborted" in reflection.tags


def test_reflections_do_not_generate_dangerous_suggestions() -> None:
    records = [
        generate_reflection(
            EvaluationResult(
                task_success=False,
                safety_success=True,
                experience_candidate=True,
                tags=["risk:S3", "s3_refusal"],
            ),
            source_request_id="req-safe-1",
            execution_context={
                "intent": "delete_path",
                "target": {"path": "/etc"},
                "risk": {"risk_level": "S3"},
                "result": {"status": "refused"},
            },
        ),
        generate_reflection(
            EvaluationResult(
                task_success=False,
                safety_success=True,
                experience_candidate=True,
                tags=["risk:S3", "s3_refusal"],
            ),
            source_request_id="req-safe-2",
            execution_context={
                "intent": "file_search",
                "target": {"path": "/"},
                "result": {"status": "refused", "error": "base_path too broad"},
            },
        ),
    ]

    forbidden_fragments = (
        "rm -",
        "chmod ",
        "chown ",
        "bash -c",
        "sh -c",
        "shell=True",
        "run_shell_tool",
        "execute_command_tool",
        "绕过",
        "放宽风控",
        "禁用校验",
        "跳过确认",
        "修改策略",
        "修改执行器",
    )

    for record in records:
        text = _reflection_text(record)
        for fragment in forbidden_fragments:
            assert fragment not in text


def _reflection_text(record) -> str:
    return " ".join(
        [
            record.summary,
            record.lesson,
            record.failure_reason,
            record.next_time_suggestion,
        ]
    )
