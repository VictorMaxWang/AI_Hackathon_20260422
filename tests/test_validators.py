from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import RiskLevel
from app.policy import SYSTEM_USERNAMES, evaluate, validate_username, validate_username_with_reasons
from app.policy.validators import USERNAME_PATTERN


def test_username_injection_rejected() -> None:
    invalid_usernames = [
        "demo;rm",
        "demo/user",
        "demo`id`",
        "demo$(id)",
        "demo*",
        "demo guest",
        "demo,guest",
        "demo|guest",
        "中文用户",
    ]

    for username in invalid_usernames:
        assert validate_username(username) is False


def test_root_username_rejected() -> None:
    result = validate_username_with_reasons("root")

    assert result.valid is False
    assert any("reserved" in reason for reason in result.reasons)


def test_valid_normal_username_accepted() -> None:
    assert validate_username("demo_guest") is True
    assert validate_username("ops-user_01") is True


def test_all_system_usernames_rejected() -> None:
    for username in SYSTEM_USERNAMES:
        assert validate_username(username) is False


def test_username_regex_boundaries() -> None:
    assert validate_username("") is False
    assert validate_username("ab") is False
    assert validate_username("1demo") is False
    assert validate_username("Demo") is False
    assert validate_username("a" * 32) is True
    assert validate_username("a" * 33) is False


def test_username_pattern_subsumes_every_shell_metacharacter() -> None:
    dangerous_tokens = (
        " ",
        "\t",
        "\n",
        "\r",
        ";",
        "/",
        "\\",
        "`",
        "$(",
        ")",
        "*",
        "?",
        "[",
        "]",
        "{",
        "}",
        "|",
        "&",
        "<",
        ">",
        ",",
        ":",
        "'",
        '"',
        "!",
        "#",
        "%",
        "=",
        "~",
        "..",
    )

    for token in dangerous_tokens:
        candidate = f"demo{token}guest"
        assert USERNAME_PATTERN.fullmatch(candidate) is None, candidate
        assert validate_username(candidate) is False, candidate


def test_non_ascii_usernames_are_rejected() -> None:
    for username in ("中文用户", "démo_user", "demo​user", "демо_user"):
        assert validate_username(username) is False


def test_non_string_username_is_rejected() -> None:
    for username in (None, 123, ["demo_guest"], {"username": "demo_guest"}):
        result = validate_username_with_reasons(username)
        assert result.valid is False
        assert result.reasons


def test_whitespace_padded_username_is_rejected() -> None:
    result = validate_username_with_reasons(" demo_guest ")

    assert result.valid is False
    assert any("whitespace" in reason for reason in result.reasons)


def test_invalid_username_blocks_user_write_intents() -> None:
    for intent in ("create_user", "delete_user", "add_user", "user_delete"):
        decision = evaluate(
            {
                "intent": intent,
                "target": {"username": "demo;rm -rf /"},
                "requires_write": True,
            }
        )
        assert decision.risk_level == RiskLevel.S3, intent
        assert decision.allow is False, intent
        assert decision.safe_alternative, intent
