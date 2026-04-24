from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel


FAILURE_CONFIRMATION_MISMATCH = "confirmation_mismatch"
FAILURE_ENVIRONMENT_DRIFT = "environment_drift"
FAILURE_PERMISSION_DENIED = "permission_denied"
FAILURE_TARGET_NOT_FOUND = "target_not_found"
FAILURE_TRANSPORT_INTERRUPTED = "transport_interrupted"
FAILURE_UNSUPPORTED_REQUEST = "unsupported_request"
FAILURE_PRECONDITION_FAILED = "precondition_failed"
FAILURE_PARTIAL_SUCCESS = "partial_success"

SUCCESS_STATUSES = {"success", "succeeded", "ok", "completed", "passed"}
FAILURE_STATUSES = {
    "failed",
    "failure",
    "error",
    "aborted",
    "timeout",
    "timed_out",
    "refused",
    "unsupported",
    "skipped",
    "cancelled",
}
WRITE_MARKERS = ("create", "delete", "remove", "write", "modify")


def build_recovery_suggestion(
    *,
    parsed_intent: Mapping[str, Any] | BaseModel | None,
    environment: Mapping[str, Any] | BaseModel | None,
    risk: Mapping[str, Any] | BaseModel | None,
    plan: Mapping[str, Any] | BaseModel | None,
    execution: Mapping[str, Any] | BaseModel | None,
    result: Mapping[str, Any] | BaseModel | None,
    timeline: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    parsed = _as_dict(parsed_intent)
    environment_data = _as_dict(environment)
    risk_data = _as_dict(risk)
    plan_data = _as_dict(plan)
    execution_data = _as_dict(execution)
    result_data = _as_dict(result)
    timeline = [item for item in (timeline or []) if isinstance(item, dict)]

    final_status = _lower(result_data.get("status") or execution_data.get("status") or plan_data.get("status"))
    final_error = _lower(result_data.get("error"))
    if final_status in SUCCESS_STATUSES:
        return None
    if final_status == "pending_confirmation" and final_error not in {
        "confirmation_text_mismatch",
    }:
        return None

    if _is_unhandled_s3_refusal(risk_data, plan_data, result_data, execution_data, timeline):
        return None

    corpus = _text_corpus(
        parsed=parsed,
        environment_data=environment_data,
        risk_data=risk_data,
        plan_data=plan_data,
        execution_data=execution_data,
        result_data=result_data,
        timeline=timeline,
    )
    primary_reason = _primary_reason(plan_data, execution_data, result_data, timeline)
    success_labels = _successful_labels(execution_data, timeline)
    has_write_side_effect = _has_write_side_effect(parsed, execution_data, timeline)
    needs_confirmation = _needs_confirmation_for_followup(parsed, risk_data, execution_data, timeline)

    if _is_confirmation_mismatch(result_data, plan_data, corpus):
        return _build_suggestion(
            failure_type=FAILURE_CONFIRMATION_MISMATCH,
            why_it_failed=(
                "The request was still waiting for an exact confirmation phrase, "
                "but the reply did not match the bound confirmation text, so no new action was executed."
            ),
            safe_next_steps=[
                "Re-submit the original request with the intended scope.",
                "Review the newly issued confirmation and confirm that fresh request instead of replaying the old confirmation.",
            ],
            suggested_readonly_diagnostics=[
                "Review the pending action summary and the exact confirmation text bound to it.",
                "Check whether the target or scope changed while the request was waiting for confirmation.",
            ],
            requires_confirmation_for_recovery=True,
            can_retry_safely=False,
        )

    if _is_environment_drift(result_data, plan_data, timeline, corpus):
        return _build_suggestion(
            failure_type=FAILURE_ENVIRONMENT_DRIFT,
            why_it_failed=(
                "The stored execution context no longer matched the current environment, "
                "target state, or confirmation binding, so the stale plan was refused."
            ),
            safe_next_steps=[
                "Re-probe the current environment and target state in read-only mode before asking for another change.",
                "If the change is still needed, start a new request so the system can create a fresh plan and confirmation.",
            ],
            suggested_readonly_diagnostics=[
                "Review the evidence and timeline for contract drift, checkpoint invalidation, or confirmation token mismatch events.",
                "Compare the latest hostname, current user, connection mode, and target state with the original checkpoint.",
            ],
            requires_confirmation_for_recovery=needs_confirmation,
            can_retry_safely=False,
        )

    if _is_permission_denied(corpus):
        return _build_suggestion(
            failure_type=FAILURE_PERMISSION_DENIED,
            why_it_failed=(
                "Execution hit a permission boundary and the current session was not allowed to continue safely."
                f"{_reason_suffix(primary_reason)}"
            ),
            safe_next_steps=[
                "Confirm whether this action should be performed by the current user at all.",
                "Check whether the current session has the expected sudo capability before submitting another request.",
            ],
            suggested_readonly_diagnostics=[
                "Review the environment snapshot for current_user, is_root, and sudo_available.",
                "Inspect the tool error details and audit trace to see which permission check blocked the request.",
            ],
            requires_confirmation_for_recovery=False,
            can_retry_safely=False,
        )

    if _is_target_not_found(corpus):
        return _build_suggestion(
            failure_type=FAILURE_TARGET_NOT_FOUND,
            why_it_failed=(
                "The requested target could not be located in the observed state."
                f"{_reason_suffix(primary_reason)}"
            ),
            safe_next_steps=[
                "Narrow the request to a specific existing target or bounded scope.",
                "After confirming the target exists, retry the same read-only request or submit a fresh write request if still needed.",
            ],
            suggested_readonly_diagnostics=[
                "Run a bounded read-only lookup for the target and confirm the exact identifier, path, or username.",
                "Review the latest observed target details in the evidence chain and execution results before retrying.",
            ],
            requires_confirmation_for_recovery=False,
            can_retry_safely=not parsed.get("requires_write") and not has_write_side_effect,
        )

    if _is_transport_interrupted(corpus, execution_data):
        return _build_suggestion(
            failure_type=FAILURE_TRANSPORT_INTERRUPTED,
            why_it_failed=(
                "The execution channel was interrupted or timed out before a stable outcome was collected."
                f"{_reason_suffix(primary_reason)}"
            ),
            safe_next_steps=[
                "Re-run read-only diagnostics to confirm the environment is reachable and stable.",
                "Retry only after the connection or executor path looks healthy again.",
            ],
            suggested_readonly_diagnostics=[
                "Review timeout, connection, or executor-failure details in the execution results.",
                "Compare the current environment snapshot with the last successful one before retrying.",
            ],
            requires_confirmation_for_recovery=False,
            can_retry_safely=not has_write_side_effect and not parsed.get("requires_write"),
        )

    if _is_unsupported_request(parsed, plan_data, execution_data):
        return _build_suggestion(
            failure_type=FAILURE_UNSUPPORTED_REQUEST,
            why_it_failed=(
                "The request could not be mapped to a supported guarded workflow with the current planner and tool boundary."
                f"{_reason_suffix(primary_reason)}"
            ),
            safe_next_steps=[
                "Rephrase the request in terms of a supported read-only diagnostic or a whitelisted guarded action.",
                "Reduce the scope so the planner can map it to existing tools and policies.",
            ],
            suggested_readonly_diagnostics=[
                "Review the parsed intent and plan status to see which part of the request remained unsupported.",
                "Compare the request against the currently whitelisted tool capabilities before asking again.",
            ],
            requires_confirmation_for_recovery=False,
            can_retry_safely=False,
        )

    if _is_precondition_failed(corpus, execution_data, timeline):
        return _build_suggestion(
            failure_type=FAILURE_PRECONDITION_FAILED,
            why_it_failed=(
                "The request stopped before safe execution because a required input, bounded parameter, or dependency precondition was not satisfied."
                f"{_reason_suffix(primary_reason)}"
            ),
            safe_next_steps=[
                "Provide the missing bounded details and narrow the target if needed.",
                "Re-run only the relevant read-only diagnostic first, then submit a fresh request.",
            ],
            suggested_readonly_diagnostics=[
                "Review validation, planner, and dependency messages for missing or invalid fields.",
                "Inspect the timeline or result details for the first unmet precondition or aborted dependency.",
            ],
            requires_confirmation_for_recovery=False,
            can_retry_safely=False,
        )

    if _is_partial_success(execution_data, timeline):
        completed_text = ", ".join(success_labels[:3]) if success_labels else "some earlier steps"
        residual_impact = (
            "state may already have changed before the failure"
            if has_write_side_effect
            else "earlier observations were collected and may already be stale"
        )
        return _build_suggestion(
            failure_type=FAILURE_PARTIAL_SUCCESS,
            why_it_failed=(
                "The request did not finish cleanly. "
                f"Completed steps: {completed_text}. "
                f"Residual impact: {residual_impact}. "
                "Review the completed work before deciding on any follow-up."
            ),
            safe_next_steps=[
                "Review the evidence chain and execution timeline before deciding on any follow-up action.",
                "If more change is still needed, submit a fresh, narrower request instead of assuming the unfinished plan can be resumed.",
            ],
            suggested_readonly_diagnostics=[
                "Inspect the last successful step and the first failing step to isolate the boundary.",
                "Use read-only checks to confirm the current target state before asking for another write.",
            ],
            requires_confirmation_for_recovery=needs_confirmation,
            can_retry_safely=False,
        )

    return _build_suggestion(
        failure_type=FAILURE_PRECONDITION_FAILED,
        why_it_failed=(
            "The request could not continue safely with the available inputs and observed state."
            f"{_reason_suffix(primary_reason)}"
        ),
        safe_next_steps=[
            "Narrow the request and re-check the relevant state in read-only mode.",
            "Submit a fresh request only after the missing context is explicit.",
        ],
        suggested_readonly_diagnostics=[
            "Review the plan, result, and timeline details for the first blocking condition.",
            "Confirm the target, scope, and bounded parameters before asking again.",
        ],
        requires_confirmation_for_recovery=False,
        can_retry_safely=False,
    )


def _build_suggestion(
    *,
    failure_type: str,
    why_it_failed: str,
    safe_next_steps: list[str],
    suggested_readonly_diagnostics: list[str],
    requires_confirmation_for_recovery: bool,
    can_retry_safely: bool,
) -> dict[str, Any]:
    return {
        "failure_type": failure_type,
        "why_it_failed": why_it_failed.strip(),
        "safe_next_steps": _unique_nonempty(safe_next_steps),
        "requires_confirmation_for_recovery": bool(requires_confirmation_for_recovery),
        "suggested_readonly_diagnostics": _unique_nonempty(suggested_readonly_diagnostics),
        "can_retry_safely": bool(can_retry_safely),
    }


def _is_confirmation_mismatch(
    result_data: dict[str, Any],
    plan_data: dict[str, Any],
    corpus: str,
) -> bool:
    error = _lower(result_data.get("error"))
    status = _lower(result_data.get("status") or plan_data.get("status"))
    return error == "confirmation_text_mismatch" or (
        status == "pending_confirmation" and "exact confirmation text" in corpus
    )


def _is_environment_drift(
    result_data: dict[str, Any],
    plan_data: dict[str, Any],
    timeline: list[dict[str, Any]],
    corpus: str,
) -> bool:
    error = _lower(result_data.get("error"))
    if error.startswith("confirmation_token_") and error.endswith("_mismatch"):
        return True
    if any(str(item.get("intent") or "").lower() == "contract_drift" for item in timeline):
        return True
    if "contract drift" in corpus:
        return True
    if "checkpoint is missing" in corpus:
        return True
    if "revalidate" in corpus and _lower(result_data.get("status") or plan_data.get("status")) in {"refused", "failed"}:
        return True
    return False


def _is_permission_denied(corpus: str) -> bool:
    return _contains_any(
        corpus,
        (
            "permission denied",
            "operation not permitted",
            "access denied",
            "insufficient privileges",
            "not allowed",
            "requires sudo",
            "sudo is required",
            "sudo unavailable",
        ),
    )


def _is_target_not_found(corpus: str) -> bool:
    return _contains_any(
        corpus,
        (
            "does not exist",
            "not found",
            "no such file",
            "missing target",
            "target is absent",
            "could not be located",
        ),
    )


def _is_transport_interrupted(corpus: str, execution_data: dict[str, Any]) -> bool:
    if _contains_any(
        corpus,
        (
            "timed out",
            "timeout",
            "connection dropped",
            "connection reset",
            "transport interrupted",
            "executor failed",
            "ssh connection",
            "network error",
        ),
    ):
        return True
    for item in _as_list(execution_data.get("results")):
        data = _as_dict(item)
        result_data = _as_dict(data.get("data"))
        if data.get("timed_out") is True or result_data.get("timed_out") is True:
            return True
    return False


def _is_unsupported_request(
    parsed: dict[str, Any],
    plan_data: dict[str, Any],
    execution_data: dict[str, Any],
) -> bool:
    if _lower(plan_data.get("status")) == "unsupported":
        return True
    if str(parsed.get("intent") or "").lower() == "unknown" and not _as_list(plan_data.get("steps")):
        return True
    if _lower(execution_data.get("status")) == "skipped" and not _as_list(execution_data.get("results")):
        reason = _lower(plan_data.get("reason"))
        if "support" in reason or "whitelist" in reason:
            return True
    return False


def _is_precondition_failed(
    corpus: str,
    execution_data: dict[str, Any],
    timeline: list[dict[str, Any]],
) -> bool:
    if _contains_any(
        corpus,
        (
            "is required",
            "must be",
            "invalid",
            "validation",
            "precondition",
            "dependency",
            "missing",
            "out of range",
            "must not be negative",
        ),
    ):
        return True
    if not _has_success(execution_data, timeline) and any(
        _lower(item.get("status")) == "aborted" for item in timeline
    ):
        return True
    return False


def _is_partial_success(execution_data: dict[str, Any], timeline: list[dict[str, Any]]) -> bool:
    return _has_success(execution_data, timeline)


def _is_unhandled_s3_refusal(
    risk_data: dict[str, Any],
    plan_data: dict[str, Any],
    result_data: dict[str, Any],
    execution_data: dict[str, Any],
    timeline: list[dict[str, Any]],
) -> bool:
    risk_level = str(risk_data.get("risk_level") or "").upper()
    final_status = _lower(result_data.get("status") or execution_data.get("status") or plan_data.get("status"))
    if risk_level != "S3" or final_status != "refused":
        return False
    if any(str(item.get("intent") or "").lower() == "contract_drift" for item in timeline):
        return False
    error = _lower(result_data.get("error"))
    return error not in {"confirmation_text_mismatch"}


def _has_success(execution_data: dict[str, Any], timeline: list[dict[str, Any]]) -> bool:
    for item in _as_list(execution_data.get("results")):
        if _as_dict(item).get("success") is True:
            return True
    return any(_lower(item.get("status")) == "success" for item in timeline)


def _successful_labels(execution_data: dict[str, Any], timeline: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    for item in _as_list(execution_data.get("results")):
        data = _as_dict(item)
        if data.get("success") is not True:
            continue
        label = data.get("tool_name")
        if isinstance(label, str) and label.strip():
            labels.append(label.strip())
    for item in timeline:
        if _lower(item.get("status")) != "success":
            continue
        label = item.get("intent") or item.get("step_id")
        if isinstance(label, str) and label.strip():
            labels.append(label.strip())
    return _unique_nonempty(labels)


def _has_write_side_effect(
    parsed: dict[str, Any],
    execution_data: dict[str, Any],
    timeline: list[dict[str, Any]],
) -> bool:
    if parsed.get("requires_write") is not True:
        parsed_requires_write = False
    else:
        parsed_requires_write = True

    for item in _as_list(execution_data.get("results")):
        data = _as_dict(item)
        if data.get("success") is not True:
            continue
        tool_name = str(data.get("tool_name") or "").lower()
        payload = _as_dict(data.get("data"))
        if any(marker in tool_name for marker in WRITE_MARKERS):
            return True
        if payload.get("verified") is True or payload.get("verified_absent") is True:
            return True

    if parsed_requires_write:
        for item in timeline:
            if _lower(item.get("status")) != "success":
                continue
            intent = str(item.get("intent") or "").lower()
            if any(marker in intent for marker in WRITE_MARKERS):
                return True
    return False


def _needs_confirmation_for_followup(
    parsed: dict[str, Any],
    risk_data: dict[str, Any],
    execution_data: dict[str, Any],
    timeline: list[dict[str, Any]],
) -> bool:
    if parsed.get("requires_write") is True:
        return True
    if bool(risk_data.get("requires_confirmation")):
        return True
    if _has_write_side_effect(parsed, execution_data, timeline):
        return True
    return False


def _primary_reason(
    plan_data: dict[str, Any],
    execution_data: dict[str, Any],
    result_data: dict[str, Any],
    timeline: list[dict[str, Any]],
) -> str | None:
    candidates: list[Any] = [
        result_data.get("error"),
        _as_dict(result_data.get("data")).get("reason"),
        plan_data.get("reason"),
    ]
    for item in reversed(_as_list(execution_data.get("results"))):
        payload = _as_dict(item)
        candidates.extend([payload.get("error"), _as_dict(payload.get("data")).get("reason")])
    for item in reversed(timeline):
        candidates.append(item.get("result_summary"))

    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _reason_suffix(reason: str | None) -> str:
    if not reason:
        return ""
    return f" Primary evidence: {reason}."


def _text_corpus(
    *,
    parsed: dict[str, Any],
    environment_data: dict[str, Any],
    risk_data: dict[str, Any],
    plan_data: dict[str, Any],
    execution_data: dict[str, Any],
    result_data: dict[str, Any],
    timeline: list[dict[str, Any]],
) -> str:
    values: list[str] = []
    values.extend(_string_values(parsed))
    values.extend(_string_values(environment_data))
    values.extend(_string_values(risk_data))
    values.extend(_string_values(plan_data))
    values.extend(_string_values(execution_data))
    values.extend(_string_values(result_data))
    for item in timeline:
        values.extend(_string_values(item))
    return " | ".join(values).lower()


def _string_values(value: Any) -> list[str]:
    plain = _to_plain(value)
    if isinstance(plain, str):
        return [plain]
    if isinstance(plain, Mapping):
        values: list[str] = []
        for item in plain.values():
            values.extend(_string_values(item))
        return values
    if isinstance(plain, list):
        values: list[str] = []
        for item in plain:
            values.extend(_string_values(item))
        return values
    return []


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _unique_nonempty(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if normalized and normalized not in cleaned:
            cleaned.append(normalized)
    return cleaned


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


def _lower(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        value = value.value
    return str(value).strip().lower()
