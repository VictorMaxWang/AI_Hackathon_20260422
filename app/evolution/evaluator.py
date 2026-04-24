from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from app.models.evolution import EvaluationResult, EvaluationSignal, MemoryType


SUCCESS_STATUSES = {"success", "succeeded", "ok", "completed", "passed"}
FAILURE_STATUSES = {"failed", "failure", "error", "aborted", "timeout", "timed_out"}
REFUSED_STATUSES = {"refused", "denied"}
PENDING_CONFIRMATION = "pending_confirmation"


def evaluate_execution(
    signal: EvaluationSignal | Mapping[str, Any] | BaseModel | None = None,
    **kwargs: Any,
) -> EvaluationResult:
    """Evaluate one execution envelope using deterministic rules only.

    The evaluator is intentionally read-only. It consumes existing orchestrator
    envelopes or equivalent fields and does not influence policy allow/deny.
    """

    data = _normalize_input(signal, kwargs)
    risk_level = _risk_level(data["policy_decision"])
    final_status = _lower(data["final_status"])
    execution_status = _lower(data["execution_status"])
    confirmation_status = _lower(data["confirmation_status"])
    plan_status = _lower(data["plan_status"])

    tool_results = data["tool_results"]
    command_results = data["command_results"]
    timeline = data["timeline"]
    execution_steps = data["execution_steps"]

    executed_any = bool(tool_results or command_results or execution_steps)
    tool_failed = _has_failed_result(tool_results)
    command_failed = _has_failed_result(command_results)
    timeline_failed = _timeline_has_status(timeline, FAILURE_STATUSES)
    timeline_refused = _timeline_has_status(timeline, REFUSED_STATUSES)

    post_check_present, post_check_passed = _post_check_state(data["post_check"])
    timeline_post_check_present, timeline_post_check_passed = _timeline_post_check_state(timeline)
    result_post_check_present, result_post_check_passed = _tool_result_post_check_state(tool_results)
    if not post_check_present and timeline_post_check_present:
        post_check_present = True
        post_check_passed = timeline_post_check_passed
    if not post_check_present and result_post_check_present:
        post_check_present = True
        post_check_passed = result_post_check_passed

    confirmation_mismatch = confirmation_status == "mismatch"
    pending_confirmation = (
        final_status == PENDING_CONFIRMATION
        or execution_status == PENDING_CONFIRMATION
        or plan_status == PENDING_CONFIRMATION
        or confirmation_status == "pending"
    )
    final_success = final_status in SUCCESS_STATUSES or execution_status in SUCCESS_STATUSES
    final_failure = final_status in FAILURE_STATUSES or execution_status in FAILURE_STATUSES
    final_refused = final_status in REFUSED_STATUSES or execution_status in REFUSED_STATUSES
    if not final_status and not execution_status and executed_any:
        final_success = not (tool_failed or command_failed or timeline_failed)

    reasons: list[str] = []
    tags: list[str] = []
    _add_tag(tags, f"risk:{risk_level or 'unknown'}")
    if final_status:
        _add_tag(tags, f"final_status:{final_status}")

    task_success = False
    safety_success = True
    confirmation_ok = True
    needs_reflection = False
    experience_candidate = False
    suggested_memory_type = MemoryType.NONE

    if confirmation_mismatch:
        confirmation_ok = False
        task_success = False
        experience_candidate = True
        suggested_memory_type = MemoryType.EPISODIC
        _add_reason(reasons, "confirmation text mismatch")
        _add_tag(tags, "confirmation_mismatch")
        if executed_any:
            safety_success = False
            needs_reflection = True
            _add_reason(reasons, "tool execution occurred after confirmation mismatch")
            _add_tag(tags, "safety_violation")
        else:
            _add_reason(reasons, "no tool executed after wrong confirmation text")

    elif risk_level == "S3" and final_refused and not executed_any:
        task_success = False
        safety_success = True
        experience_candidate = True
        suggested_memory_type = MemoryType.EPISODIC
        _add_reason(reasons, "S3 request refused before tool execution")
        _add_tag(tags, "s3_refusal")

    elif risk_level == "S3" and executed_any:
        task_success = False
        safety_success = False
        needs_reflection = True
        experience_candidate = True
        suggested_memory_type = MemoryType.EPISODIC
        _add_reason(reasons, "S3 request had tool or command execution")
        _add_tag(tags, "safety_violation")

    elif risk_level in {"S1", "S2"} and pending_confirmation and not executed_any:
        task_success = False
        safety_success = True
        confirmation_ok = True
        needs_reflection = False
        _add_reason(reasons, "write request is waiting for required confirmation")
        _add_tag(tags, "pending_confirmation")

    elif tool_failed or command_failed or final_failure or timeline_failed:
        task_success = False
        needs_reflection = True
        experience_candidate = True
        suggested_memory_type = MemoryType.EPISODIC
        if tool_failed:
            _add_reason(reasons, "tool result failed")
            _add_tag(tags, "tool_failed")
        if command_failed:
            _add_reason(reasons, "command result failed")
            _add_tag(tags, "command_failed")
        if final_failure:
            _add_reason(reasons, "final status is failed")
            _add_tag(tags, "execution_failed")
        if timeline_failed:
            _add_reason(reasons, "timeline contains failed or aborted step")
            _add_tag(tags, "timeline_failed")

    elif post_check_present and not post_check_passed:
        task_success = False
        needs_reflection = True
        experience_candidate = True
        suggested_memory_type = MemoryType.EPISODIC
        _add_reason(reasons, "post-check failed")
        _add_tag(tags, "post_check_failed")

    elif _is_write_intent(data["parsed_intent"], risk_level) and post_check_present and post_check_passed:
        task_success = final_success
        needs_reflection = not final_success
        experience_candidate = not final_success
        suggested_memory_type = MemoryType.EPISODIC if not final_success else MemoryType.NONE
        _add_reason(reasons, "write operation post-check passed")
        _add_tag(tags, "post_check_passed")

    elif risk_level == "S0" and final_success:
        task_success = True
        safety_success = True
        needs_reflection = False
        _add_reason(reasons, "S0 read-only request completed successfully")
        _add_tag(tags, "s0_success")

    elif final_success:
        task_success = True
        _add_reason(reasons, "execution completed successfully")
        _add_tag(tags, "success")

    elif final_refused or timeline_refused:
        task_success = False
        _add_reason(reasons, "execution was refused without a rule-level failure")
        _add_tag(tags, "refused")

    else:
        task_success = False
        _add_reason(reasons, "insufficient success evidence")
        _add_tag(tags, "unknown_outcome")

    if post_check_present and post_check_passed:
        _add_tag(tags, "post_check_passed")

    if final_refused or risk_level == "S3":
        _add_reason(reasons, "automatic workflow promotion blocked for refused or high-risk outcome")
        _add_tag(tags, "auto_promotion_blocked")
    elif suggested_memory_type == MemoryType.EPISODIC and experience_candidate:
        _add_tag(tags, "auto_promotion_blocked")
    elif experience_candidate:
        _add_tag(tags, "requires_governance_review")

    if needs_reflection:
        _add_tag(tags, "needs_reflection")
    if experience_candidate:
        _add_tag(tags, "experience_candidate")
    if safety_success:
        _add_tag(tags, "safety_success")

    return EvaluationResult(
        task_success=task_success,
        safety_success=safety_success,
        post_check_passed=bool(post_check_present and post_check_passed),
        confirmation_ok=confirmation_ok,
        needs_reflection=needs_reflection,
        experience_candidate=experience_candidate,
        suggested_memory_type=suggested_memory_type,
        reasons=reasons,
        tags=tags,
    )


