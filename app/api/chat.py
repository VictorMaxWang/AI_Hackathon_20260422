from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agent import ReadonlyOrchestrator
from app.executors import BaseExecutor, LocalExecutor


class Utf8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"


router = APIRouter()


EXPLANATION_SECTION_DEFINITIONS = (
    ("intent_normalized", "请求归一化"),
    ("plan_summary", "计划摘要"),
    ("risk_hits", "风险命中"),
    ("scope_preview", "范围预览"),
    ("confirmation_basis", "确认依据"),
    ("execution_evidence", "执行证据"),
    ("result_assertion", "结果断言"),
    ("residual_risks_or_next_step", "残余风险 / 下一步"),
)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_user_input: str = Field(min_length=1)


def get_executor() -> BaseExecutor:
    return LocalExecutor()


def get_orchestrator(
    http_request: Request,
    executor: BaseExecutor = Depends(get_executor),
) -> ReadonlyOrchestrator:
    orchestrator = getattr(http_request.app.state, "chat_orchestrator", None)
    if orchestrator is None:
        orchestrator = ReadonlyOrchestrator(executor)
        http_request.app.state.chat_orchestrator = orchestrator
    return orchestrator


@router.post("/api/chat", response_class=Utf8JSONResponse)
def chat(
    request: ChatRequest,
    orchestrator: ReadonlyOrchestrator = Depends(get_orchestrator),
) -> dict[str, Any]:
    envelope = dict(orchestrator.run(request.raw_user_input))
    envelope["operator_panel"] = _build_operator_panel_view(
        envelope,
        raw_user_input=request.raw_user_input,
    )
    return envelope


def _build_operator_panel_view(
    payload: dict[str, Any],
    *,
    raw_user_input: str,
) -> dict[str, Any]:
    intent = _as_dict(payload.get("intent"))
    risk = _as_dict(payload.get("risk"))
    plan = _as_dict(payload.get("plan"))
    execution = _as_dict(payload.get("execution"))
    result = _as_dict(payload.get("result"))
    recovery = _as_dict(payload.get("recovery"))
    explanation_card = _as_dict(payload.get("explanation_card"))
    evidence_chain = _as_dict(payload.get("evidence_chain"))
    blast_radius_preview = _as_dict(payload.get("blast_radius_preview"))
    policy_simulator = _as_dict(payload.get("policy_simulator"))
    timeline = _as_list(payload.get("timeline"))
    environment = _as_dict(payload.get("environment"))
    status = _first_text(
        result.get("status"),
        plan.get("status"),
        execution.get("status"),
        "unknown",
    )
    risk_level = _first_text(risk.get("risk_level"), "unknown")

    return {
        "user_input": _first_text(raw_user_input, intent.get("raw_user_input"), "-"),
        "status": status,
        "risk_level": risk_level,
        "risk_reasons": _string_list(risk.get("reasons")),
        "confidence": _normalize_confidence(intent.get("confidence")),
        "confidence_source": _first_text(
            intent.get("confidence_source"),
            risk.get("confidence_source"),
            result.get("confidence_source"),
        ),
        "blast_radius_preview": _build_blast_radius_preview(blast_radius_preview),
        "policy_simulator": _build_policy_simulator(policy_simulator),
        "explanation_sections": _build_explanation_sections(explanation_card),
        "timeline_entries": _build_timeline_entries(
            timeline=timeline,
            evidence_chain=evidence_chain,
        ),
        "preflight_items": _build_preflight_items(
            intent=intent,
            risk=risk,
            plan=plan,
            execution=execution,
            result=result,
            environment=environment,
            evidence_chain=evidence_chain,
        ),
        "confirmation": _build_confirmation_block(
            risk=risk,
            plan=plan,
            execution=execution,
            result=result,
            explanation_card=explanation_card,
            evidence_chain=evidence_chain,
        ),
        "refusal": _build_refusal_block(
            risk=risk,
            plan=plan,
            result=result,
            explanation_card=explanation_card,
        ),
        "recovery": _build_recovery_block(recovery),
        "residual_next_step": _build_residual_block(explanation_card),
    }


