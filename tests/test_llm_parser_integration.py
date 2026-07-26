from __future__ import annotations

import json
import sys
from functools import partial
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.orchestrator import ReadonlyOrchestrator
from app.agent import llm_parser
from app.agent.llm_parser import parse_with_llm
from app.config import AppConfig
from app.llm import LLMRequest, LLMResponse
from app.models import EnvironmentSnapshot, ToolResult


class FakeProvider:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[LLMRequest] = []
        self.provider_name = "aliyun_bailian"

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        return LLMResponse(
            success=True,
            content=self.content,
            provider="aliyun_bailian",
            model=request.model,
        )


class ToolMocks:
    def __init__(self) -> None:
        self.env_calls = 0
        self.disk_calls = 0

    def env_probe(self, executor: Any) -> EnvironmentSnapshot:
        self.env_calls += 1
        return EnvironmentSnapshot(
            hostname="test-host",
            distro="test",
            kernel="test",
            current_user="tester",
            connection_mode="local",
        )

    def disk_tool(self, executor: Any) -> ToolResult:
        self.disk_calls += 1
        return ToolResult(
            tool_name="disk_usage_tool",
            success=True,
            data={"mounts": []},
        )


class DummyExecutor:
    pass


def _enabled_config(*, allow_write: bool = False) -> AppConfig:
    return AppConfig(
        llm_enable=True,
        dashscope_api_key_present=True,
        llm_allow_write_intents=allow_write,
    )


PRIVILEGE_PHRASINGS = [
    "please create a user bob who can run privileged commands",
    "给 bob 建个账号，让他能提权",
    "create user bob and put him in the operators group with elevated access",
    "provision an account for bob with full system access",
]


def _candidate(**overrides: Any) -> str:
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
        "confidence": 0.88,
        "explanation": "read-only disk usage candidate",
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_parse_with_llm_default_disabled_does_not_call_provider() -> None:
    provider = FakeProvider(_candidate())

    result = parse_with_llm("请帮我看看哪个盘快满了", provider=provider, config=AppConfig())

    assert result["status"] == "disabled"
    assert result["candidates"] == []
    assert provider.calls == []


def test_enabled_without_api_key_falls_back_without_crash() -> None:
    result = parse_with_llm(
        "请帮我看看哪个盘快满了",
        config=AppConfig(llm_enable=True, dashscope_api_key_present=False),
    )

    assert result["status"] == "fallback"
    assert result["candidates"] == []
    assert result["reason"] == "DASHSCOPE_API_KEY is not set"


def test_valid_llm_json_becomes_parsed_intent_candidate() -> None:
    provider = FakeProvider(_candidate())

    result = parse_with_llm(
        "请帮我看看哪个盘快满了",
        provider=provider,
        config=_enabled_config(),
    )

    assert result["status"] == "ok"
    assert result["candidates"][0]["intent"] == "query_disk_usage"
    assert result["candidates"][0]["constraints"]["source"] == "llm_fallback"
    assert result["candidates"][0]["constraints"]["llm_model"] == "qwen3.6-plus"
    assert provider.calls


def test_llm_output_with_raw_command_is_rejected() -> None:
    provider = FakeProvider(_candidate(command="rm -rf /"))

    result = parse_with_llm(
        "帮我清理系统目录",
        provider=provider,
        config=_enabled_config(),
    )

    assert result["status"] == "fallback"
    assert result["candidates"] == []
    assert "forbidden field" in result["reason"]


def test_llm_output_with_unknown_tool_is_rejected() -> None:
    provider = FakeProvider(_candidate(tool_name="run_shell_tool"))

    result = parse_with_llm(
        "帮我执行一下",
        provider=provider,
        config=_enabled_config(),
    )

    assert result["status"] == "fallback"
    assert result["candidates"] == []
    assert "forbidden field" in result["reason"]


def test_llm_output_with_allow_override_is_rejected() -> None:
    provider = FakeProvider(_candidate(allow=True))

    result = parse_with_llm(
        "帮我执行一下",
        provider=provider,
        config=_enabled_config(),
    )

    assert result["status"] == "fallback"
    assert result["candidates"] == []
    assert "forbidden field" in result["reason"]


