from __future__ import annotations

from typing import Any, Final, Literal, TypedDict


LLM_PARSER_ENABLED: Final[bool] = False


class LLMParserNotEnabled(RuntimeError):
    """Raised by future integrations if the LLM parser is called while disabled."""


class LLMParserResult(TypedDict):
    status: Literal["disabled"]
    candidates: list[dict[str, Any]]
    reason: str


def parse_with_llm(
    raw_user_input: str,
    context: dict[str, Any] | None = None,
) -> LLMParserResult:
    """Return a predictable disabled result for the reserved LLM parser hook.

    This stub intentionally does not call a model provider, read API keys, or
    participate in the active rule-based parser flow.
    """

    _ = (raw_user_input, context)
    return {
        "status": "disabled",
        "candidates": [],
        "reason": "LLM parser is disabled in this build.",
    }
