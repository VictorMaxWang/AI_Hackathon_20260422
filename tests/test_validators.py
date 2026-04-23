from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.policy import SYSTEM_USERNAMES, validate_username, validate_username_with_reasons


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
