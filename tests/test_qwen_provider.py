from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import AppConfig
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


def test_qwen_provider_logs_failures_without_leaking_the_api_key(caplog: Any) -> None:
    completions = ExplodingCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    provider = QwenProvider(
        api_key="sk-super-secret-key",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen3.6-plus",
        timeout_seconds=12,
        client=client,
    )

    with caplog.at_level(logging.WARNING, logger="app.llm.qwen_provider"):
        provider.complete(_request())

    assert "code=RuntimeError" in caplog.text
    assert "sk-super-secret-key" not in caplog.text
    assert "fake-secret-value" not in caplog.text


def test_qwen_provider_empty_content_is_a_structured_error() -> None:
    completions = FakeCompletions("   ")
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
    assert response.error_code == "empty_response"


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(choices=[]),
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content={"text": "hi"}))]),
        SimpleNamespace(choices=[SimpleNamespace(message=None)]),
    ],
)
def test_qwen_provider_rejects_unusable_response_shapes(response: Any) -> None:
    class ShapedCompletions:
        def create(self, **kwargs: Any) -> Any:
            return response

    provider = QwenProvider(
        api_key="fake-key",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen3.6-plus",
        timeout_seconds=12,
        client=SimpleNamespace(chat=SimpleNamespace(completions=ShapedCompletions())),
    )

    result = provider.complete(_request())

    assert result.success is False
    assert result.error_code == "empty_response"


def test_qwen_provider_builds_the_client_once_and_pins_retries() -> None:
    completions = FakeCompletions('{"intent":"disk_usage"}')
    factory_calls: list[dict[str, Any]] = []

    def factory(**kwargs: Any) -> Any:
        factory_calls.append(kwargs)
        return SimpleNamespace(chat=SimpleNamespace(completions=completions))

    provider = QwenProvider(
        api_key="fake-key",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen3.6-plus",
        timeout_seconds=12,
        client_factory=factory,
    )

    assert provider.complete(_request()).success is True
    assert provider.complete(_request()).success is True

    assert len(factory_calls) == 1
    assert factory_calls[0]["max_retries"] == 0
    assert factory_calls[0]["timeout"] == 12
    assert provider._client_factory is factory


def test_qwen_provider_reports_missing_openai_dependency(monkeypatch: Any) -> None:
    monkeypatch.setitem(sys.modules, "openai", None)
    provider = QwenProvider(
        api_key="fake-key",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen3.6-plus",
        timeout_seconds=12,
    )

    response = provider.complete(_request())

    assert response.success is False
    assert response.error_code == "openai_dependency_missing"


def test_qwen_provider_from_config_reads_the_key_from_the_environment(monkeypatch: Any) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", " fake-env-key ")
    config = AppConfig(
        llm_enable=True,
        llm_base_url="https://example.invalid/v1",
        llm_model="qwen3.6-plus",
        llm_timeout_seconds=7,
        dashscope_api_key_present=True,
    )

    provider = QwenProvider.from_config(config)

    assert provider.base_url == "https://example.invalid/v1"
    assert provider.model == "qwen3.6-plus"
    assert provider.timeout_seconds == 7
    assert provider.max_retries == 0
    assert provider._api_key == "fake-env-key"


def test_qwen_provider_from_config_without_key_never_calls_out(monkeypatch: Any) -> None:
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    provider = QwenProvider.from_config(AppConfig(llm_enable=True))

    assert provider.complete(_request()).error_code == "missing_api_key"
