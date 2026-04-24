from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.policy import RiskLevel
from app.models.result import ExecutionStatus


def build_experience_dedup_hash(intent: str, summary: str, lesson: str) -> str:
    normalized = "||".join(
        [
            _normalize_hash_text(intent),
            _normalize_hash_text(summary),
            _normalize_hash_text(lesson),
        ]
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalize_hash_text(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _clean_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        value = [value]
    cleaned = [str(item).strip() for item in value if str(item).strip()]
    return list(dict.fromkeys(cleaned))


def _normalize_provenance(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if not isinstance(value, dict):
        return {"sources": _clean_string_list(value)}

    normalized: dict[str, Any] = {}
    for key, item in value.items():
        cleaned_key = str(key).strip()
        if not cleaned_key:
            continue
        if isinstance(item, BaseModel):
            item = item.model_dump(mode="json")
        if isinstance(item, dict):
            nested = _normalize_provenance(item)
            if nested:
                normalized[cleaned_key] = nested
            continue
        if isinstance(item, (list, tuple, set)):
            cleaned_list = _clean_string_list(list(item))
            if cleaned_list:
                normalized[cleaned_key] = cleaned_list
            continue
        if item is None:
            continue
        text = str(item).strip()
        if text:
            normalized[cleaned_key] = text
    return normalized


class MemoryType(StrEnum):
    NONE = "none"
    EPISODIC = "episodic"
    PROCEDURAL = "procedural"
    SEMANTIC = "semantic"


ExperienceMemoryType = MemoryType


class GovernanceStatus(StrEnum):
    QUARANTINE = "quarantine"
    VERIFIED = "verified"
    PROMOTED = "promoted"
    TOMBSTONED = "tombstoned"


class EvaluationSignal(BaseModel):
    """Normalized execution facts consumed by the rule-based evaluator."""

    model_config = ConfigDict(extra="forbid")

    raw_user_input: str | None = None
    parsed_intent: Any = None
    policy_decision: Any = None
    confirmation_status: str | None = None
    tool_results: list[Any] = Field(default_factory=list)
    command_results: list[Any] = Field(default_factory=list)
    final_status: str | None = None
    post_check: Any = None
    timeline: list[dict[str, Any]] = Field(default_factory=list)


class EvaluationResult(BaseModel):
    """Deterministic assessment of one GuardedOps execution result."""

    model_config = ConfigDict(extra="forbid")

    task_success: bool = False
    safety_success: bool = True
    post_check_passed: bool = False
    confirmation_ok: bool = True
    needs_reflection: bool = False
    experience_candidate: bool = False
    suggested_memory_type: MemoryType = MemoryType.NONE
    reasons: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ReflectionRecord(BaseModel):
    """Rule-generated safety lesson derived from one evaluated execution."""

    model_config = ConfigDict(extra="forbid")

    reflection_id: str
    source_request_id: str
    memory_type: MemoryType
    summary: str
    lesson: str
    failure_reason: str
    next_time_suggestion: str
    tags: list[str]
    evidence_refs: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    promote_to_workflow_candidate: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator(
        "reflection_id",
        "source_request_id",
        "summary",
        "lesson",
        "failure_reason",
        "next_time_suggestion",
    )
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("field must be a non-empty string")
        return value.strip()

    @field_validator("evidence_refs")
    @classmethod
    def _clean_string_lists(cls, value: list[str]) -> list[str]:
        return _clean_string_list(value)

    @field_validator("tags")
    @classmethod
    def _non_empty_tags(cls, value: list[str]) -> list[str]:
        cleaned = _clean_string_list(value)
        if not cleaned:
            raise ValueError("tags must contain at least one non-empty string")
        return cleaned

    @field_validator("provenance", mode="before")
    @classmethod
    def _clean_provenance(cls, value: Any) -> dict[str, Any]:
        return _normalize_provenance(value)

    @field_validator("created_at")
    @classmethod
    def _created_at_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @model_validator(mode="after")
    def _ensure_default_provenance(self) -> "ReflectionRecord":
        provenance = dict(self.provenance)
        reflection_ids = _clean_string_list(provenance.get("reflection_ids"))
        if self.reflection_id not in reflection_ids:
            reflection_ids.append(self.reflection_id)
        request_ids = _clean_string_list(provenance.get("request_ids"))
        if self.source_request_id not in request_ids:
            request_ids.append(self.source_request_id)
        sources = _clean_string_list(provenance.get("sources"))
        if "reflection" not in sources:
            sources.append("reflection")
        provenance["reflection_ids"] = reflection_ids
        provenance["request_ids"] = request_ids
        provenance["sources"] = sources
        self.provenance = provenance
        return self


class ExperienceRecord(BaseModel):
    """Persisted Evo-Lite memory distilled from safe reflections only."""

    model_config = ConfigDict(extra="forbid")

    memory_id: str
    session_id: str
    host_id: str
    intent: str
    risk_level: RiskLevel
    status: ExecutionStatus
    memory_type: MemoryType
    summary: str
    lesson: str
    tags: list[str]
    source_request_id: str | None = None
    promoted_to_workflow: bool = False
    provenance: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    dedup_hash: str = ""
    governance_status: GovernanceStatus = GovernanceStatus.QUARANTINE
    decay_score: float = Field(default=0.0, ge=0.0)
    promotion_gate_passed: bool = False
    host_scope: list[str] = Field(default_factory=list)
    session_scope: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None

    @field_validator(
        "memory_id",
        "session_id",
        "host_id",
        "intent",
        "summary",
        "lesson",
    )
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("field must be a non-empty string")
        return value.strip()

    @field_validator("source_request_id")
    @classmethod
    def _optional_non_empty_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("field must be a non-empty string")
        return value.strip()

    @field_validator("evidence_refs", "host_scope", "session_scope")
    @classmethod
    def _clean_string_lists(cls, value: list[str]) -> list[str]:
        return _clean_string_list(value)

    @field_validator("tags")
    @classmethod
    def _non_empty_tags(cls, value: list[str]) -> list[str]:
        cleaned = _clean_string_list(value)
        if not cleaned:
            raise ValueError("tags must contain at least one non-empty string")
        return cleaned

    @field_validator("provenance", mode="before")
    @classmethod
    def _clean_provenance(cls, value: Any) -> dict[str, Any]:
        return _normalize_provenance(value)

    @field_validator("dedup_hash")
    @classmethod
    def _dedup_hash_as_text(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else str(value or "").strip()

    @field_validator("created_at", "expires_at")
    @classmethod
    def _datetimes_have_timezone(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @model_validator(mode="after")
    def _apply_governance_defaults(self) -> "ExperienceRecord":
        if not self.host_scope:
            self.host_scope = [self.host_id]
        elif self.host_id not in self.host_scope:
            self.host_scope.append(self.host_id)

        if not self.session_scope:
            self.session_scope = [self.session_id]
        elif self.session_id not in self.session_scope:
            self.session_scope.append(self.session_id)

        if not self.dedup_hash:
            self.dedup_hash = build_experience_dedup_hash(
                self.intent,
                self.summary,
                self.lesson,
            )

        provenance = dict(self.provenance)
        sources = _clean_string_list(provenance.get("sources"))
        request_ids = _clean_string_list(provenance.get("request_ids"))
        if self.source_request_id and self.source_request_id not in request_ids:
            request_ids.append(self.source_request_id)
        if not sources:
            sources = ["experience_record"]
        provenance["sources"] = sources
        if request_ids:
            provenance["request_ids"] = request_ids
        self.provenance = provenance

        if self.governance_status == GovernanceStatus.TOMBSTONED:
            self.promotion_gate_passed = False
            self.promoted_to_workflow = False
        elif self.governance_status == GovernanceStatus.PROMOTED:
            self.promoted_to_workflow = True
        return self


class WorkflowStep(BaseModel):
    """A declarative workflow step that still requires policy-gated execution."""

    model_config = ConfigDict(extra="forbid")

    step_id: str
    tool_name: str
    intent: str
    description: str
    depends_on: list[str] = Field(default_factory=list)
    condition: str | None = None
    risk_level: RiskLevel
    constraints: dict[str, Any] = Field(default_factory=dict)
    requires_policy: bool = True
    requires_confirmation: bool = False

    @field_validator("step_id", "tool_name", "intent", "description")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("field must be a non-empty string")
        return value


class WorkflowTemplate(BaseModel):
    """Reusable safe workflow template, not an executable workflow instance."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    name: str
    description: str
    risk_level: RiskLevel
    allowed_tools: list[str]
    forbidden_actions: list[str]
    requires_confirmation: bool
    steps: list[WorkflowStep]
    post_checks: list[str]
    tags: list[str]

    @field_validator("workflow_id", "name", "description")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("field must be a non-empty string")
        return value

    @field_validator("allowed_tools", "forbidden_actions", "steps", "post_checks", "tags")
    @classmethod
    def _non_empty_list(cls, value: list[Any]) -> list[Any]:
        if not value:
            raise ValueError("field must be a non-empty list")
        return value

    @model_validator(mode="after")
    def _validate_step_tools_and_ids(self) -> "WorkflowTemplate":
        allowed_tools = set(self.allowed_tools)
        step_ids: set[str] = set()

        for step in self.steps:
            if step.tool_name not in allowed_tools:
                raise ValueError(
                    f"step {step.step_id} uses tool {step.tool_name} outside allowed_tools"
                )
            if step.step_id in step_ids:
                raise ValueError(f"duplicate workflow step_id {step.step_id}")
            step_ids.add(step.step_id)

        for step in self.steps:
            missing_dependencies = [dep for dep in step.depends_on if dep not in step_ids]
            if missing_dependencies:
                raise ValueError(
                    f"step {step.step_id} depends on unknown steps: {missing_dependencies}"
                )

        return self
