from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.evolution.experience_store import (  # noqa: E402
    ExperienceStore,
    GovernanceTransitionError,
    SensitiveExperienceError,
    is_valid_evidence_ref,
    passes_promotion_gate,
)
from app.evolution.init import apply_evo_lite_hook  # noqa: E402
from app.evolution.workflows import (  # noqa: E402
    WorkflowTemplateLoadError,
    clear_workflow_template_cache,
    load_workflow_templates,
    match_workflow_template,
    reload_workflow_templates,
    try_match_workflow_template,
)
from app.models.evolution import (  # noqa: E402
    CANONICAL_WORKFLOW_TOOL_NAMES,
    WORKFLOW_TOOL_INTENTS,
    ExperienceRecord,
    GovernanceStatus,
    MemoryType,
    WorkflowTemplate,
)
from app.models.policy import RiskLevel  # noqa: E402
from app.models.result import ExecutionStatus  # noqa: E402
from tests.test_evo_lite_hook import EvoLiteToolMocks, make_orchestrator  # noqa: E402


S3_REQUEST = "把 /etc 下面没用的配置删掉"


def make_record(
    memory_id: str = "mem-hardening",
    *,
    evidence_refs: list[str] | None = None,
    summary: str = "Bounded file search stayed inside the declared base path.",
    lesson: str = "Keep search scope bounded before reusing the procedure.",
    tags: list[str] | None = None,
    memory_type: MemoryType = MemoryType.PROCEDURAL,
    status: ExecutionStatus = ExecutionStatus.SUCCESS,
    risk_level: RiskLevel = RiskLevel.S0,
    source_request_id: str | None = "req-hardening",
    expires_at: datetime | None = None,
) -> ExperienceRecord:
    return ExperienceRecord(
        memory_id=memory_id,
        session_id="session-hardening",
        host_id="host-hardening",
        intent="search_files",
        risk_level=risk_level,
        status=status,
        memory_type=memory_type,
        summary=summary,
        lesson=lesson,
        tags=tags or ["hardening", "experience"],
        evidence_refs=evidence_refs or [],
        source_request_id=source_request_id,
        expires_at=expires_at,
    )


def promotable_record(**overrides: Any) -> ExperienceRecord:
    payload: dict[str, Any] = {
        "memory_id": "mem-promotable",
        "session_id": "session-promotable",
        "host_id": "host-promotable",
        "intent": "search_files",
        "risk_level": RiskLevel.S0,
        "status": ExecutionStatus.SUCCESS,
        "memory_type": MemoryType.PROCEDURAL,
        "summary": "Bounded file search succeeded twice on the same host.",
        "lesson": "Reuse the bounded search procedure with explicit limits.",
        "tags": ["procedure", "verified"],
        "source_request_id": None,
        "provenance": {"sources": ["reflection"], "request_ids": ["req-a", "req-b"]},
        "evidence_refs": ["ev-001", "ev-002"],
        "governance_status": GovernanceStatus.VERIFIED,
        "decay_score": 0.0,
    }
    payload.update(overrides)
    return ExperienceRecord(**payload)


