from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.policy import RiskLevel
from app.models.result import ExecutionStatus


class MemoryType(StrEnum):
    NONE = "none"
    EPISODIC = "episodic"
    PROCEDURAL = "procedural"
    SEMANTIC = "semantic"


ExperienceMemoryType = MemoryType


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

    @field_validator("tags")
    @classmethod
    def _non_empty_tags(cls, value: list[str]) -> list[str]:
        cleaned = [tag.strip() for tag in value if isinstance(tag, str) and tag.strip()]
        if not cleaned:
            raise ValueError("tags must contain at least one non-empty string")
        return list(dict.fromkeys(cleaned))

    @field_validator("created_at")
    @classmethod
    def _created_at_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


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

    @field_validator("tags")
    @classmethod
    def _non_empty_tags(cls, value: list[str]) -> list[str]:
        cleaned = [tag.strip() for tag in value if isinstance(tag, str) and tag.strip()]
        if not cleaned:
            raise ValueError("tags must contain at least one non-empty string")
        return list(dict.fromkeys(cleaned))

    @field_validator("created_at", "expires_at")
    @classmethod
    def _datetimes_have_timezone(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


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
