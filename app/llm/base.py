from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class LLMRequest:
    messages: list[dict[str, str]]
    model: str
    timeout_seconds: int
    max_tokens: int
    temperature: float
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResponse:
    success: bool
    content: str | None = None
    provider: str = ""
    model: str = ""
    error_code: str | None = None
    error_message: str | None = None


class LLMProviderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_response(self, *, provider: str, model: str) -> LLMResponse:
        return LLMResponse(
            success=False,
            provider=provider,
            model=model,
            error_code=self.code,
            error_message=self.message,
        )


class LLMProvider(Protocol):
    provider_name: str

    def complete(self, request: LLMRequest) -> LLMResponse:
        ...
