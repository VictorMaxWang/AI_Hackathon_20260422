from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import llm_parser
from app.agent.llm_parser import parse_with_llm
from app.config import AppConfig
from app.llm import LLMRequest, LLMResponse
from app.models import IntentTarget, ParsedIntent, PolicyDecision, RiskLevel


NEUTRAL_REQUEST = "请帮我看看哪个盘快满了"


class FakeProvider:
    provider_name = "aliyun_bailian"

    def __init__(self, content: str, *, success: bool = True, error_code: str | None = None) -> None:
        self.content = content
        self.success = success
        self.error_code = error_code
        self.calls: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        return LLMResponse(
            success=self.success,
            content=self.content,
            provider=self.provider_name,
            model=request.model,
            error_code=self.error_code,
        )


def _enabled_config(*, allow_write: bool = False) -> AppConfig:
    return AppConfig(
        llm_enable=True,
        dashscope_api_key_present=True,
        llm_allow_write_intents=allow_write,
    )


def _payload(**overrides: Any) -> str:
    payload: dict[str, Any] = {
        "intent": "disk_usage",
        "target": {
            "username": None,
            "path": None,
            "port": None,
            "pid": None,
            "keyword": None,
            "base_paths": [],
        },
        "constraints": {},
        "context_refs": [],
        "requires_write": False,
        "risk_hint": None,
        "confidence": 0.8,
        "explanation": "read-only disk usage candidate",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def _with_target(**target_overrides: Any) -> str:
    target: dict[str, Any] = {
        "username": None,
        "path": None,
        "port": None,
        "pid": None,
        "keyword": None,
        "base_paths": [],
    }
    target.update(target_overrides)
    return _payload(intent="file_search", target=target)


def _run(content: str, *, raw_user_input: str = NEUTRAL_REQUEST, allow_write: bool = False) -> dict[str, Any]:
    return dict(
        parse_with_llm(
            raw_user_input,
            provider=FakeProvider(content),
            config=_enabled_config(allow_write=allow_write),
        )
    )


HOSTILE_CASES: list[tuple[str, str, str]] = [
    (
        "prose_prefixed_json",
        'Sure! Here is the JSON you asked for:\n{"intent": "disk_usage"}',
        "LLM output is not valid JSON",
    ),
    (
        "non_dict_payload",
        '"disk_usage"',
        "LLM output must be a JSON object",
    ),
    (
        "empty_object",
        "{}",
        "LLM output contains unsupported intent",
    ),
    (
        "readonly_intent_marked_as_write",
        _payload(requires_write=True),
        "LLM marked a read-only intent as write",
    ),
    (
        "target_is_a_string",
        _payload(target="/var/log"),
        "LLM target must be an object",
    ),
    (
        "base_paths_is_a_string",
        _with_target(base_paths="/etc"),
        "LLM target.base_paths must be a list",
    ),
    (
        "base_paths_has_too_many_entries",
        _with_target(base_paths=[f"/var/log/{index}" for index in range(9)]),
        "LLM target.base_paths contains too many entries",
    ),
    (
        "base_paths_entry_is_null",
        _with_target(base_paths=[None]),
        "LLM target.base_paths[0] must not be null",
    ),
    (
        "base_paths_entry_is_relative",
        _with_target(base_paths=["etc/passwd"]),
        "LLM target.base_paths[0] must be an absolute path",
    ),
    (
        "constraints_is_a_list",
        _payload(constraints=[]),
        "LLM constraints must be an object",
    ),
    (
        "constraints_has_too_many_keys",
        _payload(constraints={f"key_{index}": index for index in range(25)}),
        "LLM constraints contains too many keys",
    ),
    (
        "explanation_carries_shell_text",
        _payload(explanation="just run rm -rf /tmp/cache first"),
        "LLM output contains command-like text at $.explanation",
    ),
    (
        "context_refs_carry_shell_text",
        _payload(context_refs=["please use bash -c ls"]),
        "LLM output contains command-like text at $.context_refs[0]",
    ),
    (
        "forbidden_key_in_payload",
        _payload(command="df -h"),
        "LLM output contains forbidden field at $.command",
    ),
    (
        "path_is_a_find_option",
        _with_target(path="-delete"),
        "LLM target.path must not start with an option dash",
    ),
    (
        "path_is_relative",
        _with_target(path="../../etc"),
        "LLM target.path must be an absolute path",
    ),
    (
        "path_is_not_normalized",
        _with_target(path="/etc/../tmp"),
        "LLM target.path is not in normalized form",
    ),
    (
        "path_has_empty_segment",
        _with_target(path="//etc"),
        "LLM target.path contains an empty path segment",
    ),
    (
        "path_has_surrounding_whitespace",
        _with_target(path=" /var/log"),
        "LLM target.path has surrounding whitespace",
    ),
    (
        "path_has_control_characters",
        _with_target(path="/var/log\x01"),
        "LLM target.path contains control characters",
    ),
    (
        "path_is_empty",
        _with_target(path=""),
        "LLM target.path must not be empty",
    ),
    (
        "path_is_too_long",
        _with_target(path="/" + "a" * 512),
        "LLM target.path is longer than 512 characters",
    ),
    (
        "path_is_not_a_string",
        _with_target(path=17),
        "LLM target.path must be a string",
    ),
    (
        "username_is_not_a_string",
        _payload(target={"username": 17, "base_paths": []}),
        "LLM target.username must be a string",
    ),
    (
        "username_is_too_long",
        _payload(target={"username": "u" * 65, "base_paths": []}),
        "LLM target.username is longer than 64 characters",
    ),
    (
        "keyword_has_control_characters",
        _with_target(keyword="log\tname"),
        "LLM target.keyword contains control characters",
    ),
    (
        "port_is_out_of_range",
        _payload(intent="port_query", target={"port": 99999, "base_paths": []}),
        "LLM output failed ParsedIntent schema validation",
    ),
    (
        "intent_is_unsupported_value",
        _payload(intent="reboot_host"),
        "LLM output contains unsupported intent",
    ),
    (
        "intent_is_unsupported_sentinel",
        _payload(intent="unsupported"),
        "LLM returned unsupported intent",
    ),
]


@pytest.mark.parametrize(
    ("content", "expected_reason"),
    [(content, reason) for _, content, reason in HOSTILE_CASES],
    ids=[case_id for case_id, _, _ in HOSTILE_CASES],
)
def test_hostile_llm_payload_falls_back_with_a_precise_reason(
    content: str,
    expected_reason: str,
) -> None:
    result = _run(content)

    assert result["status"] == "fallback"
    assert result["candidates"] == []
    assert result["reason"] == expected_reason


def test_hostile_cases_do_not_share_reasons() -> None:
    reasons = [reason for _, _, reason in HOSTILE_CASES]
    duplicated = {reason for reason in reasons if reasons.count(reason) > 1}

    assert duplicated == {"LLM output contains unsupported intent"}


@pytest.mark.parametrize(
    "hostile_path",
    ["-delete", "-L", "-newermt", "--help", "-exec"],
)
def test_every_find_option_shaped_path_is_refused(hostile_path: str) -> None:
    result = _run(_with_target(path=hostile_path))

    assert result["status"] == "fallback"
    assert result["reason"] == "LLM target.path must not start with an option dash"


@pytest.mark.parametrize(
    "content",
    ['[1, 2]', "[]", '{"candidates": "disk_usage"}', "null"],
)
def test_non_object_payload_shapes_never_produce_a_candidate(content: str) -> None:
    result = _run(content)

    assert result["status"] == "fallback"
    assert result["candidates"] == []


def test_pid_out_of_range_fails_schema_validation() -> None:
    result = _run(_payload(intent="process_query", target={"pid": -1, "base_paths": []}))

    assert result["status"] == "fallback"
    assert result["reason"] == "LLM output failed ParsedIntent schema validation"


def test_fenced_json_is_accepted() -> None:
    result = _run('```json\n' + _payload() + '\n```')

    assert result["status"] == "ok"
    assert result["candidates"][0]["intent"] == "query_disk_usage"


def test_candidates_wrapper_is_unwrapped() -> None:
    result = _run(json.dumps({"candidates": [json.loads(_payload())]}, ensure_ascii=False))

    assert result["status"] == "ok"
    assert result["candidates"][0]["intent"] == "query_disk_usage"


def test_bare_candidate_list_is_unwrapped() -> None:
    result = _run(json.dumps([json.loads(_payload())], ensure_ascii=False))

    assert result["status"] == "ok"
    assert result["candidates"][0]["intent"] == "query_disk_usage"


def test_empty_candidates_wrapper_is_refused() -> None:
    result = _run('{"candidates": []}')

    assert result["status"] == "fallback"
    assert result["reason"] == "LLM output contains unsupported intent"


def test_provider_failure_is_reported_as_provider_error() -> None:
    provider = FakeProvider(None, success=False, error_code="rate_limited")

    result = parse_with_llm(NEUTRAL_REQUEST, provider=provider, config=_enabled_config())

    assert result["status"] == "fallback"
    assert result["reason"] == "provider_error:rate_limited"


def test_provider_failure_without_error_code_is_still_reported() -> None:
    provider = FakeProvider(None, success=False)

    result = parse_with_llm(NEUTRAL_REQUEST, provider=provider, config=_enabled_config())

    assert result["reason"] == "provider_error:unknown"


def test_unsupported_provider_is_refused_before_any_call() -> None:
    provider = FakeProvider(_payload())

    result = parse_with_llm(
        NEUTRAL_REQUEST,
        provider=provider,
        config=AppConfig(llm_enable=True, llm_provider="openai", dashscope_api_key_present=True),
    )

    assert result["status"] == "fallback"
    assert result["reason"] == "unsupported LLM provider: openai"
    assert provider.calls == []


def test_high_risk_request_becomes_an_unknown_write() -> None:
    result = _run(_payload(intent="high_risk_request", risk_hint="user asks for wide deletion"))
    candidate = result["candidates"][0]

    assert result["status"] == "ok"
    assert candidate["intent"] == "unknown"
    assert candidate["requires_write"] is True
    assert candidate["constraints"]["llm_risk_hint"] == "user asks for wide deletion"
    assert candidate["constraints"]["unsupported_reason"] == llm_parser.HIGH_RISK_REASON


@pytest.mark.parametrize(
    ("raw_confidence", "expected"),
    [(-5, 0.0), (5, 1.0), ("abc", 0.0), (None, 0.0), ("0.5", 0.5), (0.42, 0.42)],
)
def test_confidence_is_clamped(raw_confidence: Any, expected: float) -> None:
    result = _run(_payload(confidence=raw_confidence))

    assert result["candidates"][0]["confidence"] == expected


@pytest.mark.parametrize(
    ("raw_refs", "expected"),
    [
        ("刚才那个目录", ["刚才那个目录"]),
        ([None, " 上一个端口 ", ""], ["上一个端口"]),
        ([f"ref-{index}" for index in range(12)], [f"ref-{index}" for index in range(8)]),
        (None, []),
    ],
)
def test_context_refs_are_coerced_and_bounded(raw_refs: Any, expected: list[str]) -> None:
    result = _run(_payload(context_refs=raw_refs))

    assert result["candidates"][0]["context_refs"] == expected


def test_long_llm_strings_are_capped_before_they_reach_the_evidence_chain() -> None:
    result = _run(_payload(explanation="x" * 900, constraints={"note": "y" * 900}))
    constraints = result["candidates"][0]["constraints"]

    assert len(constraints["llm_explanation"]) == 240
    assert len(constraints["note"]) == 240


def test_minimal_payload_without_target_or_constraints_is_accepted() -> None:
    result = _run('{"intent": "memory_usage"}')
    candidate = result["candidates"][0]

    assert result["status"] == "ok"
    assert candidate["intent"] == "query_memory_usage"
    assert candidate["target"]["base_paths"] == []
    assert candidate["constraints"]["source"] == "llm_fallback"


def test_blank_target_strings_become_null() -> None:
    result = _run(_payload(target={"username": "   ", "keyword": "", "base_paths": []}))
    candidate = result["candidates"][0]

    assert candidate["target"]["username"] is None
    assert candidate["target"]["keyword"] is None


def test_nested_constraint_values_are_capped_without_losing_structure() -> None:
    result = _run(
        _payload(
            constraints={
                "tags": ["a" * 300, "b"],
                "nested": {"note": "c" * 300},
                "limit": 20,
                "flag": True,
            }
        )
    )
    constraints = result["candidates"][0]["constraints"]

    assert constraints["tags"] == ["a" * 240, "b"]
    assert constraints["nested"] == {"note": "c" * 240}
    assert constraints["limit"] == 20
    assert constraints["flag"] is True


def test_confidence_rejects_nan() -> None:
    result = _run(_payload(confidence=float("nan")))

    assert result["candidates"][0]["confidence"] == 0.0


def test_privilege_signal_is_false_for_empty_input() -> None:
    assert llm_parser._looks_like_privilege_request("") is False
    assert llm_parser._looks_like_privilege_request(None) is False


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ({"target": {"path": "/var/log", "base_paths": ["/var/log"]}}, True),
        ({"target": None}, True),
        ({}, True),
        ({"target": {"path": "-delete"}}, False),
        ({"target": {"base_paths": ["../../etc"]}}, False),
        ({"target": {"path": "//etc"}}, False),
        ({"target": "/var/log"}, False),
        ("not-a-candidate", False),
    ],
)
def test_candidate_target_is_safe_rejects_smuggled_paths(candidate: Any, expected: bool) -> None:
    assert llm_parser.candidate_target_is_safe(candidate) is expected