def write_template(directory: Path, payload: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{payload['workflow_id']}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def readonly_template_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "workflow_id": "hardening_probe",
        "name": "hardening probe",
        "description": "Read-only environment probe used by hardening tests.",
        "risk_level": "S0",
        "allowed_tools": ["env_probe_tool"],
        "forbidden_actions": ["any write operation"],
        "requires_confirmation": False,
        "steps": [
            {
                "step_id": "probe_environment",
                "tool_name": "env_probe_tool",
                "intent": "env_probe",
                "description": "Collect a read-only environment snapshot.",
                "risk_level": "S0",
                "requires_policy": True,
                "requires_confirmation": False,
            }
        ],
        "post_checks": ["snapshot recorded"],
        "tags": ["readonly", "hardening"],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def template_dir(tmp_path: Path):
    directory = tmp_path / "templates"
    directory.mkdir()
    yield directory
    clear_workflow_template_cache(directory)


def test_dedup_fires_without_evidence_refs(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path / "experience.sqlite3")

    first = store.add(make_record(memory_id="mem-dup-1", evidence_refs=[]))
    second = store.add(make_record(memory_id="mem-dup-2", evidence_refs=[]))

    assert first.dedup_hash == second.dedup_hash
    assert second.memory_id == first.memory_id
    assert store.get("mem-dup-2") is None
    assert len(store.recent()) == 1


def test_hook_persists_evidence_refs_and_reflection_provenance(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path / "experience.sqlite3")

    envelope = make_orchestrator(EvoLiteToolMocks(), experience_store=store).run(S3_REQUEST)
    evo_lite = envelope["evo_lite"]

    assert evo_lite["experience_saved"] is True
    assert evo_lite["evidence_refs"]

    stored = store.get(evo_lite["memory_id"])
    assert stored is not None
    assert stored.evidence_refs == evo_lite["evidence_refs"]
    assert all(is_valid_evidence_ref(ref) for ref in stored.evidence_refs)
    assert stored.provenance["sources"] == ["reflection"]
    assert stored.provenance["reflection_ids"]
    assert stored.provenance["intent_names"] == ["delete_path"]
    assert stored.provenance["evidence_origin"] == "evidence_chain"


def test_hook_memory_id_always_resolves_after_dedup_merge(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path / "experience.sqlite3")
    orchestrator = make_orchestrator(EvoLiteToolMocks(), experience_store=store)

    first = orchestrator.run(S3_REQUEST)["evo_lite"]
    second = orchestrator.run(S3_REQUEST)["evo_lite"]

    assert first["experience_deduplicated"] is False
    assert second["experience_deduplicated"] is True
    assert second["memory_id"] == first["memory_id"]
    assert store.get(second["memory_id"]) is not None
    assert len(store.recent()) == 1

    merged = store.get(second["memory_id"])
    assert merged is not None
    assert len(merged.provenance["request_ids"]) == 2


def test_hook_reports_workflow_candidate_instead_of_dropping_it(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path / "experience.sqlite3")
    envelope = {
        "intent": {"intent": "search_files"},
        "risk": {"risk_level": "S0", "allow": True},
        "result": {"status": "failed"},
        "execution": {"status": "failed"},
        "params": {"base_path": "/"},
        "explanation": "file search scope too broad, max_results and max_depth missing",
    }

    enriched = apply_evo_lite_hook(envelope, experience_store=store)
    evo_lite = enriched["evo_lite"]

    assert evo_lite["workflow_candidate"] is True
    stored = store.get(evo_lite["memory_id"])
    assert stored is not None
    assert stored.promoted_to_workflow is False
    assert stored.provenance["workflow_candidate"] == "true"


def test_verify_rejects_unverifiable_evidence_refs(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path / "experience.sqlite3")
    record = store.add(make_record(memory_id="mem-junk-refs", evidence_refs=[]))

    with pytest.raises(GovernanceTransitionError):
        store.verify(record.memory_id, evidence_refs=["password=hunter2", "token: abc123"])

    persisted = store.get(record.memory_id)
    assert persisted is not None
    assert persisted.governance_status == GovernanceStatus.QUARANTINE
    assert persisted.evidence_refs == []
    assert persisted.promotion_gate_passed is False


def test_tombstone_rejects_sensitive_reason(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path / "experience.sqlite3")
    record = store.add(make_record(memory_id="mem-tombstone-secret", evidence_refs=["ev-001"]))

    with pytest.raises(SensitiveExperienceError):
        store.tombstone(record.memory_id, reason="api_key=AKIAIOSFODNN7EXAMPLE")

    persisted = store.get(record.memory_id)
    assert persisted is not None
    assert persisted.governance_status == GovernanceStatus.QUARANTINE
    assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(persisted.provenance, ensure_ascii=False)

    tombstoned = store.tombstone(record.memory_id, reason="conflicting policy outcome")
    assert tombstoned is not None
    assert tombstoned.governance_status == GovernanceStatus.TOMBSTONED


def test_legacy_sensitive_record_can_still_be_tombstoned(tmp_path: Path) -> None:
    db_path = tmp_path / "experience.sqlite3"
    store = ExperienceStore(db_path)
    record = store.add(make_record(memory_id="mem-legacy-secret", evidence_refs=["ev-001"]))

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE experience_records SET summary = ? WHERE memory_id = ?",
            ("password=hunter2", record.memory_id),
        )

    tombstoned = ExperienceStore(db_path).tombstone(record.memory_id, reason="poisoned memory")

    assert tombstoned is not None
    assert tombstoned.governance_status == GovernanceStatus.TOMBSTONED
    assert tombstoned.promotion_gate_passed is False


