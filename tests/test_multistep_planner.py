from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.memory import AgentMemory
from app.agent.planner import MultistepPlanner, plan_multistep


def test_environment_probe_then_conditional_create_user_plan() -> None:
    plan = plan_multistep("先查看环境，如果权限足够，创建普通用户 demo_temp")

    assert plan.supported is True
    assert plan.status == "supported"
    assert plan.raw_user_input == "先查看环境，如果权限足够，创建普通用户 demo_temp"
    assert [step.intent for step in plan.steps] == ["env_probe", "create_user"]

    create_step = plan.steps[1]
    assert create_step.step_id == "step_2"
    assert create_step.target == {
        "username": "demo_temp",
        "create_home": True,
        "no_sudo": True,
    }
    assert create_step.depends_on == ["step_1"]
    assert create_step.condition == "env.sudo_available or env.is_root"
    assert create_step.requires_policy is True
    assert create_step.requires_confirmation is True


def test_port_query_then_corresponding_process_plan() -> None:
    plan = MultistepPlanner().plan("先查 8080 端口，再告诉我对应的进程")

    assert plan.supported is True
    assert [step.intent for step in plan.steps] == ["query_port", "query_process"]
    assert plan.steps[0].target == {"port": 8080}
    assert plan.steps[1].target == {
        "port": 8080,
        "from_step": "step_1",
        "pid_from": "step_1.listeners[0].pid",
    }
    assert plan.steps[1].depends_on == ["step_1"]
    assert plan.steps[1].condition == "step_1.listener_found"
    assert plan.steps[1].requires_confirmation is False


def test_memory_referenced_delete_user_plan() -> None:
    memory = AgentMemory(last_username="demo_temp")

    plan = plan_multistep("确认它存在后删除刚才那个用户", memory=memory)

    assert plan.supported is True
    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert step.intent == "delete_user"
    assert step.target == {
        "username": "demo_temp",
        "remove_home": False,
        "resolved_from_memory": True,
    }
    assert step.condition == "target_user_exists and target_user_uid >= 1000"
    assert step.requires_policy is True
    assert step.requires_confirmation is True


def test_unsupported_complex_task_returns_unsupported() -> None:
    plan = plan_multistep("先安装 nginx，再开放防火墙，然后重启服务")

    assert plan.supported is False
    assert plan.status == "unsupported"
    assert plan.steps == []
    assert plan.reason == "unsupported or unsafe multi-step request"


def test_plan_steps_contain_required_structure_fields() -> None:
    plan = plan_multistep("先查 8080 端口，再告诉我对应的进程")

    required_fields = {
        "step_id",
        "intent",
        "target",
        "depends_on",
        "condition",
        "description",
    }
    for step_payload in plan.model_dump()["steps"]:
        assert required_fields <= set(step_payload)

    assert {"raw_user_input", "steps", "supported", "status"} <= set(plan.model_dump())
