from __future__ import annotations

from typing import Any, Callable

from app.config import AppConfig, get_dashscope_api_key, load_config
from app.llm.base import LLMProviderError, LLMRequest, LLMResponse


OpenAIClientFactory = Callable[..., Any]


class QwenProvider:
    provider_name = "aliyun_bailian"

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str,
        model: str,
        timeout_seconds: int,
        client: Any | None = None,
        client_factory: OpenAIClientFactory | None = None,
    ) -> None:
        self._api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._client = client
        self._client_factory = client_factory

    @classmethod
    def from_config(cls, config: AppConfig | None = None) -> "QwenProvider":
        resolved = config or load_config()
        return cls(
            api_key=get_dashscope_api_key(),
            base_url=resolved.llm_base_url,
            model=resolved.llm_model,
            timeout_seconds=resolved.llm_timeout_seconds,
        )

    def complete(self, request: LLMRequest) -> LLMResponse:
        if not self._api_key:
            return self._error("missing_api_key", "DASHSCOPE_API_KEY is not set")

        try:
            client = self._client or self._build_client()
            response = client.chat.completions.create(
                model=request.model or self.model,
                messages=request.messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                timeout=request.timeout_seconds,
            )
            content = _extract_content(response)
            if not content:
                return self._error("empty_response", "LLM provider returned no content")
            return LLMResponse(
                success=True,
                content=content,
                provider=self.provider_name,
                model=request.model or self.model,
            )
        except LLMProviderError as exc:
            return exc.to_response(provider=self.provider_name, model=request.model or self.model)
        except Exception as exc:
            return self._error(type(exc).__name__, "LLM provider call failed")

    def _build_client(self) -> Any:
        if self._client_factory is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise LLMProviderError(
                    "openai_dependency_missing",
                    "OpenAI Python SDK is not installed",
                ) from exc
            self._client_factory = OpenAI

        return self._client_factory(
            api_key=self._api_key,
            base_url=self.base_url,
            timeout=self.timeout_seconds,
        )

    def _error(self, code: str, message: str) -> LLMResponse:
        return LLMResponse(
            success=False,
            provider=self.provider_name,
            model=self.model,
            error_code=code,
            error_message=message,
        )


def _extract_content(response: Any) -> str | None:
    choices = getattr(response, "choices", None)
    if not choices:
        return None
    first = choices[0]
    message = getattr(first, "message", None)
    content = getattr(message, "content", None)
    if isinstance(content, str):
        cleaned = content.strip()
        return cleaned or None
    return None