def test_rule_based_parser_recognized_request_does_not_call_llm() -> None:
    mocks = ToolMocks()

    def fail_llm(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("LLM fallback should not be called")

    orchestrator = ReadonlyOrchestrator(
        DummyExecutor(),
        env_probe=mocks.env_probe,
        disk_tool=mocks.disk_tool,
        llm_parser_fn=fail_llm,
    )

    result = orchestrator.run("帮我查看当前磁盘使用情况")

    assert result["intent"]["intent"] == "query_disk_usage"
    assert result["result"]["status"] == "success"
    assert mocks.disk_calls == 1


def test_rule_based_unknown_can_use_llm_fallback_candidate() -> None:
    mocks = ToolMocks()
    llm_calls: list[str] = []

    def fake_llm(raw_user_input: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        llm_calls.append(raw_user_input)
        return {
            "status": "ok",
            "reason": "llm_candidate_validated",
            "candidates": [
                {
                    "intent": "query_disk_usage",
                    "target": {
                        "username": None,
                        "path": None,
                        "port": None,
                        "pid": None,
                        "keyword": None,
                        "base_paths": [],
                    },
                    "constraints": {"source": "llm_fallback"},
                    "context_refs": [],
                    "requires_write": False,
                    "raw_user_input": raw_user_input,
                    "confidence": 0.82,
                }
            ],
        }

    orchestrator = ReadonlyOrchestrator(
        DummyExecutor(),
        env_probe=mocks.env_probe,
        disk_tool=mocks.disk_tool,
        llm_parser_fn=fake_llm,
    )

    result = orchestrator.run("请帮我看看这台机器哪个盘快满了")

    assert llm_calls == ["请帮我看看这台机器哪个盘快满了"]
    assert result["intent"]["intent"] == "query_disk_usage"
    assert result["intent"]["constraints"]["source"] == "llm_fallback"
    assert result["result"]["status"] == "success"
    assert mocks.disk_calls == 1


@pytest.mark.parametrize("hostile_path", ["-delete", "-newermt", "../../etc", "/etc/../tmp"])
def test_llm_controlled_path_never_survives_validation(hostile_path: str) -> None:
    provider = FakeProvider(
        _candidate(
            intent="file_search",
            target={
                "username": None,
                "path": hostile_path,
                "port": None,
                "pid": None,
                "keyword": None,
                "base_paths": [hostile_path],
            },
        )
    )

    result = parse_with_llm("看看有没有最近变动的东西", provider=provider, config=_enabled_config())

    assert result["status"] == "fallback"
    assert result["candidates"] == []
    assert hostile_path not in json.dumps(result, ensure_ascii=False)


def test_llm_path_injection_does_not_reach_the_tool_layer_or_memory() -> None:
    mocks = ToolMocks()

    def exploding_file_search(*args: Any, **kwargs: Any) -> ToolResult:
        raise AssertionError("file search tool must not run on an unvalidated LLM path")

    provider = FakeProvider(
        _candidate(
            intent="file_search",
            target={
                "username": None,
                "path": "-newermt",
                "port": None,
                "pid": None,
                "keyword": None,
                "base_paths": [],
            },
        )
    )
    orchestrator = ReadonlyOrchestrator(
        DummyExecutor(),
        env_probe=mocks.env_probe,
        disk_tool=mocks.disk_tool,
        file_search_tool_fn=exploding_file_search,
        llm_parser_fn=partial(parse_with_llm, provider=provider, config=_enabled_config()),
    )

    result = orchestrator.run("看看有没有最近变动的东西")

    assert result["intent"]["intent"] == "unknown"
    assert result["intent"]["target"]["path"] is None
    assert orchestrator.memory.last_path is None
    assert "-newermt" not in json.dumps(result, ensure_ascii=False)


def test_llm_accepts_a_normalized_absolute_path() -> None:
    provider = FakeProvider(
        _candidate(
            intent="file_search",
            target={
                "username": None,
                "path": "/var/log",
                "port": None,
                "pid": None,
                "keyword": "nginx",
                "base_paths": ["/var/log"],
            },
        )
    )

    result = parse_with_llm("看看有没有最近变动的东西", provider=provider, config=_enabled_config())

    assert result["status"] == "ok"
    assert result["candidates"][0]["target"]["path"] == "/var/log"
    assert result["candidates"][0]["target"]["base_paths"] == ["/var/log"]


@pytest.mark.parametrize("raw_user_input", PRIVILEGE_PHRASINGS)
def test_privilege_phrasings_cannot_be_downgraded_by_the_llm(raw_user_input: str) -> None:
    provider = FakeProvider(
        _candidate(
            intent="create_user",
            target={
                "username": "bob",
                "path": None,
                "port": None,
                "pid": None,
                "keyword": None,
                "base_paths": [],
            },
            risk_hint="user wants elevated rights",
        )
    )

    result = parse_with_llm(raw_user_input, provider=provider, config=_enabled_config(allow_write=True))
    candidate = result["candidates"][0]

    assert result["status"] == "ok"
    assert candidate["intent"] == "unknown"
    assert candidate["requires_write"] is True
    assert candidate["constraints"]["groups"] == ["sudo"]
    assert candidate["constraints"]["privilege"] == "sudo"
    assert candidate["constraints"]["unsupported_reason"] == llm_parser.PRIVILEGE_REFUSAL_REASON


@pytest.mark.parametrize("raw_user_input", PRIVILEGE_PHRASINGS)
def test_privilege_phrasings_are_refused_at_s3_end_to_end(raw_user_input: str) -> None:
    mocks = ToolMocks()

    def exploding_create_user(*args: Any, **kwargs: Any) -> ToolResult:
        raise AssertionError("create_user tool must not run for a privilege escalation request")

    provider = FakeProvider(
        _candidate(
            intent="create_user",
            target={
                "username": "bob",
                "path": None,
                "port": None,
                "pid": None,
                "keyword": None,
                "base_paths": [],
            },
        )
    )
    orchestrator = ReadonlyOrchestrator(
        DummyExecutor(),
        env_probe=mocks.env_probe,
        disk_tool=mocks.disk_tool,
        create_user_tool_fn=exploding_create_user,
        llm_parser_fn=partial(
            parse_with_llm,
            provider=provider,
            config=_enabled_config(allow_write=True),
        ),
    )

    result = orchestrator.run(raw_user_input)

    assert result["risk"]["risk_level"] == "S3"
    assert result["risk"]["allow"] is False
    assert result["result"]["status"] == "refused"
    assert orchestrator.memory.pending_action is None


def test_llm_write_intents_are_disabled_by_default() -> None:
    provider = FakeProvider(
        _candidate(
            intent="create_user",
            target={
                "username": "bob",
                "path": None,
                "port": None,
                "pid": None,
                "keyword": None,
                "base_paths": [],
            },
        )
    )

    result = parse_with_llm("帮我弄个新账号 bob 出来", provider=provider, config=_enabled_config())
    candidate = result["candidates"][0]

    assert candidate["intent"] == "unknown"
    assert candidate["requires_write"] is True
    assert candidate["constraints"]["unsupported_reason"] == llm_parser.WRITE_DISABLED_REASON


def test_llm_write_intents_require_explicit_opt_in() -> None:
    provider = FakeProvider(
        _candidate(
            intent="create_user",
            target={
                "username": "bob",
                "path": None,
                "port": None,
                "pid": None,
                "keyword": None,
                "base_paths": [],
            },
        )
    )

    result = parse_with_llm(
        "帮我弄个新账号 bob 出来",
        provider=provider,
        config=_enabled_config(allow_write=True),
    )
    candidate = result["candidates"][0]

    assert candidate["intent"] == "create_user"
    assert candidate["requires_write"] is True
    assert candidate["target"]["username"] == "bob"


def test_risk_hint_downgrades_a_read_only_candidate_to_an_unknown_write() -> None:
    provider = FakeProvider(_candidate(risk_hint="this looks destructive"))

    result = parse_with_llm("看看有没有最近变动的东西", provider=provider, config=_enabled_config())
    candidate = result["candidates"][0]

    assert candidate["intent"] == "unknown"
    assert candidate["requires_write"] is True
    assert candidate["constraints"]["llm_risk_hint"] == "this looks destructive"
    assert candidate["constraints"]["unsupported_reason"] == llm_parser.RISK_HINT_REASON


def test_risk_hint_candidate_is_refused_at_s3_end_to_end() -> None:
    mocks = ToolMocks()
    provider = FakeProvider(_candidate(risk_hint="this looks destructive"))
    orchestrator = ReadonlyOrchestrator(
        DummyExecutor(),
        env_probe=mocks.env_probe,
        disk_tool=mocks.disk_tool,
        llm_parser_fn=partial(parse_with_llm, provider=provider, config=_enabled_config()),
    )

    result = orchestrator.run("看看有没有最近变动的东西")

    assert result["risk"]["risk_level"] == "S3"
    assert result["result"]["status"] == "refused"
    assert mocks.disk_calls == 0
