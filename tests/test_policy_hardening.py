from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.agent.confirmation import (
    CREATE_USER_CONFIRMATION_TEMPLATE,
    DELETE_USER_CONFIRMATION_TEMPLATE,
    PendingAction,
)
from app.models import RiskLevel
from app.policy import evaluate
from app.policy.rules import (
    CANONICAL_READ_ONLY_INTENTS,
    CONFIRM_CREATE_USER,
    CONFIRM_DELETE_USER,
    CREATE_USER_INTENTS,
    DELETE_USER_INTENTS,
    INTENT_ALIASES,
    READ_ONLY_INTENTS,
    contains_destructive_word,
    contains_write_word,
    is_protected_path,
    normalize_group_tokens,
    normalize_intent_name,
    normalize_path,
)


REFUSED_SCAN_PATHS = ("/", "//", "///", "/.", "/..", "/proc", "//proc", "/dev", "/sys", "/./proc", "/var/../proc")


def _assert_refused(decision: Any, context: str) -> None:
    assert decision.risk_level == RiskLevel.S3, context
    assert decision.allow is False, context
    assert decision.requires_confirmation is False, context
    assert decision.safe_alternative, context


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("//", "/"),
        ("///", "/"),
        ("////proc", "/proc"),
        ("//proc", "/proc"),
        ("/.", "/"),
        ("/..", "/"),
        ("/./proc", "/proc"),
        ("/var/../proc", "/proc"),
        ("/etc/../etc/sudoers", "/etc/sudoers"),
        ("/etc/", "/etc"),
        ("/etc//ssh///sshd_config", "/etc/ssh/sshd_config"),
        ("  /var/log  ", "/var/log"),
    ],
)
def test_normalize_path_collapses_leading_and_relative_segments(raw: str, expected: str) -> None:
    assert normalize_path(raw) == expected


def test_normalize_path_rejects_unusable_input() -> None:
    assert normalize_path(None) is None
    assert normalize_path("") is None
    assert normalize_path("   ") is None
    assert normalize_path(123) is None


@pytest.mark.parametrize("path", ["//", "///", "/.", "/..", "/./", "//."])
def test_double_slash_root_search_is_refused(path: str) -> None:
    _assert_refused(evaluate({"intent": "search_files", "target": {"path": path}}), path)


@pytest.mark.parametrize("path", ["//proc", "///proc", "/./proc", "/var/../proc", "//sys", "//dev/shm"])
def test_double_slash_virtual_filesystem_search_is_refused(path: str) -> None:
    _assert_refused(evaluate({"intent": "search_files", "target": {"path": path}}), path)


@pytest.mark.parametrize("path", ["//etc/sudoers", "/etc/../etc/sudoers", "//etc//sudoers.d/90-cloud"])
def test_double_slash_sudoers_path_is_refused(path: str) -> None:
    decision = evaluate({"intent": "search_files", "target": {"path": path}})
    _assert_refused(decision, path)
    assert any("sudoers" in reason for reason in decision.reasons)


def test_every_read_only_alias_canonicalizes_into_the_canonical_set() -> None:
    for intent_name in READ_ONLY_INTENTS:
        assert normalize_intent_name(intent_name) in CANONICAL_READ_ONLY_INTENTS


def test_every_user_intent_alias_canonicalizes() -> None:
    for intent_name in CREATE_USER_INTENTS:
        assert normalize_intent_name(intent_name) == "create_user"
    for intent_name in DELETE_USER_INTENTS:
        assert normalize_intent_name(intent_name) == "delete_user"


def test_intent_alias_targets_are_canonical() -> None:
    for alias, canonical in INTENT_ALIASES.items():
        assert alias != canonical
        assert canonical not in INTENT_ALIASES


@pytest.mark.parametrize("intent_name", sorted(READ_ONLY_INTENTS))
@pytest.mark.parametrize("path", REFUSED_SCAN_PATHS)
def test_read_only_aliases_refuse_root_and_virtual_scans(intent_name: str, path: str) -> None:
    context = f"{intent_name} {path}"
    _assert_refused(evaluate({"intent": intent_name, "target": {"path": path}}), context)
    _assert_refused(evaluate({"intent": intent_name, "constraints": {"base_path": path}}), context)
    _assert_refused(evaluate({"intent": intent_name, "target": {"base_paths": [path]}}), context)
    _assert_refused(evaluate({"intent": intent_name, "base_paths": [path]}), context)


@pytest.mark.parametrize("intent_name", sorted(READ_ONLY_INTENTS))
def test_read_only_aliases_stay_s0_on_a_bounded_path(intent_name: str) -> None:
    decision = evaluate({"intent": intent_name, "target": {"path": "/var/log"}})

    assert decision.risk_level == RiskLevel.S0
    assert decision.allow is True
    assert decision.requires_confirmation is False


