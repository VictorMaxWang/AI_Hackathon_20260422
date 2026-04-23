from __future__ import annotations

import warnings
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from app.agent.memory import AgentMemory
from app.evolution.evaluator import evaluate_execution
from app.evolution.experience_store import ExperienceStore
from app.evolution.reflection import generate_reflection
from app.models.evolution import EvaluationResult, ExperienceRecord, ReflectionRecord
from app.models.policy import RiskLevel
from app.models.result import ExecutionStatus


def apply_evo_lite_hook(
    envelope: Mapping[str, Any],
    *,
    memory: AgentMemory | None = None,
    experience_store: ExperienceStore | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    enriched = dict(envelope)
    evo_lite = _empty_evo_lite_payload()

    if not enabled:
        enriched["evo_lite"] = evo_lite
        return enriched

    try:
        evaluation = evaluate_execution(enriched)
    except Exception as exc:
        _warn("evaluation_failed", exc)
        evo_lite["warning"] = "evaluation_failed"
        enriched["evo_lite"] = evo_lite
        return enriched

    evo_lite["evaluation"] = evaluation.model_dump(mode="json")
    if not (evaluation.needs_reflection or evaluation.experience_candidate):
        enriched["evo_lite"] = evo_lite
        return enriched

    source_request_id = _source_request_id()
    try:
        reflection = generate_reflection(
            evaluation,
            source_request_id=source_request_id,
            execution_context=enriched,
        )
    except Exception as exc:
        _warn("reflection_failed", exc)
        evo_lite["warning"] = "reflection_failed"
        enriched["evo_lite"] = evo_lite
        return enriched

    evo_lite["reflection_summary"] = reflection.summary
    if experience_store is None:
        enriched["evo_lite"] = evo_lite
        return enriched

    try:
        record = _experience_record_from_reflection(
            reflection,
            envelope=enriched,
            evaluation=evaluation,
            memory=memory,
        )
        experience_store.add(record)
    except Exception as exc:
        _warn("experience_store_write_failed", exc)
        evo_lite["warning"] = "experience_store_write_failed"
        enriched["evo_lite"] = evo_lite
        return enriched

    evo_lite["experience_saved"] = True
    evo_lite["memory_id"] = record.memory_id
    enriched["evo_lite"] = evo_lite
    return enriched


def _experience_record_from_reflection(
    reflection: ReflectionRecord,
    *,
    envelope: Mapping[str, Any],
    evaluation: EvaluationResult,
    memory: AgentMemory | None,
) -> ExperienceRecord:
    return ExperienceRecord(
        memory_id=f"memory-{uuid4().hex[:12]}",
        session_id=_session_id(memory),
        host_id=_host_id(envelope),
        intent=_intent_name(envelope),
        risk_level=_risk_level(envelope),
        status=_experience_status(envelope, evaluation),
        memory_type=reflection.memory_type,
        summary=reflection.summary,
        lesson=reflection.lesson,
        tags=reflection.tags,
        source_request_id=reflection.source_request_id,
        promoted_to_workflow=reflection.promote_to_workflow_candidate,
        created_at=reflection.created_at,
        expires_at=None,
    )


def _empty_evo_lite_payload() -> dict[str, Any]:
    return {
        "evaluation": None,
        "reflection_summary": None,
        "experience_saved": False,
        "memory_id": None,
    }


def _session_id(memory: AgentMemory | None) -> str:
    session_id = getattr(memory, "session_id", None)
    if isinstance(session_id, str) and session_id.strip():
        return session_id.strip()
    return "default"


def _host_id(envelope: Mapping[str, Any]) -> str:
    environment = _as_dict(envelope.get("environment"))
    snapshot = _as_dict(environment.get("snapshot"))
    hostname = snapshot.get("hostname")
    if isinstance(hostname, str) and hostname.strip():
        return hostname.strip()
    return "unknown"


def _intent_name(envelope: Mapping[str, Any]) -> str:
    intent = _as_dict(envelope.get("intent"))
    value = intent.get("intent")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "unknown"


def _risk_level(envelope: Mapping[str, Any]) -> RiskLevel:
    risk = _as_dict(envelope.get("risk"))
    value = risk.get("risk_level")
    try:
        return RiskLevel(str(value))
    except ValueError:
        return RiskLevel.S0


def _experience_status(
    envelope: Mapping[str, Any],
    evaluation: EvaluationResult,
) -> ExecutionStatus:
    result_status = _status_value(_as_dict(envelope.get("result")).get("status"))
    execution_status = _status_value(_as_dict(envelope.get("execution")).get("status"))
    plan_status = _status_value(_as_dict(envelope.get("plan")).get("status"))
    risk_level = _status_value(_as_dict(envelope.get("risk")).get("risk_level"))
    allow = _as_dict(envelope.get("risk")).get("allow")

    if evaluation.task_success and evaluation.safety_success:
        return ExecutionStatus.SUCCESS
    if "pending_confirmation" in {result_status, execution_status, plan_status}:
        return ExecutionStatus.PENDING_CONFIRMATION
    if "refused" in {result_status, execution_status, plan_status}:
        return ExecutionStatus.REFUSED
    if risk_level == "s3" and allow is False:
        return ExecutionStatus.REFUSED
    return ExecutionStatus.FAILED


def _status_value(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        value = value.value
    return str(value).strip().lower()


def _source_request_id() -> str:
    return f"req-{uuid4().hex[:12]}"


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _warn(code: str, exc: Exception) -> None:
    warnings.warn(f"Evo-Lite {code}: {exc}", UserWarning, stacklevel=2)