def _normalize_input(
    signal: EvaluationSignal | Mapping[str, Any] | BaseModel | None,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    raw = _to_plain(signal) if signal is not None else {}
    if not isinstance(raw, dict):
        raw = {"final_status": str(raw)}

    for key, value in kwargs.items():
        if value is not None:
            raw[key] = _to_plain(value)

    execution = _as_dict(raw.get("execution"))
    result = _as_dict(raw.get("result"))
    plan = _as_dict(raw.get("plan"))

    parsed_intent = raw.get("parsed_intent", raw.get("intent"))
    policy_decision = raw.get("policy_decision", raw.get("risk_decision", raw.get("risk")))
    tool_results = _as_list(raw.get("tool_results"))
    if not tool_results:
        tool_results = _as_list(execution.get("results"))

    command_results = _as_list(raw.get("command_results"))
    timeline = _as_list(raw.get("timeline"))
    if not timeline:
        result_data = _as_dict(result.get("data"))
        timeline = _as_list(result_data.get("timeline"))

    raw_user_input = raw.get("raw_user_input")
    if raw_user_input is None:
        raw_user_input = _as_dict(parsed_intent).get("raw_user_input")

    final_status = raw.get("final_status") or result.get("status") or execution.get("status")
    confirmation_status = raw.get("confirmation_status") or _infer_confirmation_status(
        plan=plan,
        result=result,
    )

    return {
        "raw_user_input": raw_user_input,
        "parsed_intent": parsed_intent,
        "policy_decision": policy_decision,
        "confirmation_status": confirmation_status,
        "tool_results": tool_results,
        "command_results": command_results,
        "final_status": final_status,
        "post_check": raw.get("post_check"),
        "timeline": [item for item in timeline if isinstance(item, dict)],
        "execution_status": execution.get("status"),
        "execution_steps": _as_list(execution.get("steps")),
        "plan_status": plan.get("status"),
    }


def _infer_confirmation_status(*, plan: dict[str, Any], result: dict[str, Any]) -> str | None:
    result_status = _lower(result.get("status"))
    result_error = _lower(result.get("error"))
    plan_status = _lower(plan.get("status"))

    if result_error == "confirmation_text_mismatch":
        return "mismatch"
    if plan_status == "confirmed":
        return "confirmed"
    if result_status == PENDING_CONFIRMATION or plan_status == PENDING_CONFIRMATION:
        return "pending"
    if result_status == "cancelled" or plan_status == "cancelled":
        return "cancelled"
    return None


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


def _lower(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        value = value.value
    return str(value).strip().lower()


def _risk_level(policy_decision: Any) -> str:
    policy = _as_dict(policy_decision)
    value = policy.get("risk_level") or policy.get("level") or policy.get("risk")
    if hasattr(value, "value"):
        value = value.value
    return str(value or "").strip().upper()


def _has_failed_result(items: list[Any]) -> bool:
    for item in items:
        data = _as_dict(item)
        if data.get("success") is False:
            return True
        if data.get("timed_out") is True:
            return True
        if _lower(data.get("status")) in FAILURE_STATUSES:
            return True
        if "exit_code" in data:
            try:
                if int(data["exit_code"]) != 0:
                    return True
            except (TypeError, ValueError):
                return True
    return False


def _post_check_state(post_check: Any) -> tuple[bool, bool]:
    if post_check is None:
        return False, False
    if isinstance(post_check, bool):
        return True, post_check

    data = _as_dict(post_check)
    if data:
        for key in ("passed", "success", "ok"):
            if key in data:
                return True, bool(data[key])
        status = _lower(data.get("status"))
        if status:
            return True, status in SUCCESS_STATUSES
        return True, False

    status = _lower(post_check)
    if status:
        return True, status in SUCCESS_STATUSES
    return True, False


def _timeline_has_status(timeline: list[dict[str, Any]], statuses: set[str]) -> bool:
    return any(_lower(item.get("status")) in statuses for item in timeline)


def _timeline_post_check_state(timeline: list[dict[str, Any]]) -> tuple[bool, bool]:
    verification_items = [
        item
        for item in timeline
        if str(item.get("intent") or "").startswith("verify_")
        or str(item.get("step_id") or "").endswith("_verify")
    ]
    if not verification_items:
        return False, False
    return True, all(_lower(item.get("status")) in SUCCESS_STATUSES for item in verification_items)


def _tool_result_post_check_state(tool_results: list[Any]) -> tuple[bool, bool]:
    states: list[bool] = []
    for item in tool_results:
        data = _as_dict(_as_dict(item).get("data"))
        for key in ("verified", "verified_absent"):
            if key in data:
                states.append(bool(data[key]))
    if not states:
        return False, False
    return True, all(states)


def _is_write_intent(parsed_intent: Any, risk_level: str) -> bool:
    if risk_level in {"S1", "S2"}:
        return True
    intent = _as_dict(parsed_intent)
    if intent.get("requires_write") is True:
        return True
    intent_name = str(intent.get("intent") or "").lower()
    return any(word in intent_name for word in ("create", "delete", "remove", "write", "modify"))


def _add_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _add_tag(tags: list[str], tag: str) -> None:
    if tag not in tags:
        tags.append(tag)
