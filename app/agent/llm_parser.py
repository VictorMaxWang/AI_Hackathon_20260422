from __future__ import annotations

import json
import re
from typing import Any, Final, Literal, TypedDict

from pydantic import ValidationError

from app.config import AppConfig, load_config
from app.llm import LLMProvider, LLMRequest, QwenProvider
from app.llm.prompts import build_intent_candidate_messages
from app.models import IntentTarget, ParsedIntent
from app.policy import evaluate as evaluate_policy


LLM_PARSER_ENABLED: Final[bool] = False

ALLOWED_LLM_PROVIDER = "aliyun_bailian"
SUPPORTED_LLM_INTENTS = {
    "disk_usage": "query_disk_usage",
    "memory_usage": "query_memory_usage",
    "file_search": "search_files",
    "process_query": "query_process",
    "port_query": "query_port",
    "create_user": "create_user",
    "delete_user": "delete_user",
}
READONLY_INTENTS = {
    "query_disk_usage",
    "query_memory_usage",
    "search_files",
    "query_process",
    "query_port",
}
WRITE_INTENTS = {"create_user", "delete_user"}
INTENT_TOOL_WHITELIST = {
    "query_disk_usage": "disk_usage_tool",
    "query_memory_usage": "memory_usage_tool",
    "search_files": "file_search_tool",
    "query_process": "process_query_tool",
    "query_port": "port_query_tool",
}
FORBIDDEN_KEYS = {
    "allow",
    "argv",
    "bash",
    "cmd",
    "command",
    "commands",
    "confirmation_bypass",
    "decision",
    "deny",
    "execute",
    "execution_plan",
    "final_decision",
    "override_policy",
    "policy_override",
    "raw_command",
    "raw_shell",
    "script",
    "shell",
    "skip_confirmation",
    "tool",
    "tool_name",
}
COMMAND_TEXT_RE = re.compile(
    r"(?i)(?:"
    r"rm\s+-|chmod\s+[0-7]|chown\s+\S|useradd\s+\S|userdel\s+\S|"
    r"bash\s+-c|sh\s+-c|powershell\s+-command|cmd\s+/c|"
    r"run_shell_tool|execute_command_tool|bash_tool|raw shell|"
    r"&&|\|\||`|\$\("
    r")"
)


class LLMParserNotEnabled(RuntimeError):
    """Raised by future integrations if the LLM parser is called while disabled."""


class LLMParserResult(TypedDict):
    status: Literal["disabled", "fallback", "ok"]
    candidates: list[dict[str, Any]]
    reason: str


def parse_with_llm(
    raw_user_input: str,
    context: dict[str, Any] | None = None,
    *,
    provider: LLMProvider | None = None,
    config: AppConfig | None = None,
) -> LLMParserResult:
    """Parse a guarded intent candidate with an optional LLM provider.

    The default path remains disabled. When enabled, all provider output is
    validated and failures return an empty candidate list for rule fallback.
    """

    resolved_config = config or load_config()
    if not resolved_config.llm_enable:
        return {
            "status": "disabled",
            "candidates": [],
            "reason": "LLM parser is disabled in this build.",
        }

    if resolved_config.llm_provider != ALLOWED_LLM_PROVIDER:
        return _fallback(f"unsupported LLM provider: {resolved_config.llm_provider}")

    if not resolved_config.dashscope_api_key_present:
        return _fallback("DASHSCOPE_API_KEY is not set")

    resolved_provider = provider or QwenProvider.from_config(resolved_config)
    request = LLMRequest(
        messages=build_intent_candidate_messages(raw_user_input, context=context),
        model=resolved_config.llm_model,
        timeout_seconds=resolved_config.llm_timeout_seconds,
        max_tokens=resolved_config.llm_max_tokens,
        temperature=resolved_config.llm_temperature,
        metadata={"purpose": "intent_candidate"},
    )
    response = resolved_provider.complete(request)
    if not response.success:
        return _fallback(f"provider_error:{response.error_code or 'unknown'}")

    try:
        payload = _load_json_object(response.content or "")
        candidate = _validated_candidate(payload, raw_user_input, resolved_config)
    except ValueError as exc:
        return _fallback(str(exc))

    if candidate is None:
        return _fallback("LLM returned unsupported intent")

    return {
        "status": "ok",
        "candidates": [candidate.model_dump(mode="json")],
        "reason": "llm_candidate_validated",
    }


def _fallback(reason: str) -> LLMParserResult:
    return {
        "status": "fallback",
        "candidates": [],
        "reason": reason,
    }


