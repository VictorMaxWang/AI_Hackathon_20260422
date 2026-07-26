from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.confirmation import (
    claim_confirmation_token_id,
    confirmation_status_from_parts,
    new_confirmation_token_id,
)
from app.agent.extraction import (
    extract_port,
    extract_username,
    looks_like_privilege_escalation,
    mentions_privilege_token,
    split_clauses,
    tool_name_for_intent,
)
from app.agent.memory import AgentMemory
from app.agent.orchestrator import (
    ENV_PROBE_TOOL_NAME,
    ReadonlyOrchestrator,
    _condition_skip_reason,
)
from app.agent.parser import ReadonlyParser
from app.agent.planner import MultistepPlanner
from app.agent.previews import build_policy_simulator
from app.agent.recovery import FAILURE_ENVIRONMENT_DRIFT, build_recovery_suggestion
from app.agent.summarizer import ReadonlySummarizer
from app.evolution.workflows import load_workflow_templates
from app.models import EnvironmentSnapshot, IntentTarget, ParsedIntent, ToolResult
from app.policy import evaluate as evaluate_policy
from app.models.evolution import WORKFLOW_TOOL_INTENTS
from app.models.intent import PlanStep


NOT_WHITELISTED_FRAGMENTS = ("not whitelisted", "no whitelisted tool is mapped")

TEMPLATE_REQUESTS = {
    "safe_disk_triage": "先查看磁盘再查看内存",
    "safe_file_search": "先查找 /var/log 下的 nginx 日志文件再查看磁盘",
    "diagnose_port_owner": "先看 8080 端口再看对应进程",
    "safe_user_lifecycle": "创建再删除测试用户 demo_temp",
}


class DummyExecutor:
    pass


class HardeningToolMocks:
    def __init__(
        self,
        *,
        sudo_available: bool = True,
        is_root: bool = False,
        delete_delay_seconds: float = 0.0,
    ) -> None:
        self.sudo_available = sudo_available
        self.is_root = is_root
        self.delete_delay_seconds = delete_delay_seconds
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._lock = threading.Lock()

    def _record(self, tool_name: str, args: dict[str, Any]) -> None:
        with self._lock:
            self.calls.append((tool_name, args))

    def env_probe(self, executor: Any) -> EnvironmentSnapshot:
        self._record(ENV_PROBE_TOOL_NAME, {})
        return EnvironmentSnapshot(
            hostname="demo-host",
            distro="Ubuntu 24.04",
            kernel="6.8.0",
            current_user="operator",
            is_root=self.is_root,
            sudo_available=self.sudo_available,
            available_commands=["getent", "useradd", "userdel", "ss", "ps"],
            connection_mode="local",
        )

    def disk_usage(self, executor: Any) -> ToolResult:
        self._record("disk_usage_tool", {})
        return ToolResult(tool_name="disk_usage_tool", success=True, data={"filesystems": []})

    def memory_usage(self, executor: Any, **kwargs: Any) -> ToolResult:
        self._record("memory_usage_tool", kwargs)
        return ToolResult(tool_name="memory_usage_tool", success=True, data=dict(kwargs))

    def file_search(self, executor: Any, **kwargs: Any) -> ToolResult:
        self._record("file_search_tool", kwargs)
        return ToolResult(tool_name="file_search_tool", success=True, data=dict(kwargs))

    def process_query(self, executor: Any, **kwargs: Any) -> ToolResult:
        self._record("process_query_tool", kwargs)
        return ToolResult(
            tool_name="process_query_tool",
            success=True,
            data={"status": "ok", **kwargs, "processes": [], "count": 0},
        )

    def port_query(self, executor: Any, **kwargs: Any) -> ToolResult:
        self._record("port_query_tool", kwargs)
        listeners = [
            {
                "protocol": "tcp",
                "state": "LISTEN",
                "local_address": f"0.0.0.0:{kwargs['port']}",
                "pid": 456,
                "process_name": "nginx",
                "user": "www-data",
            }
        ]
        return ToolResult(
            tool_name="port_query_tool",
            success=True,
            data={
                "status": "listening",
                "port": kwargs["port"],
                "listeners": listeners,
                "count": 1,
            },
        )

    def create_user(self, executor: Any, **kwargs: Any) -> ToolResult:
        self._record("create_user_tool", kwargs)
        return ToolResult(
            tool_name="create_user_tool",
            success=True,
            data={"status": "created", "verified": True, **kwargs},
        )

    def delete_user(self, executor: Any, **kwargs: Any) -> ToolResult:
        if self.delete_delay_seconds:
            time.sleep(self.delete_delay_seconds)
        self._record("delete_user_tool", kwargs)
        return ToolResult(
            tool_name="delete_user_tool",
            success=True,
            data={"status": "deleted", "verified_absent": True, **kwargs},
        )


