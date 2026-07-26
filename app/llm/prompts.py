from __future__ import annotations

import json
from typing import Any


MAX_RAW_USER_INPUT_LENGTH = 2000
MAX_CONTEXT_TEXT_LENGTH = 200

INTENT_CANDIDATE_SYSTEM_PROMPT = """You are GuardedOps intent-candidate assistant.

You must output exactly one JSON object and nothing else.
You must not output shell commands, bash commands, raw commands, argv, scripts, or tool names.
You must not decide allow or deny.
You must not override policy.
You must not bypass confirmation.
You only generate a candidate intent for later code validation.
If unsupported or uncertain, return intent "unsupported".
For high-risk requests, set intent "high_risk_request" and risk_hint, but do not decide final risk.

risk_hint must stay null for every normal request. Any non-empty risk_hint makes the
validator downgrade the candidate to an unknown write operation, which the policy
engine then refuses.

create_user and delete_user are recognized but disabled by default. Unless the
operator explicitly enabled write candidates, the validator downgrades them to an
unknown write operation, which the policy engine then refuses.

Every path in target.path and target.base_paths must be an absolute, already
normalized POSIX path: it must start with "/", must not start with "-", and must
contain no "..", no "//", and no trailing slash. Any other value is rejected.

Allowed intent values:
disk_usage, memory_usage, file_search, process_query, port_query, create_user, delete_user, high_risk_request, unsupported

Output JSON shape:
{
  "intent": "disk_usage | memory_usage | file_search | process_query | port_query | create_user | delete_user | high_risk_request | unsupported",
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
                    "raw_user_input": str(raw_user_input or "")[:MAX_RAW_USER_INPUT_LENGTH],
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
        if isinstance(value, str):
            safe[key] = value[:MAX_CONTEXT_TEXT_LENGTH]
        elif isinstance(value, (int, float, bool)) or value is None:
            safe[key] = value
    return safe
