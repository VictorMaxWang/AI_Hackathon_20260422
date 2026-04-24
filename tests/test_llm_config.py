from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import (
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_PROVIDER,
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