def _load_json_object(content: str) -> dict[str, Any]:
    cleaned = _strip_json_fence(content)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("LLM output is not valid JSON") from exc

    if isinstance(payload, dict) and isinstance(payload.get("candidates"), list):
        candidates = payload["candidates"]
        payload = candidates[0] if candidates else {}
    elif isinstance(payload, list):
        payload = payload[0] if payload else {}

    if not isinstance(payload, dict):
        raise ValueError("LLM output must be a JSON object")
    return payload


def _strip_json_fence(content: str) -> str:
    stripped = str(content or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def _validated_candidate(
    payload: dict[str, Any],
    raw_user_input: str,
    config: AppConfig,
) -> ParsedIntent | None:
    _reject_forbidden_content(payload)

    llm_intent = str(payload.get("intent") or "").strip().lower()
    if llm_intent == "unsupported":
        return None
    if llm_intent == "high_risk_request":
        canonical_intent = "unknown"
        requires_write = True
    else:
        canonical_intent = SUPPORTED_LLM_INTENTS.get(llm_intent)
        if canonical_intent is None:
            raise ValueError("LLM output contains unsupported intent")
        candidate_requires_write = bool(payload.get("requires_write", False))
        if canonical_intent in READONLY_INTENTS and candidate_requires_write:
            raise ValueError("LLM marked a read-only intent as write")
        requires_write = canonical_intent in WRITE_INTENTS

    target_payload = payload.get("target") or {}
    if not isinstance(target_payload, dict):
        raise ValueError("LLM target must be an object")
    normalized_target = _normalized_target(target_payload)

    constraints_payload = payload.get("constraints") or {}
    if not isinstance(constraints_payload, dict):
        raise ValueError("LLM constraints must be an object")
    constraints = dict(constraints_payload)
    constraints["source"] = "llm_fallback"
    constraints["llm_provider"] = config.llm_provider
    constraints["llm_model"] = config.llm_model
    constraints["llm_intent"] = llm_intent

    risk_hint = _optional_text(payload.get("risk_hint"), max_length=120)
    if risk_hint:
        constraints["llm_risk_hint"] = risk_hint
    explanation = _optional_text(payload.get("explanation"), max_length=240)
    if explanation:
        constraints["llm_explanation"] = explanation
    if llm_intent == "high_risk_request":
        constraints.setdefault("unsupported_reason", "LLM marked request as high-risk candidate")

    try:
        parsed = ParsedIntent(
            intent=canonical_intent,
            target=IntentTarget.model_validate(normalized_target),
            constraints=constraints,
            context_refs=_string_list(payload.get("context_refs")),
            requires_write=requires_write,
            raw_user_input=raw_user_input,
            confidence=_confidence(payload.get("confidence")),
        )
    except ValidationError as exc:
        raise ValueError("LLM output failed ParsedIntent schema validation") from exc

    _validate_policy_and_tool_boundary(parsed)
    return parsed


def _normalized_target(target: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "username": target.get("username"),
        "path": target.get("path"),
        "port": target.get("port"),
        "pid": target.get("pid"),
        "keyword": target.get("keyword"),
        "base_paths": target.get("base_paths") or [],
    }
    if not isinstance(normalized["base_paths"], list):
        raise ValueError("LLM target.base_paths must be a list")
    return normalized


def _validate_policy_and_tool_boundary(parsed: ParsedIntent) -> None:
    _ = evaluate_policy(parsed)
    if parsed.intent in READONLY_INTENTS and parsed.intent not in INTENT_TOOL_WHITELIST:
        raise ValueError("LLM candidate maps to no whitelisted read-only tool")
    if parsed.intent in WRITE_INTENTS:
        return
    if parsed.intent == "unknown":
        return
    if parsed.intent not in READONLY_INTENTS:
        raise ValueError("LLM candidate intent is outside the whitelist")


def _reject_forbidden_content(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).strip().lower()
            if normalized_key in FORBIDDEN_KEYS:
                raise ValueError(f"LLM output contains forbidden field at {path}.{key}")
            _reject_forbidden_content(item, f"{path}.{key}")
        return

    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_forbidden_content(item, f"{path}[{index}]")
        return

    if isinstance(value, str) and COMMAND_TEXT_RE.search(value):
        raise ValueError(f"LLM output contains command-like text at {path}")


def _optional_text(value: Any, *, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:max_length] if text else None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        value = [value]
    return [str(item).strip() for item in value if str(item).strip()]


def _confidence(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if parsed < 0.0:
        return 0.0
    if parsed > 1.0:
        return 1.0
    return parsed