def _build_explanation_sections(explanation_card: dict[str, Any]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for key, label in EXPLANATION_SECTION_DEFINITIONS:
        section = _as_dict(explanation_card.get(key))
        sections.append(
            {
                "key": key,
                "label": label,
                "summary": _first_text(section.get("summary"), "-"),
                "evidence_refs": _string_list(section.get("evidence_refs")),
            }
        )
    return sections


def _build_timeline_entries(
    *,
    timeline: list[Any],
    evidence_chain: dict[str, Any],
) -> list[dict[str, Any]]:
    entries = [item for item in timeline if isinstance(item, dict)]
    if entries:
        return [_timeline_entry_from_narrative(index, item) for index, item in enumerate(entries, start=1)]

    evidence_events = [
        item
        for item in _as_list(evidence_chain.get("events"))
        if isinstance(item, dict)
    ]
    return [
        _timeline_entry_from_evidence(index, item)
        for index, item in enumerate(evidence_events, start=1)
    ]


def _timeline_entry_from_narrative(index: int, item: dict[str, Any]) -> dict[str, Any]:
    intent = _first_text(item.get("intent"), item.get("step_id"), f"step_{index}")
    status = _first_text(item.get("status"), "unknown")
    risk_level = _first_text(item.get("risk"))
    summary = _first_text(
        item.get("result_summary"),
        f"{intent} -> {status}",
    )
    return {
        "source": "timeline",
        "index": index,
        "title": intent,
        "summary": summary,
        "status": status,
        "severity": _severity_for_timeline_status(status),
        "stage": _first_text(item.get("intent"), "timeline"),
        "risk_level": risk_level,
        "timestamp": _first_text(item.get("timestamp")),
        "evidence_refs": _string_list(item.get("refs")),
        "step_id": _first_text(item.get("step_id")),
    }


def _timeline_entry_from_evidence(index: int, item: dict[str, Any]) -> dict[str, Any]:
    details = _as_dict(item.get("details"))
    title = _first_text(item.get("title"), item.get("stage"), f"event_{index}")
    return {
        "source": "evidence",
        "index": index,
        "title": title,
        "summary": _evidence_event_summary(item),
        "status": _first_text(details.get("status"), details.get("result_status")),
        "severity": _first_text(item.get("severity"), "info"),
        "stage": _first_text(item.get("stage"), "evidence"),
        "risk_level": _first_text(
            details.get("risk_level"),
            details.get("risk"),
        ),
        "timestamp": _first_text(item.get("timestamp")),
        "evidence_refs": _string_list(item.get("refs")),
        "step_id": _first_text(details.get("step_id")),
    }


def _build_preflight_items(
    *,
    intent: dict[str, Any],
    risk: dict[str, Any],
    plan: dict[str, Any],
    execution: dict[str, Any],
    result: dict[str, Any],
    environment: dict[str, Any],
    evidence_chain: dict[str, Any],
) -> list[dict[str, Any]]:
    events = [
        item
        for item in _as_list(evidence_chain.get("events"))
        if isinstance(item, dict)
    ]
    parse_event = _first_event_by_stage(events, "parse")
    policy_event = _first_event_by_stage(events, "policy")
    confirmation_event = _first_event_by_stage(events, "confirmation")

    plan_status = _first_text(plan.get("status"), "unknown")
    result_status = _first_text(result.get("status"), execution.get("status"), "unknown")
    requires_confirmation = bool(risk.get("requires_confirmation"))
    environment_status = _first_text(environment.get("status"), "not_collected")
    risk_level = _first_text(risk.get("risk_level"), "unknown")

    return [
        {
            "key": "intent_parsed",
            "label": "Intent parsed",
            "status": "ready" if parse_event else "not_available",
            "summary": (
                f"识别为 {intent_name}。"
                if (intent_name := _first_text(intent.get("intent")))
                else "未发现 parse evidence。"
            ),
            "evidence_refs": _event_refs(parse_event),
        },
        {
            "key": "policy_bound",
            "label": "Policy bound",
            "status": "ready" if policy_event else "not_available",
            "summary": (
                f"绑定到 {risk_level} 风险决策。"
                if policy_event
                else "未发现 policy evidence。"
            ),
            "evidence_refs": _event_refs(policy_event),
        },
        {
            "key": "plan_ready",
            "label": "Plan ready",
            "status": _plan_preflight_status(plan_status),
            "summary": f"计划状态：{plan_status}。",
            "evidence_refs": _event_refs(_first_event_by_stage(events, "plan")),
        },
        {
            "key": "confirmation_gate",
            "label": "Confirmation gate",
            "status": _confirmation_preflight_status(
                requires_confirmation=requires_confirmation,
                plan_status=plan_status,
                result_status=result_status,
                result_error=_first_text(result.get("error")),
            ),
            "summary": _confirmation_preflight_summary(
                requires_confirmation=requires_confirmation,
                plan_status=plan_status,
                result_status=result_status,
                result_error=_first_text(result.get("error")),
                confirmation_text=_first_text(
                    result.get("confirmation_text"),
                    risk.get("confirmation_text"),
                ),
            ),
            "evidence_refs": _event_refs(confirmation_event),
        },
        {
            "key": "environment_ready",
            "label": "Environment ready",
            "status": _environment_preflight_status(environment_status),
            "summary": f"环境状态：{environment_status}。",
            "evidence_refs": [],
        },
    ]


def _build_confirmation_block(
    *,
    risk: dict[str, Any],
    plan: dict[str, Any],
    execution: dict[str, Any],
    result: dict[str, Any],
    explanation_card: dict[str, Any],
    evidence_chain: dict[str, Any],
) -> dict[str, Any]:
    requires_confirmation = bool(risk.get("requires_confirmation"))
    plan_status = _first_text(plan.get("status"), "unknown")
    result_status = _first_text(result.get("status"), execution.get("status"), "unknown")
    result_error = _first_text(result.get("error"))
    confirmation_text = _first_text(
        result.get("confirmation_text"),
        risk.get("confirmation_text"),
    )
    section = _as_dict(explanation_card.get("confirmation_basis"))
    events = [
        item
        for item in _as_list(_as_dict(evidence_chain).get("events"))
        if isinstance(item, dict)
    ]
    return {
        "required": requires_confirmation,
        "status": _confirmation_panel_status(
            requires_confirmation=requires_confirmation,
            plan_status=plan_status,
            result_status=result_status,
            result_error=result_error,
        ),
        "text": confirmation_text,
        "summary": _first_text(section.get("summary"), "当前请求无确认依据。"),
        "evidence_refs": _string_list(section.get("evidence_refs"))
        or _event_refs(_first_event_by_stage(events, "confirmation")),
    }


def _build_refusal_block(
    *,
    risk: dict[str, Any],
    plan: dict[str, Any],
    result: dict[str, Any],
    explanation_card: dict[str, Any],
) -> dict[str, Any]:
    status = _first_text(result.get("status"), plan.get("status"), "unknown")
    section = _as_dict(explanation_card.get("risk_hits"))
    return {
        "is_refused": status == "refused" or _first_text(plan.get("status")) == "refused",
        "reason": _first_text(result.get("error"), plan.get("reason"), section.get("summary")),
        "safe_alternative": _first_text(risk.get("safe_alternative")),
        "evidence_refs": _string_list(section.get("evidence_refs")),
    }


def _build_blast_radius_preview(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "scenario": _first_text(payload.get("scenario"), "general"),
        "summary": _first_text(payload.get("summary"), "-"),
        "facts": _normalize_labeled_items(payload.get("facts")),
        "impacts": _normalize_impacts(payload.get("impacts")),
        "protected_paths": _string_list(payload.get("protected_paths")),
        "notes": _string_list(payload.get("notes")),
    }


def _build_policy_simulator(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "risk_level": _first_text(payload.get("risk_level"), "unknown"),
        "allow": bool(payload.get("allow")),
        "requires_confirmation": bool(payload.get("requires_confirmation")),
        "policy_version": _first_text(payload.get("policy_version"), "unknown"),
        "matched_rules": _normalize_matched_rules(payload.get("matched_rules")),
        "denied_because": _string_list(payload.get("denied_because")),
        "requires_confirmation_because": _string_list(
            payload.get("requires_confirmation_because")
        ),
        "scope_summary": _first_text(payload.get("scope_summary"), "-"),
        "target_fingerprint": _first_text(payload.get("target_fingerprint"), "-"),
        "safe_alternative": _first_text(payload.get("safe_alternative")),
    }


def _build_recovery_block(recovery: dict[str, Any]) -> dict[str, Any]:
    if not recovery:
        return {
            "available": False,
            "failure_type": None,
            "why_it_failed": "",
            "safe_next_steps": [],
            "suggested_readonly_diagnostics": [],
            "requires_confirmation_for_recovery": False,
            "can_retry_safely": False,
        }

    return {
        "available": True,
        "failure_type": _first_text(recovery.get("failure_type")),
        "why_it_failed": _first_text(recovery.get("why_it_failed"), "-"),
        "safe_next_steps": _string_list(recovery.get("safe_next_steps")),
        "suggested_readonly_diagnostics": _string_list(
            recovery.get("suggested_readonly_diagnostics")
        ),
        "requires_confirmation_for_recovery": bool(
            recovery.get("requires_confirmation_for_recovery")
        ),
        "can_retry_safely": bool(recovery.get("can_retry_safely")),
    }


def _build_residual_block(explanation_card: dict[str, Any]) -> dict[str, Any]:
    section = _as_dict(explanation_card.get("residual_risks_or_next_step"))
    return {
        "summary": _first_text(section.get("summary"), "-"),
        "evidence_refs": _string_list(section.get("evidence_refs")),
    }


def _normalize_labeled_items(value: Any) -> list[dict[str, str]]:
    items = [item for item in _as_list(value) if isinstance(item, dict)]
    normalized: list[dict[str, str]] = []
    for item in items:
        normalized.append(
            {
                "label": _first_text(item.get("label"), "item"),
                "value": _first_text(item.get("value"), "-"),
            }
        )
    return normalized


def _normalize_impacts(value: Any) -> list[dict[str, str]]:
    items = [item for item in _as_list(value) if isinstance(item, dict)]
    normalized: list[dict[str, str]] = []
    for item in items:
        normalized.append(
            {
                "label": _first_text(item.get("label"), "impact"),
                "value": _first_text(item.get("value"), "-"),
                "precision": _first_text(item.get("precision"), "conservative"),
            }
        )
    return normalized


def _normalize_matched_rules(value: Any) -> list[dict[str, str]]:
    items = [item for item in _as_list(value) if isinstance(item, dict)]
    normalized: list[dict[str, str]] = []
    for item in items:
        normalized.append(
            {
                "rule_id": _first_text(item.get("rule_id"), "unknown.rule"),
                "outcome": _first_text(item.get("outcome"), "deny"),
                "summary": _first_text(item.get("summary"), "-"),
            }
        )
    return normalized


def _plan_preflight_status(plan_status: str) -> str:
    lowered = plan_status.lower()
    if lowered in {"refused", "unsupported", "failed"}:
        return "blocked"
    if lowered in {"pending_confirmation", "cancelled"}:
        return "pending"
    if lowered in {"unknown", ""}:
        return "not_available"
    return "ready"


def _confirmation_preflight_status(
    *,
    requires_confirmation: bool,
    plan_status: str,
    result_status: str,
    result_error: str,
) -> str:
    if not requires_confirmation:
        return "not_required"
    lowered_error = result_error.lower()
    if lowered_error == "confirmation_text_mismatch" or lowered_error.startswith(
        "confirmation_token_"
    ):
        return "blocked"
    lowered_plan = plan_status.lower()
    lowered_result = result_status.lower()
    if lowered_result == "pending_confirmation" or lowered_plan == "pending_confirmation":
        return "pending"
    if lowered_plan == "confirmed" or lowered_result in {"success", "completed"}:
        return "ready"
    if lowered_result in {"refused", "failed", "cancelled"}:
        return "blocked"
    return "pending"


def _confirmation_preflight_summary(
    *,
    requires_confirmation: bool,
    plan_status: str,
    result_status: str,
    result_error: str,
    confirmation_text: str,
) -> str:
    if not requires_confirmation:
        return "该请求不需要额外确认。"
    if result_error == "confirmation_text_mismatch" or result_error.startswith(
        "confirmation_token_"
    ):
        return "确认绑定失配，当前请求未继续执行。"
    if result_status == "pending_confirmation" or plan_status == "pending_confirmation":
        if confirmation_text:
            return f"等待精确确认：{confirmation_text}"
        return "等待精确确认后继续。"
    if plan_status == "confirmed" or result_status in {"success", "completed"}:
        return "确认门已满足。"
    return "确认状态待定。"


def _confirmation_panel_status(
    *,
    requires_confirmation: bool,
    plan_status: str,
    result_status: str,
    result_error: str,
) -> str:
    if not requires_confirmation:
        return "not_required"
    lowered_error = result_error.lower()
    if lowered_error == "confirmation_text_mismatch" or lowered_error.startswith(
        "confirmation_token_"
    ):
        return "mismatch"
    lowered_result = result_status.lower()
    lowered_plan = plan_status.lower()
    if lowered_result == "pending_confirmation" or lowered_plan == "pending_confirmation":
        return "pending_confirmation"
    if lowered_result == "cancelled" or lowered_plan == "cancelled":
        return "cancelled"
    if lowered_plan == "confirmed" or lowered_result in {"success", "completed"}:
        return "confirmed"
    return "required"


def _environment_preflight_status(environment_status: str) -> str:
    lowered = environment_status.lower()
    if lowered == "ok":
        return "ready"
    if lowered == "error":
        return "blocked"
    return "not_available"


def _evidence_event_summary(event: dict[str, Any]) -> str:
    stage = _first_text(event.get("stage"), "evidence")
    title = _first_text(event.get("title"), stage)
    details = _as_dict(event.get("details"))
    for key in (
        "result_summary",
        "summary",
        "why_it_failed",
        "error",
        "status",
        "result_status",
        "tool_name",
        "intent",
    ):
        value = _first_text(details.get(key))
        if value:
            return value
    return f"{stage}: {title}"


def _severity_for_timeline_status(status: str) -> str:
    lowered = status.lower()
    if lowered in {"failed", "refused", "aborted"}:
        return "critical"
    if lowered in {"pending_confirmation", "cancelled", "skipped"}:
        return "warning"
    return "info"


def _first_event_by_stage(events: list[dict[str, Any]], stage: str) -> dict[str, Any] | None:
    for item in events:
        if _first_text(item.get("stage")).lower() == stage:
            return item
    return None


def _event_refs(event: dict[str, Any] | None) -> list[str]:
    if event is None:
        return []
    refs = _string_list(event.get("refs"))
    event_id = _first_text(event.get("event_id"))
    if event_id and event_id not in refs:
        refs.insert(0, event_id)
    return refs


def _normalize_confidence(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if confidence < 0 or confidence > 1:
        return None
    return confidence


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _string_list(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    cleaned: list[str] = []
    for item in items:
        if item is None:
            continue
        text = str(item).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""
