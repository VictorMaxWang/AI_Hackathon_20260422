from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.orchestrator import ReadonlyOrchestrator
from app.models import ToolResult


class DummyExecutor:
    pass


class UserToolMocks:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def create_user(self, executor: Any, **kwargs: Any) -> ToolResult:
        self.calls.append(("create_user_tool", kwargs))
        return ToolResult(
            tool_name="create_user_tool",
            success=True,
            data={"status": "created", **kwargs},
        )

    def delete_user(self, executor: Any, **kwargs: Any) -> ToolResult:
        self.calls.append(("delete_user_tool", kwargs))
        return ToolResult(
            tool_name="delete_user_tool",
            success=True,
            data={"status": "deleted", **kwargs},
        )


def make_orchestrator(mocks: UserToolMocks) -> ReadonlyOrchestrator:
    return ReadonlyOrchestrator(
        DummyExecutor(),
        create_user_tool_fn=mocks.create_user,
        delete_user_tool_fn=mocks.delete_user,
    )


def test_create_user_first_request_enters_pending_confirmation_without_execution() -> None:
    mocks = UserToolMocks()
    orchestrator = make_orchestrator(mocks)

    result = orchestrator.run("请创建普通用户 demo_guest")

    assert result["intent"]["intent"] == "create_user"
    assert result["risk"]["risk_level"] == "S1"
    assert result["risk"]["requires_confirmation"] is True
    assert result["result"]["status"] == "pending_confirmation"
    assert result["result"]["confirmation_text"] == "确认创建普通用户 demo_guest"
    assert result["execution"]["status"] == "skipped"
    assert result["plan"]["status"] == "pending_confirmation"
    assert mocks.calls == []
    assert orchestrator.memory.pending_action is not None
    assert orchestrator.memory.pending_action.confirmation_text == "确认创建普通用户 demo_guest"


def test_correct_create_confirmation_executes_and_clears_pending_action() -> None:
    mocks = UserToolMocks()
    orchestrator = make_orchestrator(mocks)

    orchestrator.run("请创建普通用户 demo_guest")
    result = orchestrator.run("确认创建普通用户 demo_guest")

    assert result["result"]["status"] == "success"
    assert result["result"]["tool_name"] == "create_user_tool"
    assert mocks.calls == [
        (
            "create_user_tool",
            {"username": "demo_guest", "create_home": True, "no_sudo": True},
        )
    ]
    assert orchestrator.memory.pending_action is None


def test_wrong_confirmation_does_not_execute() -> None:
    for wrong_text in ["yes", "ok", "继续", "confirm", "确认创建普通用户 other_user"]:
        mocks = UserToolMocks()
        orchestrator = make_orchestrator(mocks)
        orchestrator.run("请创建普通用户 demo_guest")

        result = orchestrator.run(wrong_text)

        assert result["result"]["status"] == "pending_confirmation"
        assert result["result"]["error"] == "confirmation_text_mismatch"
        assert result["execution"]["status"] == "skipped"
        assert mocks.calls == []
        assert orchestrator.memory.pending_action is not None
        assert orchestrator.memory.pending_action.confirmation_text == "确认创建普通用户 demo_guest"


def test_delete_user_requires_s2_strong_confirmation() -> None:
    mocks = UserToolMocks()
    orchestrator = make_orchestrator(mocks)

    result = orchestrator.run("请删除普通用户 demo_guest")

    assert result["intent"]["intent"] == "delete_user"
    assert result["risk"]["risk_level"] == "S2"
    assert result["risk"]["requires_confirmation"] is True
    assert result["result"]["status"] == "pending_confirmation"
    assert result["result"]["confirmation_text"] == "确认删除普通用户 demo_guest"
    assert mocks.calls == []


def test_correct_delete_confirmation_executes_delete_user_tool() -> None:
    mocks = UserToolMocks()
    orchestrator = make_orchestrator(mocks)

    orchestrator.run("请删除普通用户 demo_guest")
    result = orchestrator.run("确认删除普通用户 demo_guest")

    assert result["result"]["status"] == "success"
    assert result["result"]["tool_name"] == "delete_user_tool"
    assert mocks.calls == [
        (
            "delete_user_tool",
            {"username": "demo_guest", "remove_home": False},
        )
    ]
    assert orchestrator.memory.pending_action is None


def test_new_request_during_pending_does_not_replace_or_execute_pending_action() -> None:
    mocks = UserToolMocks()
    orchestrator = make_orchestrator(mocks)

    orchestrator.run("请创建普通用户 first_user")
    result = orchestrator.run("请创建普通用户 second_user")

    assert result["result"]["status"] == "pending_confirmation"
    assert result["result"]["error"] == "confirmation_text_mismatch"
    assert result["result"]["confirmation_text"] == "确认创建普通用户 first_user"
    assert mocks.calls == []
    assert orchestrator.memory.pending_action is not None
    assert orchestrator.memory.pending_action.target["username"] == "first_user"


def test_cancel_clears_pending_action_without_execution() -> None:
    for cancel_text in ["取消", "放弃", "cancel"]:
        mocks = UserToolMocks()
        orchestrator = make_orchestrator(mocks)
        orchestrator.run("请创建普通用户 demo_guest")

        result = orchestrator.run(cancel_text)

        assert result["result"]["status"] == "cancelled"
        assert result["execution"]["status"] == "skipped"
        assert mocks.calls == []
        assert orchestrator.memory.pending_action is None


def test_s3_never_enters_confirmation_flow() -> None:
    mocks = UserToolMocks()
    orchestrator = make_orchestrator(mocks)

    result = orchestrator.run("请创建普通用户 root")

    assert result["risk"]["risk_level"] == "S3"
    assert result["risk"]["requires_confirmation"] is False
    assert result["result"]["status"] == "refused"
    assert result["execution"]["status"] == "skipped"
    assert result["result"].get("confirmation_text") is None
    assert mocks.calls == []
    assert orchestrator.memory.pending_action is None