def test_policy_boundary_rejects_intents_outside_the_whitelist() -> None:
    parsed = ParsedIntent(intent="query_audit", target=IntentTarget(), raw_user_input=NEUTRAL_REQUEST)

    with pytest.raises(ValueError, match="outside the whitelist"):
        llm_parser._validate_policy_and_tool_boundary(parsed)


def test_policy_boundary_rejects_readonly_intent_without_a_whitelisted_tool(monkeypatch: Any) -> None:
    monkeypatch.delitem(llm_parser.INTENT_TOOL_WHITELIST, "query_port")
    parsed = ParsedIntent(intent="query_port", target=IntentTarget(port=8080), raw_user_input=NEUTRAL_REQUEST)

    with pytest.raises(ValueError, match="no whitelisted read-only tool"):
        llm_parser._validate_policy_and_tool_boundary(parsed)


def _decision(risk_level: RiskLevel, *, allow: bool, requires_confirmation: bool) -> PolicyDecision:
    return PolicyDecision(
        risk_level=risk_level,
        allow=allow,
        requires_confirmation=requires_confirmation,
        confirmation_text=None,
        reasons=["stub"],
        safe_alternative=None,
    )


def test_policy_boundary_rejects_a_write_allowed_without_confirmation(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        llm_parser,
        "evaluate_policy",
        lambda parsed: _decision(RiskLevel.S0, allow=True, requires_confirmation=False),
    )
    parsed = ParsedIntent(
        intent="create_user",
        target=IntentTarget(username="demo_guest"),
        requires_write=True,
        raw_user_input=NEUTRAL_REQUEST,
    )

    with pytest.raises(ValueError, match="allowed write without confirmation"):
        llm_parser._validate_policy_and_tool_boundary(parsed)


