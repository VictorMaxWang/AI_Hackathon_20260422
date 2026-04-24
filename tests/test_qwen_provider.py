from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm import LLMRequest
from app.llm.qwen_provider import QwenProvider


class FakeCompletions:
    def __init__(self, content: str = '{"intent":"unsupported"}') -> None:
        self.content = content
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.content),
                )
            ]
        )


class ExplodingCompletions(FakeCompletions):
    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        raise RuntimeError("fake-secret-value must not leak")


def _request() -> LLMRequest:
    return LLMRequest(
        messages=[{"role": "user", "content": "ping"}],
        model="qwen3.6-plus",
        timeout_seconds=12,
        max_tokens=128,
        temperature=0.0,
    )


def test_qwen_provider_success_uses_openai_compatible_chat_completion() -> None:
    completions = FakeCompletions('{"intent":"disk_usage"}')
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    provider = QwenProvider(
        api_key="fake-key",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen3.6-plus",
        timeout_seconds=12,
        client=client,
    )

    response = provider.complete(_request())

    assert response.success is True
    assert response.content == '{"intent":"disk_usage"}'
    assert response.provider == "aliyun_bailian"
    assert completions.calls == [
        {
            "model": "qwen3.6-plus",
            "messages": [{"role": "user", "content": "ping"}],
            "temperature": 0.0,
            "max_tokens": 128,
            "timeout": 12,
        }
    ]


def test_qwen_provider_missing_key_returns_structured_error_without_call() -> None:
    completions = FakeCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    provider = QwenProvider(
        api_key=None,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen3.6-plus",
        timeout_seconds=12,
        client=client,
    )

    response = provider.complete(_request())

    assert response.success is False
    assert response.error_code == "missing_api_key"
    assert completions.calls == []


def test_qwen_provider_sanitizes_provider_exception() -> None:
    completions = ExplodingCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    provider = QwenProvider(
        api_key="fake-key",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen3.6-plus",
        timeout_seconds=12,
        client=client,
    )

    response = provider.complete(_request())

    assert response.success is False
    assert response.error_code == "RuntimeError"
    assert response.error_message == "LLM provider call failed"
    assert "fake-secret-value" not in repr(response)
