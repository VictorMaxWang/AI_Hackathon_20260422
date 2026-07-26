from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.policy.rules import SYSTEM_USERNAMES


USERNAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]{2,31}$")


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    reasons: tuple[str, ...] = ()


def validate_username(username: Any) -> bool:
    return validate_username_with_reasons(username).valid


def validate_username_with_reasons(username: Any) -> ValidationResult:
    reasons: list[str] = []

    if not isinstance(username, str):
        return ValidationResult(False, ("username must be a string",))

    if username == "":
        return ValidationResult(False, ("username is required",))

    if username != username.strip():
        reasons.append("username must not contain leading or trailing whitespace")

    stripped = username.strip()
    lowered = stripped.lower()
    if not stripped:
        reasons.append("username is required")

    if lowered in SYSTEM_USERNAMES:
        reasons.append(f"username {lowered} is reserved for system or privileged accounts")

    if not USERNAME_PATTERN.fullmatch(stripped):
        reasons.append("username must match ^[a-z_][a-z0-9_-]{2,31}$")

    return ValidationResult(valid=not reasons, reasons=tuple(reasons))
