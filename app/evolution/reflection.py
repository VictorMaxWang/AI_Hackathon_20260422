from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from app.models.evolution import EvaluationResult, MemoryType, ReflectionRecord


@dataclass(frozen=True)
class _ReflectionTemplate:
    tag: str
    memory_type: MemoryType
    summary: str
    lesson: str
    failure_reason: str
    next_time_suggestion: str
    promote_to_workflow_candidate: bool = False


_DANGEROUS_SUGGESTION_RE = re.compile(
    r"(?i)(?:"
    r"rm\s+-|chmod\s+[0-7]|chown\s+\S|bash\s+-c|sh\s+-c|"
    r"powershell\s+-command|cmd\s+/c|shell=True|run_shell_tool|"
    r"execute_command_tool|bash_tool|raw shell|"
    r"绕过|关闭风控|放宽风控|放宽\s*policy|禁用校验|跳过确认|取消确认|"
    r"修改\s*policy|修改策略|修改执行器|修改\s*executor"
    r")"
)


def generate_reflection(
    evaluation: EvaluationResult | Mapping[str, Any] | BaseModel,
    *,
    source_request_id: str,
    execution_context: Mapping[str, Any] | BaseModel | None = None,
    created_at: datetime | None = None,
) -> ReflectionRecord:
    """Generate one conservative Chinese reflection from evaluator output.

    This function is intentionally deterministic and side-effect free. It does
    not call an LLM, write to a store, connect to the orchestrator, or change
    any safety boundary.
    """

    evaluation_data = _normalize_evaluation(evaluation)
    context = _as_dict(execution_context)
    template = _select_template(evaluation_data, context)
    tags = _merge_tags(["reflection", template.tag], evaluation_data["tags"])

    record = ReflectionRecord(
        reflection_id=f"reflection-{uuid4().hex[:12]}",
        source_request_id=source_request_id,
        memory_type=template.memory_type,
        summary=template.summary,
        lesson=template.lesson,
        failure_reason=template.failure_reason,
        next_time_suggestion=template.next_time_suggestion,
        tags=tags,
        promote_to_workflow_candidate=template.promote_to_workflow_candidate,
        created_at=created_at or datetime.now(timezone.utc),
    )
    _assert_safe_reflection(record)
    return record


def _select_template(
    evaluation: dict[str, Any],
    context: dict[str, Any],
) -> _ReflectionTemplate:
    if _is_confirmation_mismatch(evaluation, context):
        return _ReflectionTemplate(
            tag="confirmation_mismatch",
            memory_type=MemoryType.EPISODIC,
            summary="确认语不匹配，写操作保持暂停。",
            lesson="敏感操作必须以精确确认语为准，错误确认不能推进执行。",
            failure_reason="用户输入的确认语与要求文本不一致。",
            next_time_suggestion="下次先核对系统给出的精确确认语，再重新提交确认。",
        )

    if _is_file_search_scope_too_large(evaluation, context):
        return _ReflectionTemplate(
            tag="file_search_scope_limited",
            memory_type=MemoryType.PROCEDURAL,
            summary="文件搜索范围过大，已按安全限制拒绝或截断。",
            lesson="文件搜索必须限定 base_path、max_results 和 max_depth，避免全盘或无限递归。",
            failure_reason="搜索范围缺少有效边界，可能覆盖受保护路径或返回过多结果。",
            next_time_suggestion="下次提供具体 base_path，并设置 max_results 和 max_depth 后再做只读搜索。",
            promote_to_workflow_candidate=True,
        )

    if _is_create_user_failure(evaluation, context):
        return _ReflectionTemplate(
            tag="create_user_failed",
            memory_type=MemoryType.EPISODIC,
            summary="用户创建未成功，未把失败伪装成完成。",
            lesson="普通用户创建失败时只记录原因，不尝试绕开权限或扩大变更范围。",
            failure_reason=_create_user_failure_reason(evaluation, context),
            next_time_suggestion="下次先只读检查用户是否已存在、权限是否足够、创建能力是否可用，再按确认流程处理。",
        )

    if _is_continuous_task_aborted(evaluation, context):
        interrupted_step = _interrupted_step(context)
        return _ReflectionTemplate(
            tag="continuous_task_aborted",
            memory_type=MemoryType.EPISODIC,
            summary=f"连续任务在{interrupted_step}中断，后续步骤未继续。",
            lesson="连续任务必须以前置步骤成功为条件，不能在依赖缺失时继续推进。",
            failure_reason="前置步骤失败、被拒绝或未完成确认，导致连续任务中止。",
            next_time_suggestion="下次先补全前置条件，并确认中断步骤成功后再继续后续任务。",
        )

    if _is_high_risk_refusal(evaluation, context):
        return _ReflectionTemplate(
            tag="high_risk_refusal",
            memory_type=MemoryType.EPISODIC,
            summary="高风险请求已拒绝，未执行任何工具。",
            lesson="受保护路径或禁止类写操作必须由策略拒绝，经验只能记录拒绝原因。",
            failure_reason=_high_risk_failure_reason(context),
            next_time_suggestion="下次改为只读盘点，或收窄到非核心业务路径后查看候选对象。",
        )

    return _ReflectionTemplate(
        tag="generic_execution_reflection",
        memory_type=_suggested_memory_type(evaluation),
        summary="执行结果需要沉淀为简短经验。",
        lesson="失败经验只用于改进后续判断，不改变安全边界。",
        failure_reason=_generic_failure_reason(evaluation),
        next_time_suggestion="下次先核对前置条件、范围和确认状态，再选择受支持的安全流程。",
    )