def make_orchestrator(
    mocks: HardeningToolMocks,
    memory: AgentMemory | None = None,
    **kwargs: Any,
) -> ReadonlyOrchestrator:
    return ReadonlyOrchestrator(
        DummyExecutor(),
        memory=memory,
        env_probe=mocks.env_probe,
        disk_tool=mocks.disk_usage,
        memory_usage_tool_fn=mocks.memory_usage,
        file_search_tool_fn=mocks.file_search,
        process_query_tool_fn=mocks.process_query,
        port_query_tool_fn=mocks.port_query,
        create_user_tool_fn=mocks.create_user,
        delete_user_tool_fn=mocks.delete_user,
        **kwargs,
    )


def executed_tool_names(envelope: dict[str, Any]) -> list[str]:
    return [str(item.get("tool_name")) for item in envelope["execution"]["results"]]


def assert_no_unmapped_tool(envelope: dict[str, Any]) -> None:
    blobs = [str(item.get("error") or "") for item in envelope["execution"]["results"]]
    blobs.extend(str(item.get("error") or "") for item in envelope["execution"]["steps"])
    blobs.extend(str(item.get("result_summary") or "") for item in envelope.get("timeline") or [])
    blobs.append(str(envelope["result"].get("error") or ""))
    for blob in blobs:
        for fragment in NOT_WHITELISTED_FRAGMENTS:
            assert fragment not in blob, blob


# ---------------------------------------------------------------- bug 1


@pytest.mark.parametrize(
    "raw_user_input",
    [
        "创建普通用户 alice，权限和 sudo 一样",
        "创建普通用户 alice，把他放到 wheel 组里",
        "创建普通用户 alice 并让他能 sudo",
        "创建普通用户 alice，顺便给他管理员权限",
        "删除普通用户 alice，同时清掉他的 sudo 权限",
    ],
)
def test_privileged_user_request_is_refused_instead_of_silently_narrowed(
    raw_user_input: str,
) -> None:
    mocks = HardeningToolMocks()
    result = make_orchestrator(mocks).run(raw_user_input)

    assert result["risk"]["risk_level"] == "S3"
    assert result["risk"]["allow"] is False
    assert result["result"]["status"] == "refused"
    assert result["intent"]["intent"] != "create_user"
    assert mocks.calls == []


def test_privilege_request_answers_the_same_with_or_without_the_normal_user_wording() -> None:
    privileged = "创建{prefix} alice，权限和 sudo 一样"
    risks = set()
    for prefix in ["普通用户", "用户"]:
        mocks = HardeningToolMocks()
        result = make_orchestrator(mocks).run(privileged.format(prefix=prefix))
        risks.add((result["risk"]["risk_level"], result["result"]["status"]))
        assert mocks.calls == []

    assert risks == {("S3", "refused")}


def test_plain_normal_user_request_still_reaches_the_confirmation_gate() -> None:
    mocks = HardeningToolMocks()
    result = make_orchestrator(mocks).run("请创建普通用户 demo_guest")

    assert result["intent"]["intent"] == "create_user"
    assert result["intent"]["constraints"]["groups"] == []
    assert result["risk"]["risk_level"] == "S1"
    assert result["result"]["status"] == "pending_confirmation"
    assert "create_user_tool" not in [name for name, _args in mocks.calls]


def test_explicitly_declined_sudo_is_not_treated_as_a_privilege_request() -> None:
    mocks = HardeningToolMocks()
    result = make_orchestrator(mocks).run("创建普通用户 demo_guest，不要给 sudo 权限")

    assert result["intent"]["intent"] == "create_user"
    assert result["risk"]["risk_level"] == "S1"
    assert result["result"]["status"] == "pending_confirmation"


def test_orchestrator_and_parser_share_one_privilege_detector() -> None:
    text = "给 demo_guest 加 sudo 权限"

    assert looks_like_privilege_escalation(text) is True
    assert ReadonlyParser().parse(text).intent == "grant_sudo"
    assert mentions_privilege_token("把他放到 wheel 组里") is True
    assert mentions_privilege_token("如果权限足够就继续") is False


# ---------------------------------------------------------------- bug 2


