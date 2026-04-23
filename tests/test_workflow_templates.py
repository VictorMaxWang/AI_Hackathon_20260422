from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evolution.workflows import (
    DEFAULT_TEMPLATE_DIR,
    WorkflowTemplateLoadError,
    load_workflow_template,
    load_workflow_templates,
)


EXPECTED_WORKFLOW_IDS = {
    "safe_disk_triage",
    "safe_file_search",
    "diagnose_port_owner",
    "safe_user_lifecycle",
}
BANNED_RAW_FIELDS = {
    "argv",
    "bash",
    "cmd",
    "command",
    "commands",
    "executable",
    "raw_command",
    "raw_shell",
    "script",
    "shell",
}
BANNED_TEXT_FRAGMENTS = (
    "run_shell_tool",
    "execute_command_tool",
    "bash_tool",
    "rm -rf",
    "bash -c",
    "sh -c",
    "systemctl ",
    "iptables ",
    "ufw ",
    "kill -",
    "&&",
    "||",
)


def test_four_workflow_templates_can_load() -> None:
    templates = load_workflow_templates()

    assert EXPECTED_WORKFLOW_IDS <= set(templates)
    for workflow_id in EXPECTED_WORKFLOW_IDS:
        assert load_workflow_template(workflow_id).workflow_id == workflow_id


def test_templates_contain_allowed_tools_and_steps_stay_within_them() -> None:
    templates = load_workflow_templates()

    for template in templates.values():
        assert template.allowed_tools
        allowed_tools = set(template.allowed_tools)
        assert all(step.tool_name in allowed_tools for step in template.steps)


def test_templates_do_not_contain_shell_or_raw_command_content() -> None:
    for template_path in DEFAULT_TEMPLATE_DIR.glob("*.json"):
        payload = json.loads(template_path.read_text(encoding="utf-8"))
        _assert_no_raw_command_content(payload, template_path.name)


def test_safe_user_lifecycle_requires_confirmation_and_keeps_policy_gates() -> None:
    template = load_workflow_template("safe_user_lifecycle")

    assert template.requires_confirmation is True

    create_step = _step_by_id(template, "create_normal_user")
    delete_step = _step_by_id(template, "delete_normal_user")

    assert create_step.requires_policy is True
    assert create_step.requires_confirmation is True
    assert delete_step.requires_policy is True
    assert delete_step.requires_confirmation is True
    assert delete_step.constraints["remove_home_default"] is False
    assert template.post_checks


def test_diagnose_port_owner_forbids_kill_restart_and_firewall_changes() -> None:
    template = load_workflow_template("diagnose_port_owner")
    forbidden_actions = " ".join(template.forbidden_actions).lower()

    assert "kill" in forbidden_actions
    assert "restart" in forbidden_actions
    assert "firewall" in forbidden_actions


def test_safe_file_search_declares_bounded_search_constraints() -> None:
    template = load_workflow_template("safe_file_search")
    step = _step_by_id(template, "bounded_file_search")
    constraints = step.constraints

    assert constraints["base_path"]["required"] is True
    assert constraints["max_results"]["required"] is True
    assert constraints["max_results"]["default"] == 20
    assert constraints["max_results"]["maximum"] == 50
    assert constraints["max_depth"]["required"] is True
    assert constraints["max_depth"]["default"] == 4
    assert constraints["max_depth"]["maximum"] == 8


def test_loader_reports_missing_template_with_clear_error(tmp_path: Path) -> None:
    with pytest.raises(WorkflowTemplateLoadError, match="workflow template not found"):
        load_workflow_template("missing_template", templates_dir=tmp_path)


def _step_by_id(template, step_id: str):
    for step in template.steps:
        if step.step_id == step_id:
            return step
    raise AssertionError(f"missing step {step_id}")


def _assert_no_raw_command_content(value, path: str) -> None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            assert str(key).lower() not in BANNED_RAW_FIELDS, f"{path}.{key}"
            _assert_no_raw_command_content(nested_value, f"{path}.{key}")
        return

    if isinstance(value, list):
        for index, nested_value in enumerate(value):
            _assert_no_raw_command_content(nested_value, f"{path}[{index}]")
        return

    if isinstance(value, str):
        lowered = value.lower()
        for fragment in BANNED_TEXT_FRAGMENTS:
            assert fragment not in lowered, f"{path}: {value}"
