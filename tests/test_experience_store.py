from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evolution.experience_store import ExperienceStore, SensitiveExperienceError
from app.models.evolution import ExperienceMemoryType, ExperienceRecord
from app.models.policy import RiskLevel
from app.models.result import ExecutionStatus


def make_record(
    memory_id: str = "mem-001",
    *,
    tags: list[str] | None = None,
    created_at: datetime | None = None,
    memory_type: ExperienceMemoryType = ExperienceMemoryType.EPISODIC,
    summary: str = "Refused unsafe request after risk evaluation.",
    lesson: str = "Keep policy decisions separate from experience storage.",
) -> ExperienceRecord:
    return ExperienceRecord(
        memory_id=memory_id,
        session_id="session-001",
        host_id="host-001",
        intent="delete_user",
        risk_level=RiskLevel.S2,
        status=ExecutionStatus.REFUSED,
        memory_type=memory_type,
        summary=summary,
        lesson=lesson,
        tags=tags or ["refusal", "policy"],
        source_request_id="req-001",
        created_at=created_at or datetime.now(timezone.utc),
    )


def test_add_and_get_experience(tmp_path) -> None:
    store = ExperienceStore(tmp_path / "experience.sqlite3")
    record = make_record(tags=["refusal", "confirmation"])

    stored = store.add(record)
    loaded = store.get(record.memory_id)

    assert stored == record
    assert loaded == record
    assert loaded is not None
    assert loaded.tags == ["refusal", "confirmation"]
    assert loaded.memory_type == ExperienceMemoryType.EPISODIC


def test_search_by_tags_returns_matching_records(tmp_path) -> None:
    store = ExperienceStore(tmp_path / "experience.sqlite3")
    older = make_record(
        "mem-old",
        tags=["semantic", "host"],
        created_at=datetime(2026, 4, 22, tzinfo=timezone.utc),
        memory_type=ExperienceMemoryType.SEMANTIC,
    )
    newer = make_record(
        "mem-new",
        tags=["policy", "confirmation"],
        created_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
    )
    store.add(older)
    store.add(newer)

    matches = store.search_by_tags(["confirmation", "missing"])

    assert [record.memory_id for record in matches] == ["mem-new"]


def test_recent_returns_newest_records_first_and_honors_limit(tmp_path) -> None:
    store = ExperienceStore(tmp_path / "experience.sqlite3")
    base = datetime(2026, 4, 23, tzinfo=timezone.utc)
    store.add(make_record("mem-1", created_at=base))
    store.add(make_record("mem-2", created_at=base + timedelta(minutes=1)))
    store.add(make_record("mem-3", created_at=base + timedelta(minutes=2)))

    recent = store.recent(limit=2)

    assert [record.memory_id for record in recent] == ["mem-3", "mem-2"]


def test_mark_promoted_updates_record(tmp_path) -> None:
    store = ExperienceStore(tmp_path / "experience.sqlite3")
    record = make_record("mem-promote", memory_type=ExperienceMemoryType.PROCEDURAL)
    store.add(record)

    promoted = store.mark_promoted(record.memory_id)

    assert promoted is not None
    assert promoted.promoted_to_workflow is True
    assert store.get(record.memory_id).promoted_to_workflow is True


def test_delete_removes_record(tmp_path) -> None:
    store = ExperienceStore(tmp_path / "experience.sqlite3")
    record = make_record("mem-delete")
    store.add(record)

    assert store.delete(record.memory_id) is True
    assert store.get(record.memory_id) is None
    assert store.delete(record.memory_id) is False


@pytest.mark.parametrize(
    ("summary", "lesson"),
    [
        ("password=hunter2", "Do not preserve secrets."),
        ("-----BEGIN RSA PRIVATE KEY-----\nsecret\n-----END RSA PRIVATE KEY-----", "no key material"),
        ("stdout: " + ("x" * 600), "Do not save long stdout or stderr."),
        ("$ rm -rf /", "Do not store raw user command text."),
        ("PATH=/usr/bin\nHOME=/home/demo", "Do not store full environment dumps."),
    ],
)
def test_sensitive_or_large_fields_are_not_saved(tmp_path, summary: str, lesson: str) -> None:
    store = ExperienceStore(tmp_path / "experience.sqlite3")
    record = make_record("mem-sensitive", summary=summary, lesson=lesson, tags=["sensitive"])

    with pytest.raises(SensitiveExperienceError):
        store.add(record)

    assert store.get(record.memory_id) is None
    assert store.recent() == []
    assert store.search_by_tags(["sensitive"]) == []