@pytest.mark.parametrize(
    "intent_name",
    [
        "deleteallconfigs",
        "deleteUser",
        "delete.user",
        "deletefile",
        "erase_etc",
        "format_disk",
        "kill_process",
        "overwrite_file",
        "mkfs_disk",
        "reboot_host",
        "shutdown_host",
        "mount_disk",
        "install_pkg",
        "grant_access",
        "reset_password",
        "flush_iptables",
        "unlink_file",
        "exec_script",
    ],
)
def test_unrecognized_destructive_intents_are_s3_without_a_write_flag(intent_name: str) -> None:
    _assert_refused(
        evaluate({"intent": intent_name, "target": {"path": "/etc"}, "requires_write": False}),
        intent_name,
    )
    _assert_refused(evaluate({"intent": intent_name, "requires_write": False}), intent_name)


def test_unknown_intent_without_path_is_s3() -> None:
    _assert_refused(evaluate({"intent": "unknown"}), "unknown")
    _assert_refused(evaluate({"intent": ""}), "empty")
    _assert_refused(evaluate("not-a-dict"), "scalar")


def test_no_decision_is_s0_and_denied() -> None:
    payloads: list[dict[str, Any]] = [
        {"intent": "unknown"},
        {"intent": "patch_config", "target": {"path": "/etc/passwd"}},
        {"intent": "kill_process"},
        {"intent": "search_files", "target": {"path": "/var/log"}},
    ]
    for payload in payloads:
        decision = evaluate(payload)
        assert not (decision.risk_level == RiskLevel.S0 and decision.allow is False), payload


@pytest.mark.parametrize("requires_write", [True, False])
def test_protected_path_rules_ignore_the_model_supplied_write_flag(requires_write: bool) -> None:
    decision = evaluate(
        {
            "intent": "patch_config",
            "target": {"path": "/etc/passwd"},
            "requires_write": requires_write,
        }
    )

    _assert_refused(decision, f"requires_write={requires_write}")


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "/boot/grub", "/usr/bin", "/root", "/root/.ssh", "/var/spool/cron", "/var/spool/cron/root", "/opt"],
)
def test_protected_paths_are_refused_for_non_read_only_intents(path: str) -> None:
    _assert_refused(evaluate({"intent": "patch_config", "target": {"path": path}}), path)


def test_top_level_entries_are_protected_but_children_are_not() -> None:
    assert is_protected_path("/") is True
    assert is_protected_path("//") is True
    assert is_protected_path("/initrd.img") is True
    assert is_protected_path("/home") is True
    assert is_protected_path("/root") is True
    assert is_protected_path("/var/spool/cron") is True
    assert is_protected_path("/home/demo") is False
    assert is_protected_path("/var/log") is False


@pytest.mark.parametrize(
    "constraints",
    [
        {"groups": "sudo,wheel"},
        {"groups": ["sudo,wheel"]},
        {"groups": " SUDO "},
        {"groups": ["docker"]},
        {"groups": ["lxd"]},
        {"groups": ["shadow"]},
        {"groups": ["adm"]},
        {"groups": ["0"]},
        {"groups": [0]},
        {"supplementary_groups": ["wheel"]},
        {"supplementary_groups": "sudo,wheel"},
        {"add_groups": ["root"]},
        {"secondary_groups": "admin"},
        {"extra_groups": ["wheel"]},
        {"group": "sudo"},
        {"role": "administrator"},
        {"privilege": "root"},
    ],
)
def test_privileged_group_shapes_are_refused(constraints: dict[str, Any]) -> None:
    decision = evaluate(
        {
            "intent": "create_user",
            "target": {"username": "demo_guest"},
            "constraints": constraints,
            "requires_write": True,
        }
    )

    _assert_refused(decision, str(constraints))


@pytest.mark.parametrize("key", ["groups", "add_groups", "supplementary_groups"])
def test_top_level_group_keys_are_not_dropped(key: str) -> None:
    decision = evaluate(
        {
            "intent": "create_user",
            "target": {"username": "demo_guest"},
            key: ["wheel"],
            "requires_write": True,
        }
    )

    _assert_refused(decision, key)


def test_top_level_base_paths_are_not_dropped() -> None:
    _assert_refused(evaluate({"intent": "search_files", "base_paths": ["/proc"]}), "base_paths")
    _assert_refused(evaluate({"intent": "search_files", "base_paths": "//"}), "base_paths string")


def test_normal_group_membership_still_allowed() -> None:
    decision = evaluate(
        {
            "intent": "create_user",
            "target": {"username": "demo_guest"},
            "constraints": {"groups": ["demo", "developers"]},
            "requires_write": True,
        }
    )

    assert decision.risk_level == RiskLevel.S1
    assert decision.allow is True
    assert decision.requires_confirmation is True