def _is_confirmation_mismatch(evaluation: dict[str, Any], context: dict[str, Any]) -> bool:
    text = _combined_text(evaluation, context)
    return (
        "confirmation_mismatch" in evaluation["tags"]
        or evaluation.get("confirmation_ok") is False
        or "confirmation_text_mismatch" in text
        or "确认语不匹配" in text
    )


def _is_file_search_scope_too_large(evaluation: dict[str, Any], context: dict[str, Any]) -> bool:
    text = _combined_text(evaluation, context)
    intent = _intent_name(context)
    path = str(_deep_get(context, "target", "path") or _deep_get(context, "params", "base_path") or "")
    return (
        "file" in intent
        and ("search" in intent or "搜索" in text or "检索" in text)
        and (
            path == "/"
            or "全盘" in text
            or "整个硬盘" in text
            or "base_path" in text
            or "max_results" in text
            or "max_depth" in text
            or "scope" in text
            or "too broad" in text
        )
    )


def _is_create_user_failure(evaluation: dict[str, Any], context: dict[str, Any]) -> bool:
    text = _combined_text(evaluation, context)
    intent = _intent_name(context)
    return (
        "create_user" in intent
        or "create_user_tool" in text
        or "创建用户" in text
        or "用户创建" in text
    ) and (
        evaluation.get("task_success") is False
        or "tool_failed" in evaluation["tags"]
        or "execution_failed" in evaluation["tags"]
        or "failed" in text
        or "失败" in text
    )


def _is_continuous_task_aborted(evaluation: dict[str, Any], context: dict[str, Any]) -> bool:
    text = _combined_text(evaluation, context)
    timeline = _as_list(context.get("timeline"))
    has_aborted_step = any(_lower(item.get("status")) in {"aborted", "failed"} for item in timeline)
    return (
        "continuous" in text
        or "连续任务" in text
        or len(timeline) > 1
    ) and (
        "timeline_failed" in evaluation["tags"]
        or "aborted" in text
        or "中断" in text
        or has_aborted_step
    )


def _is_high_risk_refusal(evaluation: dict[str, Any], context: dict[str, Any]) -> bool:
    text = _combined_text(evaluation, context)
    return (
        "s3_refusal" in evaluation["tags"]
        or "risk:s3" in {tag.lower() for tag in evaluation["tags"]}
        or _risk_level(context) == "S3"
    ) and (
        "refused" in text
        or "denied" in text
        or "拒绝" in text
        or evaluation.get("safety_success") is True
    )


def _create_user_failure_reason(
    evaluation: dict[str, Any],
    context: dict[str, Any],
) -> str:
    text = _combined_text(evaluation, context)
    if any(marker in text for marker in ("permission denied", "not permitted", "权限不足", "权限")):
        return "权限不足，无法完成普通用户创建。"
    if any(marker in text for marker in ("already exists", "exists already", "已存在")):
        return "目标用户已存在，创建流程停止。"
    if any(marker in text for marker in ("not found", "not recognized", "missing", "缺失", "找不到")):
        return "用户创建能力不可用或系统命令缺失。"
    reasons = evaluation["reasons"]
    if reasons:
        return f"用户创建工具返回失败：{_safe_reason_text(reasons[0])}。"
    return "用户创建工具返回失败，未验证创建成功。"


def _high_risk_failure_reason(context: dict[str, Any]) -> str:
    text = _combined_text({}, context)
    if "/etc" in text:
        return "请求涉及受保护路径 /etc，属于禁止执行的高风险操作。"
    if "protected" in text or "受保护" in text:
        return "请求涉及受保护系统路径，策略要求拒绝执行。"
    return "策略判定为 S3 高风险请求，必须拒绝执行。"


