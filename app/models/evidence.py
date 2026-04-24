from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
