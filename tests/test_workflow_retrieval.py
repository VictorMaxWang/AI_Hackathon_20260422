from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.planner import plan_multistep
from app.evolution.workflows import match_workflow_template
from app.models.intent import PlanStep


def test_workflow_id_exact_match_is_supported() -> None:
    match = match_workflow_template("safe_disk_triage")

    assert match is not None
    assert match.workflow_id == "safe_disk_triage"


def test_keyword_or_tag_match_is_supported() -> None:
    match = match_workflow_template("please do disk triage safely")

    assert match is not None
    assert match.workflow_id == "safe_disk_triage"


def test_disk_request_matches_safe_disk_triage() -> None:
    plan = plan_multistep("帮我安全检查磁盘")

    assert plan.supported is True
    assert [step.intent for step in plan.steps] == ["env_probe", "query_disk_usage"]
    assert _workflow_ids(plan) == {"safe_disk_triage"}
    assert all(step.target["source"] == "workflow_template" for step in plan.steps)
    assert all(step.target["risk_level"] == "S0" for step in plan.steps)


def test_file_search_request_matches_safe_file_search() -> None:
    plan = plan_multistep("帮我按安全方式找 nginx 日志")

    assert plan.supported is True
    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert step.intent == "search_files"
    assert step.target["workflow_id"] == "safe_file_search"
    assert step.target["tool_name"] == "file_search_tool"
    assert step.target["base_path"] == "/var/log"
    assert step.target["name_contains"] == "nginx"
    assert step.target["max_results"] == 20
    assert step.target["max_depth"] == 4


def test_port_owner_request_matches_diagnose_port_owner() -> None:
    plan = plan_multistep("查 8080 端口是谁占用")

    assert plan.supported is True
    assert [step.intent for step in plan.steps] == ["query_port", "query_process"]
    assert _workflow_ids(plan) == {"diagnose_port_owner"}
    assert plan.steps[0].target["port"] == 8080
    assert plan.steps[1].target["port"] == 8080
    assert plan.steps[1].depends_on == ["step_1"]
    assert plan.steps[1].condition == "step_1.listener_found"


def test_user_lifecycle_request_matches_safe_user_lifecycle() -> None:
    plan = plan_multistep("创建再删除测试用户 demo_temp")

    assert plan.supported is True
    assert [step.intent for step in plan.steps] == [
        "env_probe",
        "create_user",
        "delete_user",
    ]
    assert _workflow_ids(plan) == {"safe_user_lifecycle"}

    create_step = plan.steps[1]
    assert create_step.target["username"] == "demo_temp"
    assert create_step.target["risk_level"] == "S1"
    assert create_step.requires_policy is True
    assert create_step.requires_confirmation is True

    delete_step = plan.steps[2]
    assert delete_step.target["username"] == "demo_temp"
    assert delete_step.target["risk_level"] == "S2"
    assert delete_step.target["remove_home"] is False
    assert delete_step.requires_policy is True
    assert delete_step.requires_confirmation is True


def test_unrelated_request_does_not_match_workflow() -> None:
    plan = plan_multistep("帮我写一份周报")

    assert plan.supported is False
    assert plan.status == "unsupported"
    assert plan.steps == []
    assert plan.reason == "unsupported multi-step pattern"


def test_workflow_derived_plan_does_not_execute_tools() -> None:
    plan = plan_multistep("查 8080 端口是谁占用")
    payload = plan.model_dump()

    assert all(isinstance(step, PlanStep) for step in plan.steps)
    assert set(payload) == {"raw_user_input", "status", "supported", "steps", "reason"}
    assert "execution" not in payload
    assert "tool_calls" not in payload
    assert all("args" not in step for step in payload["steps"])


def _workflow_ids(plan) -> set[str]:
    return {
        step.target["workflow_id"]
        for step in plan.steps
        if step.target.get("source") == "workflow_template"
    }
