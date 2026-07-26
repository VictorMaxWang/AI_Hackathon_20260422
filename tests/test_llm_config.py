from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import (
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_LLM_TEMPERATURE,
    DEFAULT_LLM_TIMEOUT_SECONDS,
    MAX_LLM_MAX_TOKENS,
    MAX_LLM_TEMPERATURE,
    MAX_LLM_TIMEOUT_SECONDS,
    get_dashscope_api_key,
    load_config,
)


def test_llm_config_defaults_to_disabled_without_key() -> None:
    config = load_config({})

    assert config.llm_enable is False
    assert config.llm_provider == DEFAULT_LLM_PROVIDER
    assert config.llm_model == DEFAULT_LLM_MODEL
    assert config.llm_base_url == DEFAULT_LLM_BASE_URL
    assert config.dashscope_api_key_present is False


def test_llm_config_reads_env_and_repr_does_not_include_key() -> None:
    fake_key = "fake-test-key-for-repr"
    config = load_config(
        {
            "GUARDEDOPS_LLM_ENABLE": "true",
            "GUARDEDOPS_LLM_PROVIDER": "aliyun_bailian",
            "GUARDEDOPS_LLM_MODEL": "qwen3.6-plus",
            "GUARDEDOPS_LLM_BASE_URL": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            "GUARDEDOPS_LLM_TIMEOUT_SECONDS": "45",
            "GUARDEDOPS_LLM_MAX_TOKENS": "512",
            "GUARDEDOPS_LLM_TEMPERATURE": "0.2",
            "DASHSCOPE_API_KEY": fake_key,
        }
    )

    assert config.llm_enable is True
    assert config.llm_provider == "aliyun_bailian"
    assert config.llm_model == "qwen3.6-plus"
    assert config.llm_base_url == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    assert config.llm_timeout_seconds == 45
    assert config.llm_max_tokens == 512
    assert config.llm_temperature == 0.2
    assert config.dashscope_api_key_present is True
    assert fake_key not in repr(config)


def test_llm_config_invalid_numbers_fall_back_to_safe_defaults() -> None:
    config = load_config(
        {
            "GUARDEDOPS_LLM_ENABLE": "true",
            "GUARDEDOPS_LLM_TIMEOUT_SECONDS": "0",
            "GUARDEDOPS_LLM_MAX_TOKENS": "-1",
            "GUARDEDOPS_LLM_TEMPERATURE": "bad",
        }
    )

    assert config.llm_timeout_seconds == 30
    assert config.llm_max_tokens == 1024
    assert config.llm_temperature == 0.0


def test_llm_numeric_settings_have_ceilings() -> None:
    config = load_config(
        {
            "GUARDEDOPS_LLM_TIMEOUT_SECONDS": str(MAX_LLM_TIMEOUT_SECONDS + 1),
            "GUARDEDOPS_LLM_MAX_TOKENS": str(MAX_LLM_MAX_TOKENS + 1),
            "GUARDEDOPS_LLM_TEMPERATURE": str(MAX_LLM_TEMPERATURE + 0.5),
        }
    )

    assert config.llm_timeout_seconds == DEFAULT_LLM_TIMEOUT_SECONDS
    assert config.llm_max_tokens == DEFAULT_LLM_MAX_TOKENS
    assert config.llm_temperature == DEFAULT_LLM_TEMPERATURE


def test_llm_numeric_settings_accept_values_on_the_ceiling() -> None:
    config = load_config(
        {
            "GUARDEDOPS_LLM_TIMEOUT_SECONDS": str(MAX_LLM_TIMEOUT_SECONDS),
            "GUARDEDOPS_LLM_MAX_TOKENS": str(MAX_LLM_MAX_TOKENS),
            "GUARDEDOPS_LLM_TEMPERATURE": str(MAX_LLM_TEMPERATURE),
        }
    )

    assert config.llm_timeout_seconds == MAX_LLM_TIMEOUT_SECONDS
    assert config.llm_max_tokens == MAX_LLM_MAX_TOKENS
    assert config.llm_temperature == MAX_LLM_TEMPERATURE


def test_llm_enable_accepts_explicit_false_values() -> None:
    for value in ("0", "false", "no", "off", "N"):
        assert load_config({"GUARDEDOPS_LLM_ENABLE": value}).llm_enable is False
    for value in ("1", "true", "yes", "on", "Y"):
        assert load_config({"GUARDEDOPS_LLM_ENABLE": value}).llm_enable is True
    assert load_config({"GUARDEDOPS_LLM_ENABLE": "perhaps"}).llm_enable is False


def test_llm_integer_settings_ignore_unparseable_values() -> None:
    config = load_config(
        {
            "GUARDEDOPS_LLM_TIMEOUT_SECONDS": "thirty",
            "GUARDEDOPS_LLM_MAX_TOKENS": "",
        }
    )

    assert config.llm_timeout_seconds == DEFAULT_LLM_TIMEOUT_SECONDS
    assert config.llm_max_tokens == DEFAULT_LLM_MAX_TOKENS


def test_llm_temperature_rejects_nan() -> None:
    config = load_config({"GUARDEDOPS_LLM_TEMPERATURE": "nan"})

    assert config.llm_temperature == DEFAULT_LLM_TEMPERATURE


def test_llm_write_intents_are_opt_in() -> None:
    assert load_config({}).llm_allow_write_intents is False
    assert load_config({"GUARDEDOPS_LLM_ALLOW_WRITE_INTENTS": "maybe"}).llm_allow_write_intents is False
    assert load_config({"GUARDEDOPS_LLM_ALLOW_WRITE_INTENTS": "true"}).llm_allow_write_intents is True


def test_get_dashscope_api_key_returns_none_when_absent_or_blank() -> None:
    assert get_dashscope_api_key({}) is None
    assert get_dashscope_api_key({"DASHSCOPE_API_KEY": ""}) is None
    assert get_dashscope_api_key({"DASHSCOPE_API_KEY": "   "}) is None


def test_get_dashscope_api_key_strips_and_reads_process_environment(monkeypatch: Any) -> None:
    assert get_dashscope_api_key({"DASHSCOPE_API_KEY": "  fake-key-value  "}) == "fake-key-value"
    assert get_dashscope_api_key() is None

    monkeypatch.setenv("DASHSCOPE_API_KEY", " env-fake-key ")

    assert get_dashscope_api_key() == "env-fake-key"
    assert load_config().dashscope_api_key_present is True
