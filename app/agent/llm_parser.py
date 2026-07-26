from __future__ import annotations

import json
import re
from typing import Any, Literal, TypedDict

from pydantic import ValidationError

from app.agent.parser import _looks_like_privilege_escalation as rule_privilege_escalation
from app.config import AppConfig, load_config
from app.llm import LLMProvider, LLMRequest, QwenProvider
from app.llm.prompts import build_intent_candidate_messages
from app.models import IntentTarget, ParsedIntent, RiskLevel
from app.policy import evaluate as evaluate_policy
from app.policy.rules import normalize_path


ALLOWED_LLM_PROVIDER = "aliyun_bailian"
READONLY_LLM_INTENTS = {
    "disk_usage": "query_disk_usage",
    "memory_usage": "query_memory_usage",
    "file_search": "search_files",
    "process_query": "query_process",
    "port_query": "query_port",
}
WRITE_LLM_INTENTS = {
    "create_user": "create_user",
    "delete_user": "delete_user",
}
SUPPORTED_LLM_INTENTS = {**READONLY_LLM_INTENTS, **WRITE_LLM_INTENTS}
READONLY_INTENTS = set(READONLY_LLM_INTENTS.values())
WRITE_INTENTS = set(WRITE_LLM_INTENTS.values())
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
PRIVILEGE_SIGNAL_RE = re.compile(
    r"(?i)(?:"
    r"sudo|wheel|\broot\b|\badmin(?:istrator)?s?\b|superuser|super\s+user|"
    r"privileg|elevat|escalat|"
    r"full\s+(?:system|server|machine|root)?\s*access|all\s+permissions|unrestricted|"
    r"提权|提升权限|管理员|超级用户|特权|最高权限|完全访问|全部权限|所有权限"
    r")"
)

MAX_PATH_LENGTH = 512
MAX_BASE_PATHS = 8
MAX_USERNAME_LENGTH = 64
MAX_KEYWORD_LENGTH = 64
MAX_CONSTRAINT_KEYS = 24
MAX_CONSTRAINT_TEXT_LENGTH = 240
MAX_RISK_HINT_LENGTH = 120
MAX_EXPLANATION_LENGTH = 240
MAX_CONTEXT_REFS = 8
MAX_CONTEXT_REF_LENGTH = 120

PRIVILEGE_REFUSAL_REASON = "请求疑似涉及提权，已按未知写操作最高风险处理"
WRITE_DISABLED_REASON = "LLM 写操作候选默认禁用，已按未知写操作处理"
HIGH_RISK_REASON = "LLM 将该请求标记为高风险候选"
RISK_HINT_REASON = "LLM 输出带有风险提示，已按未知写操作处理"


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
    A candidate is never allowed to widen the policy outcome: write intents
    require the ``llm_allow_write_intents`` opt-in, any privilege signal in the
    original request forces an unknown write, and every path must already be
    absolute and normalized.
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


def candidate_target_is_safe(candidate: Any) -> bool:
    """Re-check the target invariant of an already-built intent candidate.

    Callers that accept candidates from an injected parser function can use
    this to reject smuggled paths without importing the validator internals.
    """

    if not isinstance(candidate, dict):
        return False
    target = candidate.get("target")
    if target is None:
        return True
    if not isinstance(target, dict):
        return False
    try:
        _normalized_target(target)
    except ValueError:
        return False
    return True


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

    canonical_intent, requires_write, unsupported_reason = _resolved_intent(
        llm_intent,
        payload,
        config,
    )

    target_payload = payload.get("target")
    if target_payload is None:
        target_payload = {}
    if not isinstance(target_payload, dict):
        raise ValueError("LLM target must be an object")
    normalized_target = _normalized_target(target_payload)

    constraints_payload = payload.get("constraints")
    if constraints_payload is None:
        constraints_payload = {}
    if not isinstance(constraints_payload, dict):
        raise ValueError("LLM constraints must be an object")
    if len(constraints_payload) > MAX_CONSTRAINT_KEYS:
        raise ValueError("LLM constraints contains too many keys")
    constraints = _capped_constraints(constraints_payload)

    risk_hint = _optional_text(payload.get("risk_hint"), max_length=MAX_RISK_HINT_LENGTH)
    if risk_hint:
        constraints["llm_risk_hint"] = risk_hint
        if canonical_intent != "unknown" or not requires_write:
            canonical_intent = "unknown"
            requires_write = True
            unsupported_reason = RISK_HINT_REASON

    privileged_request = _looks_like_privilege_request(raw_user_input)
    if privileged_request and (requires_write or canonical_intent not in READONLY_INTENTS):
        canonical_intent = "unknown"
        requires_write = True
        unsupported_reason = PRIVILEGE_REFUSAL_REASON

    constraints["source"] = "llm_fallback"
    constraints["llm_provider"] = config.llm_provider
    constraints["llm_model"] = config.llm_model
    constraints["llm_intent"] = llm_intent

    explanation = _optional_text(payload.get("explanation"), max_length=MAX_EXPLANATION_LENGTH)
    if explanation:
        constraints["llm_explanation"] = explanation
    if unsupported_reason:
        constraints["unsupported_reason"] = unsupported_reason
    if privileged_request and canonical_intent == "unknown":
        constraints["danger_category"] = "privilege_escalation"
        constraints["groups"] = ["sudo"]
        constraints["privilege"] = "sudo"

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