def _generic_failure_reason(evaluation: dict[str, Any]) -> str:
    if evaluation["reasons"]:
        return f"评估器记录原因：{_safe_reason_text(evaluation['reasons'][0])}。"
    return "评估结果缺少成功证据，需要保守记录。"


def _interrupted_step(context: dict[str, Any]) -> str:
    for item in _as_list(context.get("timeline")):
        data = _as_dict(item)
        if _lower(data.get("status")) in {"aborted", "failed", "refused"}:
            return str(data.get("intent") or data.get("step_id") or "前置步骤")
    return "前置步骤"


def _suggested_memory_type(evaluation: dict[str, Any]) -> MemoryType:
    value = evaluation.get("suggested_memory_type")
    if isinstance(value, MemoryType):
        return value if value != MemoryType.NONE else MemoryType.EPISODIC
    try:
        memory_type = MemoryType(str(value))
    except ValueError:
        return MemoryType.EPISODIC
    return memory_type if memory_type != MemoryType.NONE else MemoryType.EPISODIC


def _normalize_evaluation(evaluation: EvaluationResult | Mapping[str, Any] | BaseModel) -> dict[str, Any]:
    data = _as_dict(evaluation)
    return {
        "task_success": bool(data.get("task_success", False)),
        "safety_success": bool(data.get("safety_success", True)),
        "confirmation_ok": bool(data.get("confirmation_ok", True)),
        "needs_reflection": bool(data.get("needs_reflection", False)),
        "experience_candidate": bool(data.get("experience_candidate", False)),
        "suggested_memory_type": data.get("suggested_memory_type", MemoryType.NONE),
        "reasons": [str(item) for item in _as_list(data.get("reasons"))],
        "tags": [str(item) for item in _as_list(data.get("tags"))],
    }


def _as_dict(value: Any) -> dict[str, Any]:
    plain = _to_plain(value)
    return plain if isinstance(plain, dict) else {}


def _as_list(value: Any) -> list[Any]:
    plain = _to_plain(value)
    if plain is None:
        return []
    if isinstance(plain, list):
        return plain
    if isinstance(plain, tuple):
        return list(plain)
    return [plain]


def _to_plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _to_plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    if isinstance(value, tuple):
        return [_to_plain(item) for item in value]
    return value


def _combined_text(evaluation: dict[str, Any], context: dict[str, Any]) -> str:
    parts = [
        " ".join(str(item) for item in evaluation.get("reasons", [])),
        " ".join(str(item) for item in evaluation.get("tags", [])),
        _flatten_text(context),
    ]
    return " ".join(part for part in parts if part).lower()


def _flatten_text(value: Any) -> str:
    plain = _to_plain(value)
    if isinstance(plain, dict):
        return " ".join(_flatten_text(item) for item in plain.values())
    if isinstance(plain, list):
        return " ".join(_flatten_text(item) for item in plain)
    if plain is None:
        return ""
    return str(plain)


def _intent_name(context: dict[str, Any]) -> str:
    parsed_intent = _as_dict(context.get("parsed_intent") or context.get("intent"))
    value = parsed_intent.get("intent") or context.get("intent") or context.get("intent_name")
    return str(value or "").lower()


def _risk_level(context: dict[str, Any]) -> str:
    policy = _as_dict(context.get("policy_decision") or context.get("risk_decision") or context.get("risk"))
    value = policy.get("risk_level") or policy.get("level") or policy.get("risk")
    return str(value or "").strip().upper()


def _deep_get(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        current = _as_dict(current).get(key)
    return current


def _safe_reason_text(reason: str) -> str:
    cleaned = reason.strip().rstrip("。")
    if _DANGEROUS_SUGGESTION_RE.search(cleaned):
        return "失败原因包含敏感操作细节，已按安全原则收敛记录"
    return cleaned[:80]


def _merge_tags(required: list[str], existing: list[str]) -> list[str]:
    tags: list[str] = []
    for tag in [*required, *existing]:
        cleaned = tag.strip()
        if cleaned and cleaned not in tags:
            tags.append(cleaned)
    return tags


def _lower(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _assert_safe_reflection(record: ReflectionRecord) -> None:
    text = " ".join(
        [
            record.summary,
            record.lesson,
            record.failure_reason,
            record.next_time_suggestion,
        ]
    )
    if _DANGEROUS_SUGGESTION_RE.search(text):
        raise ValueError("reflection contains unsafe suggestion")
