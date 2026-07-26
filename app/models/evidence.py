from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.result import STATE_UNKNOWN_STATUS


BINDING_PROBE_REASON = "readonly_env_probe_for_confirmation_binding"
BINDING_PROBE_TOOL_NAME = "env_probe_tool"
NO_SIDE_EFFECTS = "no_side_effects"
STATE_UNKNOWN = "state_unknown"
WRITE_TOOL_MARKERS = ("create", "delete", "remove", "write", "modify", "chmod", "chown")


class EvidenceStage(StrEnum):
    PARSE = "parse"
    PLAN = "plan"
    POLICY = "policy"
    CONFIRMATION = "confirmation"
    TOOL_CALL = "tool_call"
    POST_CHECK = "post_check"
    RECOVERY = "recovery"
    RESULT = "result"


class EvidenceSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EvidenceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    stage: EvidenceStage
    title: str
    details: dict[str, Any] = Field(default_factory=dict)
    severity: EvidenceSeverity = EvidenceSeverity.INFO
    refs: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=_utc_now)

    @field_validator("event_id", "title")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("field must be a non-empty string")
        return value.strip()

    @field_validator("refs")
    @classmethod
    def _clean_refs(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        return list(dict.fromkeys(cleaned))


class StateAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assertion_id: str
    name: str
    passed: bool
    evidence_refs: list[str] = Field(default_factory=list)
    summary: str

    @field_validator("assertion_id", "name", "summary")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("field must be a non-empty string")
        return value.strip()

    @field_validator("evidence_refs")
    @classmethod
    def _clean_refs(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        return list(dict.fromkeys(cleaned))


class ExplanationSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    evidence_refs: list[str] = Field(default_factory=list)

    @field_validator("summary")
    @classmethod
    def _summary_as_text(cls, value: str) -> str:
        return value if isinstance(value, str) else str(value or "")

    @field_validator("evidence_refs")
    @classmethod
    def _clean_refs(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        return list(dict.fromkeys(cleaned))


class ExplanationCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_normalized: ExplanationSection = Field(default_factory=ExplanationSection)
    plan_summary: ExplanationSection = Field(default_factory=ExplanationSection)
    risk_hits: ExplanationSection = Field(default_factory=ExplanationSection)
    scope_preview: ExplanationSection = Field(default_factory=ExplanationSection)
    confirmation_basis: ExplanationSection = Field(default_factory=ExplanationSection)
    execution_evidence: ExplanationSection = Field(default_factory=ExplanationSection)
    result_assertion: ExplanationSection = Field(default_factory=ExplanationSection)
    residual_risks_or_next_step: ExplanationSection = Field(default_factory=ExplanationSection)


class EvidenceChain(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[EvidenceEvent] = Field(default_factory=list)
    state_assertions: list[StateAssertion] = Field(default_factory=list)


class ToolCallRecord(BaseModel):
    """One tool invocation as the evidence chain must report it."""

    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=1)
    tool_name: str = "unknown"
    args: dict[str, Any] = Field(default_factory=dict)
    success: bool | None = None
    error: str | None = None
    started_at: Any = None
    finished_at: Any = None
    step_id: str | None = None
    result_recorded: bool = True

    def to_event_details(self) -> dict[str, Any]:
        details = self.model_dump(mode="json", exclude={"step_id"})
        if self.result_recorded:
            details.pop("result_recorded")
        return details


def tool_call_records(execution: Mapping[str, Any] | None) -> list[ToolCallRecord]:
    """Derive the evidence view of tool calls from one execution record.

    ``execution.steps`` records invocations and ``execution.results`` records
    returns. The evidence chain is a derived view of both instead of a third
    independently populated list: a step whose result never arrived still
    happened, so it stays in the chain with ``result_recorded=False`` rather
    than making the chain claim that no tool ran.
    """

    execution_data = _as_dict(execution)
    steps = [item for item in _as_list(execution_data.get("steps")) if isinstance(item, dict)]
    results = [item for item in _as_list(execution_data.get("results")) if isinstance(item, dict)]

    records: list[ToolCallRecord] = []
    for index in range(max(len(steps), len(results))):
        step = steps[index] if index < len(steps) else {}
        result = results[index] if index < len(results) else None
        step_id = step.get("step_id")
        records.append(
            ToolCallRecord(
                order=index + 1,
                tool_name=str(
                    step.get("tool_name") or (result or {}).get("tool_name") or "unknown"
                ),
                args=dict(step.get("args") or {}),
                success=(result or {}).get("success") if result is not None else None,
                error=_text_or_none((result or {}).get("error") if result is not None else None)
                or _text_or_none(step.get("error")),
                started_at=step.get("started_at"),
                finished_at=step.get("finished_at"),
                step_id=step_id if isinstance(step_id, str) and step_id else None,
                result_recorded=result is not None,
            )
        )
    return records


def crash_state_disposition(execution: Mapping[str, Any] | None) -> str:
    """Classify what a crashed turn is allowed to claim about the target state.

    ``NO_SIDE_EFFECTS`` is only returned when nothing that can change the
    target was invoked. Once a write tool has been entered, its outcome is not
    knowable from a crashed turn, so the turn must report ``STATE_UNKNOWN``
    instead of asserting a refusal.
    """

    records = tool_call_records(execution)
    if not records:
        return NO_SIDE_EFFECTS
    for record in records:
        tool_name = record.tool_name.lower()
        if any(marker in tool_name for marker in WRITE_TOOL_MARKERS):
            return STATE_UNKNOWN
    return NO_SIDE_EFFECTS


def is_state_unknown(
    result: Mapping[str, Any] | None,
    execution: Mapping[str, Any] | None = None,
) -> bool:
    """Report whether the turn ended without knowing the target state.

    A crash after a tool already ran must never be described as refused: the
    side effect may have been applied, so the only honest outcome is unknown.
    """

    result_data = _as_dict(result)
    if _lower(result_data.get("status")) == STATE_UNKNOWN_STATUS:
        return True
    if result_data.get("state_unknown") is True:
        return True
    return _lower(_as_dict(execution).get("status")) == STATE_UNKNOWN_STATUS


def final_outcome_assertion_summary(result: Mapping[str, Any] | None) -> str:
    """Single source of truth for how the final outcome assertion reads."""

    result_data = _as_dict(result)
    status = str(result_data.get("status") or "unknown")
    if is_state_unknown(result_data):
        return (
            "最终结果未知：请求在工具执行之后中断，系统无法断言目标状态，"
            "请人工核对目标状态后再决定下一步。"
        )
    if status == "incomplete":
        return "最终结果为 incomplete：受保护的写步骤没有执行。"
    return f"最终结果为 {status}。"


def binding_probe_disclosure(environment: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Describe a read-only probe that ran outside the execution record.

    The confirmation-token host binding really probes the host, so a turn that
    reports an empty execution record still has to say that this read-only
    probe ran, otherwise the envelope understates what touched the target.
    """

    environment_data = _as_dict(environment)
    if str(environment_data.get("reason") or "") != BINDING_PROBE_REASON:
        return None
    return {
        "tool_name": BINDING_PROBE_TOOL_NAME,
        "read_only": True,
        "outside_execution_record": True,
        "reason": BINDING_PROBE_REASON,
        "status": str(environment_data.get("status") or "unknown"),
        "summary": (
            f"为绑定确认令牌运行了只读环境探测 {BINDING_PROBE_TOOL_NAME}，"
            "该调用不在执行记录内，也不是受确认门控的写操作。"
        ),
    }


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _lower(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        value = value.value
    return str(value).strip().lower()


class EvidenceBuilder:
    def __init__(self) -> None:
        self._events: list[EvidenceEvent] = []
        self._assertions: list[StateAssertion] = []
        self._event_counter = 0
        self._assertion_counter = 0

    def add_event(
        self,
        *,
        stage: EvidenceStage,
        title: str,
        details: dict[str, Any] | None = None,
        severity: EvidenceSeverity = EvidenceSeverity.INFO,
        refs: list[str] | None = None,
    ) -> EvidenceEvent:
        self._event_counter += 1
        event = EvidenceEvent(
            event_id=f"ev-{self._event_counter:03d}",
            stage=stage,
            title=title,
            details=dict(details or {}),
            severity=severity,
            refs=list(refs or []),
        )
        self._events.append(event)
        return event

    def add_assertion(
        self,
        *,
        name: str,
        passed: bool,
        evidence_refs: list[str] | None,
        summary: str,
    ) -> StateAssertion:
        self._assertion_counter += 1
        assertion = StateAssertion(
            assertion_id=f"as-{self._assertion_counter:03d}",
            name=name,
            passed=bool(passed),
            evidence_refs=list(evidence_refs or []),
            summary=summary,
        )
        self._assertions.append(assertion)
        return assertion

    def build(self) -> EvidenceChain:
        return EvidenceChain(
            events=list(self._events),
            state_assertions=list(self._assertions),
        )