def test_two_different_usernames_in_one_lifecycle_request_are_refused() -> None:
    mocks = HardeningToolMocks()
    result = make_orchestrator(mocks).run("创建普通用户 alice，然后删除普通用户 bob")

    assert result["result"]["status"] == "refused"
    assert result["risk"]["allow"] is False
    assert mocks.calls == []


def test_single_user_lifecycle_binds_every_step_to_the_same_account() -> None:
    plan = MultistepPlanner().plan("创建再删除测试用户 demo_temp")

    assert plan.supported is True
    usernames = {
        step.target.get("username")
        for step in plan.steps
        if step.intent in {"create_user", "delete_user"}
    }
    assert usernames == {"demo_temp"}


def test_workflow_username_is_bound_per_clause_not_per_request() -> None:
    clauses = split_clauses("创建普通用户 alice，然后删除普通用户 bob")

    assert [extract_username(clause) for clause in clauses] == ["alice", "bob"]


def test_bare_workflow_id_does_not_expand_into_a_write_plan() -> None:
    plan = MultistepPlanner().plan("safe user lifecycle")

    assert plan.supported is False
    assert plan.steps == []


# ---------------------------------------------------------------- bug 3


def test_confirmation_phrase_without_pending_action_never_creates_a_write_intent() -> None:
    mocks = HardeningToolMocks()
    result = make_orchestrator(mocks).run("确认删除普通用户 demo_guest")

    assert result["result"]["status"] == "refused"
    assert result["result"]["error"] == "no_pending_action_to_confirm"
    assert result["intent"]["intent"] == "unknown"
    assert result["result"].get("confirmation_text") is None
    assert mocks.calls == []


def test_replaying_a_used_confirmation_does_not_execute_the_write_again() -> None:
    mocks = HardeningToolMocks()
    orchestrator = make_orchestrator(mocks)

    orchestrator.run("请删除普通用户 demo_guest")
    executed = orchestrator.run("确认删除普通用户 demo_guest")
    replayed = orchestrator.run("确认删除普通用户 demo_guest")

    assert executed["result"]["status"] == "success"
    assert replayed["result"]["status"] == "refused"
    assert replayed["result"]["error"] == "no_pending_action_to_confirm"
    assert [name for name, _args in mocks.calls].count("delete_user_tool") == 1
    assert orchestrator.memory.pending_action is None


# ---------------------------------------------------------------- bug 4


