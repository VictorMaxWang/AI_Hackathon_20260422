from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.orchestrator import ReadonlyOrchestrator
from app.evolution.experience_store import ExperienceStore
from app.models import EnvironmentSnapshot, ToolResult


class DummyExecutor:
    pass


class EvoLiteToolMocks:
    def __init__(self, *, create_verified: bool = True) -> None:
        self.create_verified = create_verified
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def env_probe(self, executor: Any) -> EnvironmentSnapshot:
        self.calls.append(("env_probe_tool", {}))
        return EnvironmentSnapshot(
            hostname="demo-host",
            distro="Ubuntu 24.04",
            kernel="6.8.0",
            current_user="demo",
            is_root=False,
            sudo_available=False,
            available_commands=["df", "find", "ps", "ss"],
            connection_mode="local",
        )

    def disk_usage(self, executor: Any) -> ToolResult:
        self.calls.append(("disk_usage_tool", {}))
        return ToolResult(
            tool_name="disk_usage_tool",
            success=True,
            data={
                "status": "ok",
                "count": 1,
                "filesystems": [
                    {
                        "filesystem": "/dev/sda1",
                        "type": "ext4",
                        "size": "50G",
                        "used": "20G",
                        "available": "30G",
                        "use_percent": "40%",
                        "mounted_on": "/",
                    }
                ],
            },
        )

    def create_user(self, executor: Any, **kwargs: Any) -> ToolResult:
        self.calls.append(("create_user_tool", kwargs))
        return ToolResult(
            tool_name="create_user_tool",
            success=True,
            data={
                "status": "created",
                "username": kwargs["username"],
                "verified": self.create_verified,
            },
        )


class FailingExperienceStore(ExperienceStore):
    def add(self, record):  # type: ignore[override]
        raise RuntimeError("store unavailable")


def make_orchestrator(
    mocks: EvoLiteToolMocks,
    *,
    experience_store: ExperienceStore | None = None,
    evo_lite_enabled: bool = True,
) -> ReadonlyOrchestrator:
    return ReadonlyOrchestrator(
        DummyExecutor(),
        env_probe=mocks.env_probe,
        disk_tool=mocks.disk_usage,
        create_user_tool_fn=mocks.create_user,
        experience_store=experience_store,
        evo_lite_enabled=evo_lite_enabled,
    )


def test_s0_success_query_runs_evaluator_and_returns_evo_lite(tmp_path) -> None:
    store = ExperienceStore(tmp_path / "experience.sqlite3")
    mocks = EvoLiteToolMocks()
    result = make_orchestrator(mocks, experience_store=store).run("帮我查看当前磁盘使用情况")

    assert result["result"]["status"] == "success"
    assert "evo_lite" in result
    assert result["evo_lite"]["evaluation"]["task_success"] is True
    assert result["evo_lite"]["evaluation"]["needs_reflection"] is False
    assert result["evo_lite"]["reflection_summary"] is None
    assert result["evo_lite"]["experience_saved"] is False
    assert result["evo_lite"]["memory_id"] is None
    assert store.recent() == []


def test_s3_refusal_generates_reflection_and_saves_experience(tmp_path) -> None:
    store = ExperienceStore(tmp_path / "experience.sqlite3")
    result = make_orchestrator(EvoLiteToolMocks(), experience_store=store).run(
        "把 /etc 下面没用的配置删掉"
    )

    assert result["result"]["status"] == "refused"
    assert result["execution"]["status"] == "skipped"
    assert result["evo_lite"]["evaluation"]["experience_candidate"] is True
    assert result["evo_lite"]["reflection_summary"]
    assert result["evo_lite"]["experience_saved"] is True
    assert result["evo_lite"]["memory_id"]

    records = store.recent()
    assert len(records) == 1
    assert records[0].memory_id == result["evo_lite"]["memory_id"]
    assert records[0].summary == result["evo_lite"]["reflection_summary"]
    assert records[0].host_id == "unknown"


def test_post_check_failure_sets_needs_reflection_and_saves_failed_experience(tmp_path) -> None:
    store = ExperienceStore(tmp_path / "experience.sqlite3")
    mocks = EvoLiteToolMocks(create_verified=False)
    orchestrator = make_orchestrator(mocks, experience_store=store)

    pending = orchestrator.run("请创建普通用户 demo_guest")
    result = orchestrator.run(pending["result"]["confirmation_text"])

    assert pending["result"]["status"] == "pending_confirmation"
    assert result["result"]["status"] == "success"
    assert result["evo_lite"]["evaluation"]["needs_reflection"] is True
    assert result["evo_lite"]["evaluation"]["post_check_passed"] is False
    assert result["evo_lite"]["reflection_summary"]
    assert result["evo_lite"]["experience_saved"] is True

    records = store.recent()
    assert len(records) == 1
    assert records[0].status == "failed"
    assert records[0].intent == "create_user"
    assert records[0].host_id == "unknown"


def test_store_write_failure_does_not_break_main_request(tmp_path) -> None:
    store = FailingExperienceStore(tmp_path / "experience.sqlite3")
    result = make_orchestrator(EvoLiteToolMocks(), experience_store=store).run(
        "把 /etc 下面没用的配置删掉"
    )

    assert result["result"]["status"] == "refused"
    assert result["evo_lite"]["evaluation"]["experience_candidate"] is True
    assert result["evo_lite"]["reflection_summary"]
    assert result["evo_lite"]["experience_saved"] is False
    assert result["evo_lite"]["memory_id"] is None
    assert result["evo_lite"]["warning"] == "experience_store_write_failed"


def test_s1_pending_confirmation_is_not_changed_to_execution(tmp_path) -> None:
    store = ExperienceStore(tmp_path / "experience.sqlite3")
    result = make_orchestrator(EvoLiteToolMocks(), experience_store=store).run(
        "请创建普通用户 demo_guest"
    )

    assert result["risk"]["risk_level"] == "S1"
    assert result["result"]["status"] == "pending_confirmation"
    assert result["execution"]["status"] == "skipped"
    assert result["execution"]["steps"] == []
    assert result["evo_lite"]["evaluation"]["task_success"] is False
    assert result["evo_lite"]["evaluation"]["needs_reflection"] is False
    assert result["evo_lite"]["reflection_summary"] is None
    assert result["evo_lite"]["experience_saved"] is False
    assert store.recent() == []