def test_verify_rejects_a_record_whose_stored_content_is_sensitive(tmp_path: Path) -> None:
    db_path = tmp_path / "experience.sqlite3"
    store = ExperienceStore(db_path)
    record = store.add(make_record(memory_id="mem-verify-secret", evidence_refs=["ev-001"]))

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE experience_records SET lesson = ? WHERE memory_id = ?",
            ("api_key=AKIAIOSFODNN7EXAMPLE", record.memory_id),
        )

    reopened = ExperienceStore(db_path)
    with pytest.raises(SensitiveExperienceError):
        reopened.verify(record.memory_id, evidence_refs=["ev-002"])

    persisted = reopened.get(record.memory_id)
    assert persisted is not None
    assert persisted.governance_status == GovernanceStatus.QUARANTINE


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ev-001", True),
        ("as-001", True),
        ("ev-old-1", True),
        (" ev-002 ", True),
        ("password=hunter2", False),
        ("token: abc123", False),
        ("evidence", False),
        ("ev-", False),
        ("", False),
        (None, False),
    ],
)
def test_evidence_ref_shape_validation(value: Any, expected: bool) -> None:
    assert is_valid_evidence_ref(value) is expected


def test_promotion_gate_accepts_a_fully_qualified_record() -> None:
    assert passes_promotion_gate(promotable_record()) is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"governance_status": GovernanceStatus.QUARANTINE},
        {"governance_status": GovernanceStatus.TOMBSTONED},
        {"memory_type": MemoryType.EPISODIC},
        {"status": ExecutionStatus.REFUSED},
        {"risk_level": RiskLevel.S2},
        {"decay_score": 1.0},
        {"evidence_refs": ["ev-001"]},
        {"evidence_refs": ["not-evidence-1", "not-evidence-2"]},
        {"provenance": {"sources": ["reflection"], "request_ids": ["req-a"]}},
        {"tags": ["high_risk_refusal", "procedure"]},
    ],
)
def test_promotion_gate_blocks_each_policy_violation(overrides: dict[str, Any]) -> None:
    assert passes_promotion_gate(promotable_record(**overrides)) is False


def test_verified_record_with_junk_refs_cannot_pass_the_gate(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path / "experience.sqlite3")
    record = store.add(
        make_record(
            memory_id="mem-shape-gate",
            evidence_refs=["totally made up", "another made up ref"],
        )
    )

    verified = store.verify(record.memory_id)

    assert verified is not None
    assert verified.governance_status == GovernanceStatus.VERIFIED
    assert verified.promotion_gate_passed is False


def test_expired_records_are_hidden_from_read_paths(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path / "experience.sqlite3")
    expired = store.add(
        make_record(
            memory_id="mem-expired",
            summary="Expired experience that must not be reused.",
            tags=["expiring"],
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
    )
    store.add(
        make_record(
            memory_id="mem-live",
            summary="Live experience that is still valid.",
            tags=["expiring"],
        )
    )

    assert [record.memory_id for record in store.recent()] == ["mem-live"]
    assert [record.memory_id for record in store.search_by_tags(["expiring"])] == ["mem-live"]
    assert store.get("mem-expired") is None
    assert store.get("mem-expired", include_expired=True) is not None
    assert store.purge_expired() == [expired.memory_id]
    assert store.get("mem-expired", include_expired=True) is None


def test_one_unreadable_row_does_not_brick_the_store(tmp_path: Path) -> None:
    db_path = tmp_path / "experience.sqlite3"
    store = ExperienceStore(db_path)
    store.add(make_record(memory_id="mem-good", summary="Good readable experience."))
    store.add(make_record(memory_id="mem-broken", summary="Row that becomes unreadable."))

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE experience_records SET risk_level = ? WHERE memory_id = ?",
            ("NOT_A_RISK_LEVEL", "mem-broken"),
        )

    with pytest.warns(UserWarning, match="unreadable row"):
        reopened = ExperienceStore(db_path)

    assert [record.memory_id for record in reopened.recent()] == ["mem-good"]
    assert reopened.get("mem-good") is not None


