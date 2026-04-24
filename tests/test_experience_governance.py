from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evolution.experience_store import ExperienceStore, GovernanceTransitionError
from app.models.evolution import ExperienceMemoryType, ExperienceRecord, GovernanceStatus
from app.models.policy import RiskLevel
from app.models.result import ExecutionStatus


def make_record(
    memory_id: str = "mem-001",
    *,
    session_id: str = "session-001",
    host_id: str = "host-001",
    risk_level: RiskLevel = RiskLevel.S0,
    status: ExecutionStatus = ExecutionStatus.SUCCESS,
    memory_type: ExperienceMemoryType = ExperienceMemoryType.PROCEDURAL,
    summary: str = "Use bounded file search after env probe.",
    lesson: str = "Bound search scope and verify outcomes before reuse.",
    tags: list[str] | None = None,
    evidence_refs: list[str] | None = None,
    source_request_id: str = "req-001",
    created_at: datetime | None = None,
) -> ExperienceRecord:
    return ExperienceRecord(
        memory_id=memory_id,
        session_id=session_id,
        host_id=host_id,
        intent="search_files",
        risk_level=risk_level,
        status=status,
        memory_type=memory_type,
        summary=summary,
        lesson=lesson,
        tags=tags or ["governance", "experience"],
        evidence_refs=evidence_refs or [],
        source_request_id=source_request_id,
        created_at=created_at or datetime.now(timezone.utc),
    )


def test_new_experience_starts_in_quarantine(tmp_path) -> None:
    store = ExperienceStore(tmp_path / "experience.sqlite3")
    record = make_record(evidence_refs=["ev-001"])

    stored = store.add(record)

    assert stored.governance_status == GovernanceStatus.QUARANTINE
    assert stored.promoted_to_workflow is False
    assert stored.promotion_gate_passed is False
    assert stored.dedup_hash
    assert stored.host_scope == ["host-001"]
    assert stored.session_scope == ["session-001"]


def test_verify_requires_evidence_refs(tmp_path) -> None:
    store = ExperienceStore(tmp_path / "experience.sqlite3")
    record = store.add(make_record(memory_id="mem-no-evidence", evidence_refs=[]))

    with pytest.raises(GovernanceTransitionError):
        store.verify(record.memory_id)

    persisted = store.get(record.memory_id)
    assert persisted is not None
    assert persisted.governance_status == GovernanceStatus.QUARANTINE


def test_duplicate_experience_merges_by_dedup_hash(tmp_path) -> None:
    store = ExperienceStore(tmp_path / "experience.sqlite3")
    first = store.add(
        make_record(
            memory_id="mem-first",
            session_id="session-a",
            host_id="host-a",
            summary="Use bounded file search after env probe.",
            lesson="Bound search scope and verify outcomes before reuse.",
            evidence_refs=["ev-001"],
            source_request_id="req-a",
        )
    )
    merged = store.add(
        make_record(
            memory_id="mem-second",
            session_id="session-b",
            host_id="host-b",
            summary="  use bounded file search after env probe.  ",
            lesson="bound search scope and verify outcomes before reuse.",
            evidence_refs=["ev-002"],
            source_request_id="req-b",
        )
    )

    assert merged.memory_id == first.memory_id
    assert merged.evidence_refs == ["ev-001", "ev-002"]
    assert merged.host_scope == ["host-a", "host-b"]
    assert merged.session_scope == ["session-a", "session-b"]
    assert merged.provenance["request_ids"] == ["req-a", "req-b"]
    assert len(store.recent()) == 1


def test_single_success_does_not_auto_promote(tmp_path) -> None:
    store = ExperienceStore(tmp_path / "experience.sqlite3")
    record = store.add(
        make_record(
            memory_id="mem-single-success",
            evidence_refs=["ev-001"],
            source_request_id="req-single",
        )
    )

    verified = store.verify(record.memory_id)

    assert verified is not None
    assert verified.governance_status == GovernanceStatus.VERIFIED
    assert verified.promotion_gate_passed is False
    assert verified.promoted_to_workflow is False


def test_apply_decay_and_tombstone_update_governance_state(tmp_path) -> None:
    store = ExperienceStore(tmp_path / "experience.sqlite3")
    old_record = store.add(
        make_record(
            memory_id="mem-decay",
            evidence_refs=["ev-old-1", "ev-old-2"],
            created_at=datetime.now(timezone.utc) - timedelta(days=45),
            source_request_id="req-old",
        )
    )
    verified = store.verify(old_record.memory_id, evidence_refs=["ev-old-3"])
    assert verified is not None

    decayed = store.apply_decay(stale_after_days=30, increment=0.5)
    assert [record.memory_id for record in decayed] == [old_record.memory_id]
    assert decayed[0].decay_score == pytest.approx(0.5)

    tombstoned = store.tombstone(old_record.memory_id, reason="conflicting policy outcome")
    assert tombstoned is not None
    assert tombstoned.governance_status == GovernanceStatus.TOMBSTONED
    assert tombstoned.promotion_gate_passed is False
    assert tombstoned.promoted_to_workflow is False
    assert store.recent() == []


def test_high_risk_refusal_can_be_verified_but_not_auto_promoted(tmp_path) -> None:
    store = ExperienceStore(tmp_path / "experience.sqlite3")
    refused = store.add(
        make_record(
            memory_id="mem-refusal",
            risk_level=RiskLevel.S3,
            status=ExecutionStatus.REFUSED,
            memory_type=ExperienceMemoryType.EPISODIC,
            summary="Refused protected-path deletion request.",
            lesson="Keep protected path refusals as audit-grade episodic experience.",
            tags=["high_risk_refusal", "policy"],
            evidence_refs=["ev-refusal-1"],
            source_request_id="req-refusal",
        )
    )

    verified = store.verify(refused.memory_id)

    assert verified is not None
    assert verified.governance_status == GovernanceStatus.VERIFIED
    assert verified.promotion_gate_passed is False
    assert verified.promoted_to_workflow is False
