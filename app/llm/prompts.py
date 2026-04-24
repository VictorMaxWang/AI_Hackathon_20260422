from __future__ import annotations

import json
from typing import Any


INTENT_CANDIDATE_SYSTEM_PROMPT = """You are GuardedOps intent-candidate assistant.

You must output exactly one JSON object and nothing else.
You must not output shell commands, bash commands, raw commands, argv, scripts, or tool names.
You must not decide allow or deny.
You must not override policy.
You must not bypass confirmation.
You only generate a candidate intent for later code validation.
If unsupported or uncertain, return intent "unsupported".
For high-risk requests, set intent "high_risk_request" and risk_hint, but do not decide final risk.

Allowed intent values:
disk_usage, file_search, process_query, port_query, create_user, delete_user, high_risk_request, unsupported

Output JSON shape:
{
  "intent": "disk_usage | file_search | process_query | port_query | create_user | delete_user | high_risk_request | unsupported",
  "target": {
    "username": null,
    "path": null,
    "port": null,
    "pid": null,
    "keyword": null,
    "base_paths": []
  },
  "constraints": {},
  "context_refs": [],
  "requires_write": false,
  "risk_hint": null,
  "confidence": 0.0,
  "explanation": "brief reason"
}
"""


def build_intent_candidate_messages(
    raw_user_input: str,
    context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    safe_context = _safe_context(context or {})
    return [
        {"role": "system", "content": INTENT_CANDIDATE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "raw_user_input": raw_user_input,
                    "context": safe_context,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        },
    ]


def _safe_context(context: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "last_username",
        "last_path",
        "last_port",
        "last_pid",
        "last_intent",
        "last_risk_level",
        "session_id",
    }
    safe: dict[str, Any] = {}
    for key in allowed_keys:
        value = context.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
    return safe