def test_template_loader_rejects_tools_outside_the_canonical_registry(template_dir: Path) -> None:
    write_template(
        template_dir,
        readonly_template_payload(
            workflow_id="unregistered_tool",
            allowed_tools=["totally_new_tool"],
            steps=[
                {
                    "step_id": "do_anything",
                    "tool_name": "totally_new_tool",
                    "intent": "do anything",
                    "description": "Step that should never be loadable.",
                    "risk_level": "S0",
                    "requires_policy": True,
                    "requires_confirmation": False,
                }
            ],
        ),
    )

    with pytest.raises(WorkflowTemplateLoadError, match="canonical tool registry"):
        load_workflow_templates(template_dir)


def test_shipped_templates_only_use_registered_tools() -> None:
    for template in load_workflow_templates().values():
        used_tools = set(template.allowed_tools)
        used_tools.update(step.tool_name for step in template.steps)
        assert used_tools <= CANONICAL_WORKFLOW_TOOL_NAMES


def test_canonical_registry_matches_the_planner_registry() -> None:
    from app.agent.planner import WORKFLOW_TOOL_INTENTS as planner_registry

    assert planner_registry == WORKFLOW_TOOL_INTENTS


def test_template_step_risk_cannot_exceed_template_risk() -> None:
    payload = readonly_template_payload(
        workflow_id="risk_escalation",
        steps=[
            {
                "step_id": "escalated_step",
                "tool_name": "env_probe_tool",
                "intent": "env_probe",
                "description": "Step declaring a higher risk than its template.",
                "risk_level": "S3",
                "requires_policy": True,
                "requires_confirmation": True,
            }
        ],
    )

    with pytest.raises(ValidationError, match="above template risk"):
        WorkflowTemplate.model_validate(payload)


def test_template_with_write_step_must_require_confirmation() -> None:
    payload = readonly_template_payload(
        workflow_id="silent_write",
        risk_level="S1",
        requires_confirmation=False,
        allowed_tools=["create_user_tool"],
        steps=[
            {
                "step_id": "create_normal_user",
                "tool_name": "create_user_tool",
                "intent": "create_user",
                "description": "Write step without a template confirmation gate.",
                "risk_level": "S1",
                "requires_policy": True,
                "requires_confirmation": True,
            }
        ],
    )

    with pytest.raises(ValidationError, match="requires_confirmation is false"):
        WorkflowTemplate.model_validate(payload)


def test_write_step_must_declare_policy_and_confirmation() -> None:
    payload = readonly_template_payload(
        workflow_id="ungated_write",
        risk_level="S1",
        requires_confirmation=True,
        allowed_tools=["create_user_tool"],
        steps=[
            {
                "step_id": "create_normal_user",
                "tool_name": "create_user_tool",
                "intent": "create_user",
                "description": "Write step that skips its own confirmation gate.",
                "risk_level": "S1",
                "requires_policy": True,
                "requires_confirmation": False,
            }
        ],
    )

    with pytest.raises(ValidationError, match="must set requires_confirmation"):
        WorkflowTemplate.model_validate(payload)


def test_shipped_templates_contain_their_step_risk() -> None:
    for template in load_workflow_templates().values():
        for step in template.steps:
            assert step.risk_level.value <= template.risk_level.value
            if step.risk_level != RiskLevel.S0:
                assert step.requires_confirmation is True
                assert template.requires_confirmation is True


def test_one_malformed_template_does_not_break_matching(template_dir: Path) -> None:
    write_template(template_dir, readonly_template_payload())
    (template_dir / "broken.json").write_text("{ not json", encoding="utf-8")
    clear_workflow_template_cache(template_dir)

    with pytest.raises(WorkflowTemplateLoadError):
        match_workflow_template("hardening probe", template_dir)

    with pytest.warns(UserWarning, match="workflow template load failed"):
        assert try_match_workflow_template("hardening probe", template_dir) is None


def test_templates_are_validated_once_and_cached(template_dir: Path) -> None:
    path = write_template(template_dir, readonly_template_payload())
    clear_workflow_template_cache(template_dir)

    first = load_workflow_templates(template_dir)
    assert first["hardening_probe"].name == "hardening probe"

    path.write_text(
        json.dumps(readonly_template_payload(name="renamed probe"), ensure_ascii=False),
        encoding="utf-8",
    )

    assert load_workflow_templates(template_dir)["hardening_probe"].name == "hardening probe"
    assert reload_workflow_templates(template_dir)["hardening_probe"].name == "renamed probe"


def test_evolution_init_imports_without_the_agent_package() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", "import app.evolution.init as hook; print(hook.apply_evo_lite_hook.__name__)"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "apply_evo_lite_hook" in completed.stdout
