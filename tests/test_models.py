from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.models import (
    AuditRecord,
    CommandResult,
    EnvironmentSnapshot,
    ExecutionStatus,
    IntentTarget,
    ParsedIntent,
    PolicyDecision,
    RiskLevel,
    ToolCall,
    ToolResult,
)


def test_models_can_import() -> None:
    assert ParsedIntent
    assert IntentTarget
    assert PolicyDecision
    assert CommandResult
    assert ToolCall
    assert ToolResult
    assert EnvironmentSnapshot
    assert AuditRecord


def test_key_models_can_be_instantiated_and_json_serialized() -> None:
    parsed_intent = ParsedIntent(
        intent="query_port",
        target=IntentTarget(port=8080),
        constraints={"max_results": 20},
        context_refs=["last_host"],
        requires_write=False,
        raw_user_input="Who is using port 8080?",
        confidence=0.9,
    )

    policy = PolicyDecision(
        risk_level=RiskLevel.S0,
        allow=True,
        requires_confirmation=False,
        confirmation_text=None,
        reasons=["read-only port query"],
        safe_alternative=None,
    )

    command_result = CommandResult(
        argv=["ss", "-ltnp"],
        exit_code=0,
        stdout="LISTEN 0 4096 0.0.0.0:8080",
        stderr="",
        duration_ms=12,
        timed_out=False,
        success=True,
    )

    audit_record = AuditRecord(
        request_id="req-001",
        raw_user_input="Who is using port 8080?",
        parsed_intent=parsed_intent,
        environment_snapshot=EnvironmentSnapshot(
            hostname="demo-host",
            distro="Ubuntu 24.04",
            kernel="6.8.0",
            current_user="demo",
            is_root=False,
            sudo_available=False,
            available_commands=["ss"],
            connection_mode="local",
        ),
        risk_decision=policy,
        confirmation_status="not_required",
        tool_calls=[
            ToolCall(tool_name="port_query_tool", args={"port": 8080}),
        ],
        command_results=[command_result],
        final_status=ExecutionStatus.SUCCESS,
        final_answer="Port 8080 is listening.",
    )

    payload = json.loads(audit_record.model_dump_json())

    assert payload["request_id"] == "req-001"
    assert payload["parsed_intent"]["intent"] == "query_port"
    assert payload["risk_decision"]["risk_level"] == "S0"
    assert payload["command_results"][0]["argv"] == ["ss", "-ltnp"]
    assert payload["final_status"] == "success"


def test_policy_decision_fields_are_complete() -> None:
    expected_fields = {
        "risk_level",
        "allow",
        "requires_confirmation",
        "confirmation_text",
        "reasons",
        "safe_alternative",
    }

    assert expected_fields <= set(PolicyDecision.model_fields)


def test_command_result_fields_are_complete() -> None:
    expected_fields = {
        "argv",
        "exit_code",
        "stdout",
        "stderr",
        "duration_ms",
        "timed_out",
        "success",
    }

    assert expected_fields <= set(CommandResult.model_fields)


def test_audit_record_accepts_nested_structures() -> None:
    audit_record = AuditRecord(
        request_id="req-nested",
        raw_user_input="show disk usage",
        parsed_intent=ParsedIntent(intent="query_disk_usage"),
        environment_snapshot=EnvironmentSnapshot(
            hostname="demo-host",
            distro="Ubuntu",
            kernel="6.8.0",
            current_user="demo",
            available_commands=["df"],
            connection_mode="local",
        ),
        risk_decision=PolicyDecision(
            risk_level=RiskLevel.S0,
            allow=True,
            reasons=["read-only disk query"],
        ),
        tool_calls=[ToolCall(tool_name="disk_usage_tool", args={})],
        command_results=[
            CommandResult(argv=["df", "-h"], exit_code=0, success=True),
        ],
        final_status=ExecutionStatus.SUCCESS,
        final_answer="Disk usage collected.",
    )

    assert audit_record.parsed_intent.intent == "query_disk_usage"
    assert audit_record.environment_snapshot.connection_mode == "local"
    assert audit_record.risk_decision.risk_level == RiskLevel.S0
    assert audit_record.tool_calls[0].tool_name == "disk_usage_tool"
    assert audit_record.command_results[0].success is True


def test_raw_shell_fields_are_not_exposed_or_accepted() -> None:
    banned_fields = {
        "raw_shell_command",
        "freeform_command",
        "bash_command",
        "arbitrary_command",
    }
    model_classes = [
        IntentTarget,
        ParsedIntent,
        PolicyDecision,
        CommandResult,
        ToolCall,
        ToolResult,
        EnvironmentSnapshot,
        AuditRecord,
    ]

    for model_class in model_classes:
        assert banned_fields.isdisjoint(model_class.model_fields)

    with pytest.raises(ValidationError):
        CommandResult(raw_shell_command="rm -rf /")
