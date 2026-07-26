from __future__ import annotations

import importlib
import os
import socket
import sys
import time
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.config import AppConfig, load_config
from app.llm.qwen_provider import QwenProvider


BLOCKED_BASE_URL = "http://192.0.2.1:9/v1"


def test_llm_parser_stub_can_import() -> None:
    module = importlib.import_module("app.agent.llm_parser")

    assert hasattr(module, "parse_with_llm")
    assert load_config({}).llm_enable is False


def test_parse_with_llm_returns_predictable_disabled_result() -> None:
    from app.agent.llm_parser import parse_with_llm

    result = parse_with_llm("查一下 8080 端口", context={"session_id": "test"})

    assert result["status"] == "disabled"
    assert result["candidates"] == []
    assert result["reason"] == "LLM parser is disabled in this build."


def test_llm_environment_is_scrubbed_for_the_whole_suite() -> None:
    assert os.environ.get("DASHSCOPE_API_KEY") is None
    assert [name for name in os.environ if name.startswith("GUARDEDOPS_LLM_")] == []
    assert load_config().llm_enable is False


def test_network_guard_blocks_non_loopback_connections() -> None:
    with pytest.raises(RuntimeError) as excinfo:
        socket.create_connection(("192.0.2.1", 9), timeout=1)

    assert "outbound network access is blocked" in str(excinfo.value)


def test_enabled_llm_provider_cannot_reach_the_network_from_tests() -> None:
    from app.agent.llm_parser import parse_with_llm

    pytest.importorskip("openai")
    provider = QwenProvider(
        api_key="fake-key-never-sent",
        base_url=BLOCKED_BASE_URL,
        model="qwen3.6-plus",
        timeout_seconds=2,
    )
    started = time.monotonic()

    result = parse_with_llm(
        "帮我查看当前磁盘使用情况",
        provider=provider,
        config=AppConfig(llm_enable=True, dashscope_api_key_present=True),
    )

    assert result["status"] == "fallback"
    assert result["candidates"] == []
    assert result["reason"].startswith("provider_error:")
    assert time.monotonic() - started < 10.0


def test_parse_with_llm_does_not_open_network_socket() -> None:
    from app.agent.llm_parser import parse_with_llm

    result = parse_with_llm("帮我查看当前磁盘使用情况")

    assert result["status"] == "disabled"
    assert result["candidates"] == []


def test_core_prompt_document_is_present_and_contains_safety_contract() -> None:
    prompt_path = Path(__file__).resolve().parents[1] / "docs" / "core_prompt.md"

    assert prompt_path.exists()

    content = prompt_path.read_text(encoding="utf-8")
    required_terms = [
        "Prompt 不是安全边界",
        "JSON",
        "不得直接生成 bash",
        "不得绕过 policy engine",
        "不得直接驱动执行层",
        "allow/deny",
    ]

    for term in required_terms:
        assert term in content