def test_normalize_group_tokens_flattens_every_shape() -> None:
    assert normalize_group_tokens("sudo,wheel") == frozenset({"sudo", "wheel"})
    assert normalize_group_tokens(["Sudo", " wheel "]) == frozenset({"sudo", "wheel"})
    assert normalize_group_tokens("sudo wheel;admin") == frozenset({"sudo", "wheel", "admin"})
    assert normalize_group_tokens(None) == frozenset()
    assert normalize_group_tokens([]) == frozenset()


def test_confirmation_text_matches_the_real_confirmation_gate() -> None:
    create_decision = evaluate(
        {
            "intent": "create_user",
            "target": {"username": "demo_guest"},
            "requires_write": True,
        }
    )
    delete_decision = evaluate(
        {
            "intent": "delete_user",
            "target": {"username": "demo_guest"},
            "requires_write": True,
        }
    )

    assert CONFIRM_CREATE_USER == CREATE_USER_CONFIRMATION_TEMPLATE
    assert CONFIRM_DELETE_USER == DELETE_USER_CONFIRMATION_TEMPLATE

    for decision, risk_level in ((create_decision, RiskLevel.S1), (delete_decision, RiskLevel.S2)):
        pending = PendingAction(
            intent="create_user" if risk_level == RiskLevel.S1 else "delete_user",
            target={"username": "demo_guest"},
            risk_level=risk_level,
            confirmation_text=decision.confirmation_text or "",
            tool_name="create_user_tool" if risk_level == RiskLevel.S1 else "delete_user_tool",
        )
        assert pending.matches_confirmation(decision.confirmation_text or "")


@pytest.mark.parametrize("path", ["/etc/sudoers", "/etc/sudoers.d/90-cloud", "/etc/ssh/sshd_config"])
def test_safe_alternative_never_suggests_a_path_the_engine_refuses(path: str) -> None:
    decision = evaluate({"intent": "modify_file", "target": {"path": path}, "requires_write": True})

    assert decision.safe_alternative
    read_only_inspection = evaluate({"intent": "search_files", "target": {"path": path}})
    assert read_only_inspection.allow is False
    assert path not in decision.safe_alternative
    assert "sudoers" not in decision.safe_alternative
    assert "sshd_config" not in decision.safe_alternative


def test_sshd_config_safe_alternative_points_at_an_allowed_action() -> None:
    decision = evaluate(
        {"intent": "modify_file", "target": {"path": "/etc/ssh/sshd_config"}, "requires_write": True}
    )

    assert decision.safe_alternative
    assert "/var/log" in decision.safe_alternative
    assert evaluate({"intent": "search_files", "target": {"path": "/var/log"}}).allow is True


@pytest.mark.parametrize("recursive", [True, 1, "true", "yes", "-R", "--recursive", "on"])
def test_bulk_permission_change_detects_non_boolean_recursive_flags(recursive: Any) -> None:
    decision = evaluate(
        {
            "intent": "chmod_files",
            "target": {"path": "/home/demo"},
            "constraints": {"recursive": recursive},
            "requires_write": True,
        }
    )

    _assert_refused(decision, str(recursive))
    assert any("permission" in reason for reason in decision.reasons)


@pytest.mark.parametrize("recursive", [False, 0, "false", "no", "", None])
def test_non_recursive_permission_change_is_not_reported_as_bulk(recursive: Any) -> None:
    decision = evaluate(
        {
            "intent": "chmod_files",
            "target": {"path": "/home/demo"},
            "constraints": {"recursive": recursive},
            "requires_write": True,
        }
    )

    assert decision.risk_level == RiskLevel.S3
    assert not any("bulk chmod" in reason for reason in decision.reasons)


@pytest.mark.parametrize(
    "intent_name",
    ["deleteallconfigs", "deleteUser", "delete.user", "erase-etc", "OVERWRITE_FILE", "mkfs_disk", "shred.data"],
)
def test_contains_destructive_word_sees_unseparated_and_dotted_names(intent_name: str) -> None:
    assert contains_destructive_word(intent_name) is True
    assert contains_write_word(intent_name) is True


@pytest.mark.parametrize("intent_name", sorted(CANONICAL_READ_ONLY_INTENTS))
def test_canonical_read_only_intents_are_not_write_like(intent_name: str) -> None:
    assert contains_write_word(intent_name) is False
    assert contains_destructive_word(intent_name) is False


@pytest.mark.parametrize("path", ["../..", "..", "proc", "./proc", "var/../../proc", "~/", "-/etc"])
def test_read_only_scope_must_be_absolute(path: str) -> None:
    _assert_refused(evaluate({"intent": "search_files", "target": {"path": path}}), path)
    _assert_refused(evaluate({"intent": "file_search_tool", "constraints": {"base_path": path}}), path)


def test_read_only_intent_with_write_flag_is_escalated_not_allowed() -> None:
    decision = evaluate(
        {
            "intent": "search_files",
            "target": {"path": "/var/log"},
            "requires_write": True,
        }
    )

    _assert_refused(decision, "search_files with requires_write")
