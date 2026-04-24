from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


DEFAULT_LLM_PROVIDER = "aliyun_bailian"
DEFAULT_LLM_MODEL = "qwen3.6-plus"
DEFAULT_LLM_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_LLM_TIMEOUT_SECONDS = 30
DEFAULT_LLM_MAX_TOKENS = 1024
DEFAULT_LLM_TEMPERATURE = 0.0


@dataclass(frozen=True)
class AppConfig:
    llm_enable: bool = False
    llm_provider: str = DEFAULT_LLM_PROVIDER
    llm_model: str = DEFAULT_LLM_MODEL
    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_timeout_seconds: int = DEFAULT_LLM_TIMEOUT_SECONDS
    llm_max_tokens: int = DEFAULT_LLM_MAX_TOKENS
    llm_temperature: float = DEFAULT_LLM_TEMPERATURE
    dashscope_api_key_present: bool = False


def load_config(env: Mapping[str, str] | None = None) -> AppConfig:
    source = env if env is not None else os.environ
    return AppConfig(
        llm_enable=_parse_bool(source.get("GUARDEDOPS_LLM_ENABLE"), default=False),
        llm_provider=_read_text(source, "GUARDEDOPS_LLM_PROVIDER", DEFAULT_LLM_PROVIDER),
        llm_model=_read_text(source, "GUARDEDOPS_LLM_MODEL", DEFAULT_LLM_MODEL),
        llm_base_url=_read_text(source, "GUARDEDOPS_LLM_BASE_URL", DEFAULT_LLM_BASE_URL),
        llm_timeout_seconds=_parse_int(
            source.get("GUARDEDOPS_LLM_TIMEOUT_SECONDS"),
            default=DEFAULT_LLM_TIMEOUT_SECONDS,
            minimum=1,
        ),
        llm_max_tokens=_parse_int(
            source.get("GUARDEDOPS_LLM_MAX_TOKENS"),
            default=DEFAULT_LLM_MAX_TOKENS,
            minimum=1,
        ),
        llm_temperature=_parse_float(
            source.get("GUARDEDOPS_LLM_TEMPERATURE"),
            default=DEFAULT_LLM_TEMPERATURE,
            minimum=0.0,
        ),
        dashscope_api_key_present=bool(str(source.get("DASHSCOPE_API_KEY") or "").strip()),
    )


def get_dashscope_api_key(env: Mapping[str, str] | None = None) -> str | None:
    source = env if env is not None else os.environ
    value = str(source.get("DASHSCOPE_API_KEY") or "").strip()
    return value or None


def _read_text(source: Mapping[str, str], name: str, default: str) -> str:
    value = str(source.get(name) or "").strip()
    return value or default


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _parse_int(value: str | None, *, default: int, minimum: int) -> int:
    try:
        parsed = int(str(value).strip()) if value is not None else default
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= minimum else default


def _parse_float(value: str | None, *, default: float, minimum: float) -> float:
    try:
        parsed = float(str(value).strip()) if value is not None else default
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= minimum else default