def _resolved_intent(
    llm_intent: str,
    payload: dict[str, Any],
    config: AppConfig,
) -> tuple[str, bool, str | None]:
    if llm_intent == "high_risk_request":
        return "unknown", True, HIGH_RISK_REASON

    canonical_intent = SUPPORTED_LLM_INTENTS.get(llm_intent)
    if canonical_intent is None:
        raise ValueError("LLM output contains unsupported intent")

    if canonical_intent in WRITE_INTENTS:
        if not config.llm_allow_write_intents:
            return "unknown", True, WRITE_DISABLED_REASON
        return canonical_intent, True, None

    if bool(payload.get("requires_write", False)):
        raise ValueError("LLM marked a read-only intent as write")
    return canonical_intent, False, None


def _looks_like_privilege_request(raw_user_input: str) -> bool:
    text = str(raw_user_input or "")
    if not text:
        return False
    return bool(PRIVILEGE_SIGNAL_RE.search(text) or rule_privilege_escalation(text))


def _normalized_target(target: dict[str, Any]) -> dict[str, Any]:
    base_paths_payload = target.get("base_paths")
    if base_paths_payload is None:
        base_paths_payload = []
    if not isinstance(base_paths_payload, list):
        raise ValueError("LLM target.base_paths must be a list")
    if len(base_paths_payload) > MAX_BASE_PATHS:
        raise ValueError("LLM target.base_paths contains too many entries")

    return {
        "username": _guarded_text(
            target.get("username"),
            field="target.username",
            max_length=MAX_USERNAME_LENGTH,
        ),
        "path": _guarded_path(target.get("path"), field="target.path", required=False),
        "port": target.get("port"),
        "pid": target.get("pid"),
        "keyword": _guarded_text(
            target.get("keyword"),
            field="target.keyword",
            max_length=MAX_KEYWORD_LENGTH,
        ),
        "base_paths": [
            _guarded_path(item, field=f"target.base_paths[{index}]", required=True)
            for index, item in enumerate(base_paths_payload)
        ],
    }


def _guarded_path(value: Any, *, field: str, required: bool) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"LLM {field} must not be null")
        return None
    if not isinstance(value, str):
        raise ValueError(f"LLM {field} must be a string")
    if not value.strip():
        raise ValueError(f"LLM {field} must not be empty")
    if len(value) > MAX_PATH_LENGTH:
        raise ValueError(f"LLM {field} is longer than {MAX_PATH_LENGTH} characters")
    if value != value.strip():
        raise ValueError(f"LLM {field} has surrounding whitespace")
    if _has_control_characters(value):
        raise ValueError(f"LLM {field} contains control characters")
    if value.startswith("-"):
        raise ValueError(f"LLM {field} must not start with an option dash")
    if not value.startswith("/"):
        raise ValueError(f"LLM {field} must be an absolute path")
    if "//" in value:
        raise ValueError(f"LLM {field} contains an empty path segment")
    if normalize_path(value) != value:
        raise ValueError(f"LLM {field} is not in normalized form")
    return value


def _guarded_text(value: Any, *, field: str, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"LLM {field} must be a string")
    text = value.strip()
    if not text:
        return None
    if len(text) > max_length:
        raise ValueError(f"LLM {field} is longer than {max_length} characters")
    if _has_control_characters(text):
        raise ValueError(f"LLM {field} contains control characters")
    return text


def _has_control_characters(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _capped_constraints(constraints: dict[str, Any]) -> dict[str, Any]:
    capped: dict[str, Any] = {}
    for key, value in constraints.items():
        capped[str(key)[:MAX_CONSTRAINT_TEXT_LENGTH]] = _capped_constraint_value(value)
    return capped


def _capped_constraint_value(value: Any) -> Any:
    if isinstance(value, str):
        return value[:MAX_CONSTRAINT_TEXT_LENGTH]
    if isinstance(value, list):
        return [_capped_constraint_value(item) for item in value[:MAX_BASE_PATHS]]
    if isinstance(value, dict):
        return {
            str(key)[:MAX_CONSTRAINT_TEXT_LENGTH]: _capped_constraint_value(item)
            for key, item in list(value.items())[:MAX_CONSTRAINT_KEYS]
        }
    return value


def _validate_policy_and_tool_boundary(parsed: ParsedIntent) -> None:
    if parsed.intent in READONLY_INTENTS and parsed.intent not in INTENT_TOOL_WHITELIST:
        raise ValueError("LLM candidate maps to no whitelisted read-only tool")
    if (
        parsed.intent not in READONLY_INTENTS
        and parsed.intent not in WRITE_INTENTS
        and parsed.intent != "unknown"
    ):
        raise ValueError("LLM candidate intent is outside the whitelist")

    decision = evaluate_policy(parsed)
    if not decision.allow:
        return
    if parsed.requires_write and not decision.requires_confirmation:
        raise ValueError("LLM candidate is an allowed write without confirmation")
    if decision.risk_level != RiskLevel.S0 and not decision.requires_confirmation:
        raise ValueError("LLM candidate is allowed above S0 without confirmation")
    if decision.risk_level == RiskLevel.S0 and parsed.intent not in INTENT_TOOL_WHITELIST:
        raise ValueError("LLM candidate is S0-allowed outside the tool whitelist")


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
    items = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in items[:MAX_CONTEXT_REFS]:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            result.append(text[:MAX_CONTEXT_REF_LENGTH])
    return result


def _confidence(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if parsed != parsed:
        return 0.0
    if parsed < 0.0:
        return 0.0
    if parsed > 1.0:
        return 1.0
    return parsed
