from __future__ import annotations

import importlib
import socket
import sys
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_llm_parser_stub_can_import() -> None:
    module = importlib.import_module("app.agent.llm_parser")

    assert module.LLM_PARSER_ENABLED is False
    assert hasattr(module, "parse_with_llm")


def test_parse_with_llm_returns_predictable_disabled_result() -> None:
    from app.agent.llm_parser import parse_with_llm

    result = parse_with_llm("查一下 8080 端口", context={"session_id": "test"})

    assert result["status"] == "disabled"
    assert result["candidates"] == []
    assert result["reason"] == "LLM parser is disabled in this build."


def test_parse_with_llm_does_not_open_network_socket(monkeypatch: Any) -> None:
    from app.agent.llm_parser import parse_with_llm

    def fail_socket(*args: Any, **kwargs: Any) -> socket.socket:
        raise AssertionError("LLM parser stub must not open network sockets")

    monkeypatch.setattr(socket, "socket", fail_socket)

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