def test_policy_boundary_rejects_an_unconfirmed_decision_above_s0(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        llm_parser,
        "evaluate_policy",
        lambda parsed: _decision(RiskLevel.S2, allow=True, requires_confirmation=False),
    )
    parsed = ParsedIntent(
        intent="query_disk_usage",
        target=IntentTarget(),
        raw_user_input=NEUTRAL_REQUEST,
    )

    with pytest.raises(ValueError, match="allowed above S0 without confirmation"):
        llm_parser._validate_policy_and_tool_boundary(parsed)


def test_policy_boundary_rejects_s0_allowed_intent_outside_the_tool_whitelist(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        llm_parser,
        "evaluate_policy",
        lambda parsed: _decision(RiskLevel.S0, allow=True, requires_confirmation=False),
    )
    parsed = ParsedIntent(
        intent="create_user",
        target=IntentTarget(username="demo_guest"),
        raw_user_input=NEUTRAL_REQUEST,
    )

    with pytest.raises(ValueError, match="S0-allowed outside the tool whitelist"):
        llm_parser._validate_policy_and_tool_boundary(parsed)


def test_policy_boundary_accepts_a_refused_candidate_unchanged() -> None:
    parsed = ParsedIntent(
        intent="unknown",
        target=IntentTarget(),
        constraints={"groups": ["sudo"]},
        requires_write=True,
        raw_user_input=NEUTRAL_REQUEST,
    )

    llm_parser._validate_policy_and_tool_boundary(parsed)
