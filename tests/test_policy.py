from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import IntentTarget, ParsedIntent, RiskLevel
from app.policy import evaluate


def test_refuse_delete_etc() -> None:
    decision = evaluate(
        ParsedIntent(
            intent="delete_path",
            target=IntentTarget(path="/etc"),
            requires_write=True,
        )
    )

    assert decision.risk_level == RiskLevel.S3
    assert decision.allow is False
    assert decision.requires_confirmation is False
    assert decision.safe_alternative
    assert any("/etc" in reason for reason in decision.reasons)


def test_create_user_requires_confirmation() -> None:
    decision = evaluate(
        ParsedIntent(
            intent="create_user",
            target=IntentTarget(username="demo_guest"),
            constraints={"groups": []},
            requires_write=True,
        )
    )

    assert decision.risk_level == RiskLevel.S1
    assert decision.allow is True
    assert decision.requires_confirmation is True
    assert decision.confirmation_text == "Confirm creating normal user demo_guest"
    assert decision.safe_alternative is None


def test_delete_user_requires_strong_confirmation() -> None:
    decision = evaluate(
        ParsedIntent(
            intent="delete_user",
            target=IntentTarget(username="demo_guest"),
            requires_write=True,
        )
    )

    assert decision.risk_level == RiskLevel.S2
    assert decision.allow is True
    assert decision.requires_confirmation is True
    assert decision.confirmation_text == "Confirm deleting normal user demo_guest"


def test_unknown_write_operation_denied() -> None:
    decision = evaluate(
        ParsedIntent(
            intent="restart_service",
            constraints={"service": "sshd"},
            requires_write=True,
        )
    )

    assert decision.risk_level == RiskLevel.S3
    assert decision.allow is False
    assert decision.requires_confirmation is False
    assert decision.safe_alternative
    assert any("unknown writes" in reason for reason in decision.reasons)


def test_readonly_query_allowed_as_s0() -> None:
    decision = evaluate(ParsedIntent(intent="query_disk_usage"))

    assert decision.risk_level == RiskLevel.S0
    assert decision.allow is True
    assert decision.requires_confirmation is False


def test_modify_sudoers_is_s3() -> None:
    decision = evaluate(
        {
            "intent": "modify_file",
            "target": {"path": "/etc/sudoers"},
            "requires_write": True,
        }
    )

    assert decision.risk_level == RiskLevel.S3
    assert decision.allow is False
    assert decision.safe_alternative
    assert any("sudoers" in reason for reason in decision.reasons)


def test_modify_sshd_config_is_s3() -> None:
    decision = evaluate(
        {
            "intent": "modify_file",
            "target": {"path": "/etc/ssh/sshd_config"},
            "requires_write": True,
        }
    )

    assert decision.risk_level == RiskLevel.S3
    assert decision.allow is False
    assert decision.safe_alternative
    assert any("sshd_config" in reason for reason in decision.reasons)


def test_grant_sudo_to_user_is_s3() -> None:
    decision = evaluate(
        {
            "intent": "create_user",
            "target": {"username": "demo_guest"},
            "constraints": {"groups": ["sudo"]},
            "requires_write": True,
        }
    )

    assert decision.risk_level == RiskLevel.S3
    assert decision.allow is False
    assert decision.safe_alternative


def test_bulk_permission_change_is_s3() -> None:
    decision = evaluate(
        {
            "intent": "bulk_chmod",
            "target": {"path": "/home/demo"},
            "constraints": {"recursive": True},
            "requires_write": True,
        }
    )

    assert decision.risk_level == RiskLevel.S3
    assert decision.allow is False
    assert decision.safe_alternative


def test_search_root_is_denied() -> None:
    decision = evaluate(ParsedIntent(intent="search_files", target=IntentTarget(path="/")))

    assert decision.risk_level == RiskLevel.S3
    assert decision.allow is False
    assert decision.safe_alternative


def test_search_virtual_filesystem_is_denied() -> None:
    decision = evaluate(ParsedIntent(intent="search_files", target=IntentTarget(path="/proc")))

    assert decision.risk_level == RiskLevel.S3
    assert decision.allow is False
    assert decision.safe_alternative


def test_valid_bounded_file_search_is_s0() -> None:
    decision = evaluate(
        ParsedIntent(
            intent="search_files",
            target=IntentTarget(path="/var/log"),
            constraints={"max_depth": 4, "max_results": 20},
        )
    )

    assert decision.risk_level == RiskLevel.S0
    assert decision.allow is True