def test_two_concurrent_confirmations_execute_the_write_exactly_once() -> None:
    mocks = HardeningToolMocks(delete_delay_seconds=0.25)
    orchestrator = make_orchestrator(mocks)
    orchestrator.run("请删除普通用户 demo_guest")

    results: list[dict[str, Any]] = []
    results_lock = threading.Lock()

    def confirm() -> None:
        envelope = orchestrator.run("确认删除普通用户 demo_guest")
        with results_lock:
            results.append(envelope)

    threads = [threading.Thread(target=confirm) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert [name for name, _args in mocks.calls].count("delete_user_tool") == 1
    statuses = sorted(envelope["result"]["status"] for envelope in results)
    assert statuses == ["refused", "success"]


def test_executing_a_claimed_action_does_not_erase_a_newer_pending_action() -> None:
    mocks = HardeningToolMocks()
    orchestrator = make_orchestrator(mocks)

    def delete_user_then_start_another_turn(executor: Any, **kwargs: Any) -> ToolResult:
        orchestrator.run("请创建普通用户 later_user")
        return mocks.delete_user(executor, **kwargs)

    orchestrator.tools["delete_user_tool"] = delete_user_then_start_another_turn
    orchestrator.run("请删除普通用户 demo_guest")
    executed = orchestrator.run("确认删除普通用户 demo_guest")

    assert executed["result"]["status"] == "success"
    pending_action = orchestrator.memory.pending_action
    assert pending_action is not None
    assert pending_action.confirmation_text == "确认创建普通用户 later_user"


def test_a_confirmation_token_id_can_only_be_claimed_once() -> None:
    token_id = new_confirmation_token_id()

    assert claim_confirmation_token_id(token_id) is True
    assert claim_confirmation_token_id(token_id) is False
    assert claim_confirmation_token_id("") is False


def test_a_pending_action_carries_a_single_use_token_id() -> None:
    mocks = HardeningToolMocks()
    orchestrator = make_orchestrator(mocks)
    orchestrator.run("请删除普通用户 demo_guest")

    pending_action = orchestrator.memory.pending_action
    assert pending_action is not None
    assert pending_action.confirmation_token is not None
    assert pending_action.confirmation_token.token_id


# ---------------------------------------------------------------- bug 5


def test_a_skipped_guarded_write_step_is_not_reported_as_success() -> None:
    mocks = HardeningToolMocks(sudo_available=False, is_root=False)
    result = make_orchestrator(mocks).run("先探测环境，如果权限足够再创建普通用户 demo_guest")

    timeline = result["timeline"]
    assert [(item["intent"], item["status"]) for item in timeline] == [
        ("env_probe", "success"),
        ("create_user", "skipped"),
    ]
    assert result["result"]["status"] == "incomplete"
    assert result["execution"]["status"] == "incomplete"
    assert "环境权限不足" in result["result"]["error"]
    assert result["recovery"] is not None

    outcome = next(
        item
        for item in result["evidence_chain"]["state_assertions"]
        if item["name"] == "final_outcome"
    )
    assert outcome["passed"] is False
    assert "create_user_tool" not in [name for name, _args in mocks.calls]


def test_a_fully_executed_continuous_plan_is_still_reported_as_success() -> None:
    mocks = HardeningToolMocks()
    result = make_orchestrator(mocks).run("先看 8080 端口再看对应进程")

    assert result["result"]["status"] == "success"
    assert executed_tool_names(result) == ["port_query_tool", "process_query_tool"]


# ---------------------------------------------------------------- bug 6


def test_every_shipped_template_tool_maps_to_a_whitelisted_orchestrator_tool() -> None:
    orchestrator = make_orchestrator(HardeningToolMocks())
    whitelisted = set(orchestrator.tools) | {ENV_PROBE_TOOL_NAME}

    for template in load_workflow_templates().values():
        for step in template.steps:
            intent = WORKFLOW_TOOL_INTENTS.get(step.tool_name)
            assert intent is not None, f"{template.workflow_id}:{step.tool_name}"
            assert tool_name_for_intent(intent) in whitelisted


def test_template_requests_cover_every_shipped_template() -> None:
    assert set(TEMPLATE_REQUESTS) == set(load_workflow_templates())


@pytest.mark.parametrize("workflow_id", sorted(TEMPLATE_REQUESTS))
def test_every_shipped_template_runs_without_an_unmapped_tool(workflow_id: str) -> None:
    mocks = HardeningToolMocks()
    result = make_orchestrator(mocks).run(TEMPLATE_REQUESTS[workflow_id])

    assert_no_unmapped_tool(result)
    assert result["result"]["status"] in {"success", "pending_confirmation"}


def test_multi_step_readonly_requests_run_their_whitelisted_tools() -> None:
    mocks = HardeningToolMocks()
    disk_result = make_orchestrator(mocks).run("先查看磁盘再查看内存")

    assert disk_result["result"]["status"] == "success"
    assert "disk_usage_tool" in executed_tool_names(disk_result)

    search_mocks = HardeningToolMocks()
    search_result = make_orchestrator(search_mocks).run(
        "先查找 /var/log 下的 nginx 日志文件再查看磁盘"
    )

    assert search_result["result"]["status"] == "success"
    assert "file_search_tool" in executed_tool_names(search_result)
    file_args = dict(search_mocks.calls[0][1])
    assert file_args["base_path"] == "/var/log"
    assert file_args["name_contains"] == "nginx"


# ---------------------------------------------------------------- bug 7


def test_an_unexpected_internal_error_becomes_a_refused_envelope() -> None:
    class ExplodingParser(ReadonlyParser):
        def parse(self, raw_user_input: str, memory: Any | None = None) -> ParsedIntent:
            raise RuntimeError("boom")

    mocks = HardeningToolMocks()
    result = make_orchestrator(mocks, parser=ExplodingParser()).run("查一下磁盘")

    assert result["result"]["status"] == "refused"
    assert "boom" in result["result"]["error"]
    assert result["risk"]["allow"] is False
    assert result["evidence_chain"]["events"]
    assert mocks.calls == []


def test_a_denied_decision_is_refused_even_when_it_is_not_labelled_s3() -> None:
    mocks = HardeningToolMocks()
    result = make_orchestrator(mocks).run("请把整个系统重装一遍")

    assert result["risk"]["allow"] is False
    assert result["result"]["status"] == "refused"
    assert result["execution"]["results"] == []
    assert mocks.calls == []


def test_llm_candidates_with_unsafe_targets_are_rejected() -> None:
    def unsafe_llm_parser(raw_user_input: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "ok",
            "candidates": [
                {
                    "intent": "search_files",
                    "target": {"path": "-newermt"},
                    "constraints": {},
                    "requires_write": False,
                    "raw_user_input": raw_user_input,
                    "confidence": 0.9,
                }
            ],
            "reason": "stub",
        }

    mocks = HardeningToolMocks()
    result = make_orchestrator(mocks, llm_parser_fn=unsafe_llm_parser).run("随便看看点什么")

    assert result["intent"]["intent"] == "unknown"
    assert result["intent"]["target"]["path"] is None
    assert result["result"]["status"] == "refused"
    assert mocks.calls == []


@pytest.mark.parametrize(
    ("parsed_intent", "expected_rule_id"),
    [
        (
            ParsedIntent(
                intent="query_process",
                target=IntentTarget(path="/proc"),
                raw_user_input="看看 /proc 里的进程",
            ),
            "path.deep_search_refused",
        ),
        (
            ParsedIntent(
                intent="query_audit",
                target=IntentTarget(path="/"),
                raw_user_input="从根目录开始盘点",
            ),
            "path.full_disk_search_refused",
        ),
        (
            ParsedIntent(
                intent="inspect_boot",
                target=IntentTarget(path="/boot"),
                raw_user_input="看看 /boot",
            ),
            "path.protected_write_denied",
        ),
    ],
)
def test_policy_simulator_reports_the_rule_the_engine_applied(
    parsed_intent: ParsedIntent,
    expected_rule_id: str,
) -> None:
    risk = evaluate_policy(parsed_intent)
    simulator = build_policy_simulator(
        parsed_intent=parsed_intent,
        risk=risk,
        policy_version="test-policy-version",
    )

    assert risk.allow is False
    assert simulator["allow"] is False
    assert simulator["matched_rules"][0]["rule_id"] == expected_rule_id
    assert simulator["matched_rules"][0]["outcome"] == "deny"


def test_unknown_operation_refusal_is_explained_in_chinese() -> None:
    mocks = HardeningToolMocks()
    result = make_orchestrator(mocks).run("帮我把这台机器的天气预报调出来")

    assert result["result"]["status"] == "refused"
    assert "未识别的操作默认拒绝" in result["explanation"]


def test_partial_file_search_says_the_traversal_was_incomplete() -> None:
    summary = ReadonlySummarizer().summarize(
        ParsedIntent(
            intent="search_files",
            target=IntentTarget(path="/var/log"),
            raw_user_input="找日志",
        ),
        status="success",
        tool_result=ToolResult(
            tool_name="file_search_tool",
            success=True,
            data={
                "base_path": "/var/log",
                "count": 3,
                "truncated": False,
                "partial": True,
                "warnings": ["find: '/var/log/private': Permission denied"],
            },
        ),
    )

    assert "不完整" in summary
    assert "Permission denied" in summary


# ---------------------------------------------------------------- bug 8


@pytest.mark.parametrize(
    ("raw_user_input", "expected_port"),
    [
        ("查一下 8080 端口", 8080),
        ("端口 8080 现在是谁在占用", 8080),
        ("port 8080 有没有监听", 8080),
        ("查一下 123456 端口", None),
        ("端口 70000 有没有监听", None),
    ],
)
def test_port_extraction_handles_word_order_and_never_truncates(
    raw_user_input: str,
    expected_port: int | None,
) -> None:
    assert extract_port(raw_user_input) == expected_port
    assert ReadonlyParser().parse(raw_user_input).target.port == expected_port


def test_a_port_query_written_the_other_way_round_still_executes() -> None:
    mocks = HardeningToolMocks()
    result = make_orchestrator(mocks).run("端口 8080 现在是谁在占用")

    assert result["result"]["status"] == "success"
    assert ("port_query_tool", {"port": 8080}) in mocks.calls


def test_an_unconfirmed_user_does_not_resolve_a_later_context_reference() -> None:
    mocks = HardeningToolMocks()
    memory = AgentMemory()
    orchestrator = make_orchestrator(mocks, memory)

    orchestrator.run("请创建普通用户 demo_guest")
    orchestrator.run("取消")
    result = orchestrator.run("删除刚才那个用户")

    assert memory.last_username == "demo_guest"
    assert memory.last_username_unconfirmed is True
    assert result["result"]["status"] == "refused"
    assert result["intent"]["constraints"]["unresolved_context_ref"] == "username"
    assert "delete_user_tool" not in [name for name, _args in mocks.calls]


def test_a_confirmed_user_still_resolves_a_later_context_reference() -> None:
    mocks = HardeningToolMocks()
    memory = AgentMemory()
    orchestrator = make_orchestrator(mocks, memory)

    orchestrator.run("请创建普通用户 demo_guest")
    orchestrator.run("确认创建普通用户 demo_guest")

    assert memory.last_username_unconfirmed is False
    result = orchestrator.run("删除刚才那个用户")
    assert result["intent"]["target"]["username"] == "demo_guest"


def test_expired_and_missing_confirmation_tokens_are_environment_drift() -> None:
    for error_code in [
        "confirmation_token_expired",
        "missing_confirmation_token",
        "confirmation_token_already_used",
    ]:
        recovery = build_recovery_suggestion(
            parsed_intent={"intent": "delete_user", "requires_write": True},
            environment={"status": "not_collected"},
            risk={"risk_level": "S2", "allow": False},
            plan={"status": "refused", "steps": []},
            execution={"status": "skipped", "steps": [], "results": []},
            result={"status": "refused", "data": None, "error": error_code},
        )
        assert recovery is not None
        assert recovery["failure_type"] == FAILURE_ENVIRONMENT_DRIFT


def test_a_non_whitelisted_tool_still_appears_in_execution_results() -> None:
    mocks = HardeningToolMocks()
    orchestrator = make_orchestrator(mocks)
    orchestrator.tools.pop("disk_usage_tool")

    result = orchestrator.run("看看磁盘空间怎么样")

    assert result["result"]["status"] == "failed"
    assert len(result["execution"]["results"]) == len(result["execution"]["steps"])
    assert executed_tool_names(result)[-1] == "disk_usage_tool"


def test_an_unevaluable_condition_never_lets_a_write_step_run() -> None:
    step = PlanStep(
        step_id="step_2",
        intent="create_user",
        target={"username": "demo_guest"},
        condition="policy_allows_create_user and confirmation_received",
        description="workflow condition the code layer cannot evaluate",
        write_step=True,
        requires_confirmation=True,
    )

    reason = _condition_skip_reason(step, {"status": "ok", "snapshot": {}}, {})

    assert reason is not None
    assert "policy_allows_create_user" in reason


def test_a_pending_turn_reports_the_environment_probe_it_actually_ran() -> None:
    mocks = HardeningToolMocks()
    result = make_orchestrator(mocks).run("请创建普通用户 demo_guest")

    assert result["result"]["status"] == "pending_confirmation"
    assert result["environment"]["reason"] == "readonly_env_probe_for_confirmation_binding"
    assert result["environment"]["status"] in {"ok", "error"}


# ---------------------------------------------------------------- bug 9


def test_confirmation_status_has_one_shared_implementation() -> None:
    from app.agent.orchestrator import _evidence_confirmation_status
    from app.agent.summarizer import _confirmation_status
    from app.models import PolicyDecision, RiskLevel

    risk = PolicyDecision(
        risk_level=RiskLevel.S2,
        allow=False,
        requires_confirmation=True,
        reasons=["pending action requires exact confirmation text"],
    )
    plan = {"status": "pending_confirmation", "steps": []}
    execution = {"status": "skipped", "steps": [], "results": []}
    result = {"status": "pending_confirmation", "error": "confirmation_token_expired"}

    shared = confirmation_status_from_parts(
        risk=risk.model_dump(mode="json"),
        plan=plan,
        execution=execution,
        result=result,
        timeline=[],
    )
    assert shared == "mismatch"
    assert (
        _evidence_confirmation_status(
            risk=risk,
            plan_payload=plan,
            execution=execution,
            result=result,
            timeline=[],
        )
        == shared
    )
    assert (
        _confirmation_status(
            risk_data=risk.model_dump(mode="json"),
            plan_data=plan,
            execution_data=execution,
            result_data=result,
            timeline=[],
        )
        == shared
    )


def test_parser_planner_and_orchestrator_share_the_same_extractors() -> None:
    import app.agent.extraction as extraction
    import app.agent.orchestrator as orchestrator
    import app.agent.parser as parser
    import app.agent.planner as planner

    assert parser._extract_port is extraction.extract_port
    assert planner._extract_port is extraction.extract_port
    assert parser._extract_path is extraction.extract_path
    assert planner._extract_path is extraction.extract_path
    assert planner._extract_username is extraction.extract_username
    assert parser.looks_like_privilege_escalation is extraction.looks_like_privilege_escalation
    assert orchestrator._contains_any is extraction.contains_any
