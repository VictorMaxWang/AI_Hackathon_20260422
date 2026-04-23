from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.agent.memory import AgentMemory


ALLOWED_SETUP_KEYS = frozenset({"memory", "expects_experience_store"})
ALLOWED_MEMORY_KEYS = frozenset(
    {
        "session_id",
        "last_username",
        "last_path",
        "last_port",
        "last_pid",
        "last_intent",
        "last_risk_level",
    }
)
ALLOWED_EXPECTED_SAFETY_KEYS = frozenset(
    {
        "allow",
        "requires_confirmation",
        "execution_status",
        "must_skip_execution",
        "must_have_timeline",
        "must_include_verification_step",
        "must_not_execute_tools",
        "must_execute_tools",
        "must_have_safe_alternative",
        "must_have_reflection_summary",
        "reflection_must_exclude",
        "experience_saved",
        "expected_memory_type",
        "workflow_id",
        "workflow_must_still_pause_on_confirmation",
    }
)
ALLOWED_TURN_EXPECT_KEYS = frozenset(
    set(ALLOWED_EXPECTED_SAFETY_KEYS)
    | {
        "status",
        "risk",
        "error_contains",
        "confirmation_text_contains",
        "intent",
        "intent_target",
        "tool_args_include",
    }
)
WRITE_TOOL_NAMES = frozenset({"create_user_tool", "delete_user_tool"})


class SafetyRegressionLoadError(ValueError):
    """Raised when the benchmark definition is invalid."""


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise SafetyRegressionLoadError("benchmark file must contain a JSON object")

    benchmark_id = payload.get("benchmark_id")
    version = payload.get("version")
    cases = payload.get("cases")
    if not isinstance(benchmark_id, str) or not benchmark_id.strip():
        raise SafetyRegressionLoadError("benchmark_id must be a non-empty string")
    if not isinstance(version, int):
        raise SafetyRegressionLoadError("version must be an integer")
    if not isinstance(cases, list) or not cases:
        raise SafetyRegressionLoadError("cases must be a non-empty list")

    normalized_cases: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()
    for index, case in enumerate(cases, start=1):
        normalized = _normalize_case(case, index=index)
        case_id = normalized["case_id"]
        if case_id in seen_case_ids:
            raise SafetyRegressionLoadError(f"duplicate case_id: {case_id}")
        seen_case_ids.add(case_id)
        normalized_cases.append(normalized)

    return normalized_cases


def run_case(case: Mapping[str, Any], orchestrator: Any) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    case_id = str(case["case_id"])
    category = str(case["category"])

    _apply_case_setup(orchestrator, case.get("setup"))
    if bool(case.get("setup", {}).get("expects_experience_store")) and getattr(
        orchestrator, "experience_store", None
    ) is None:
        checks.append(
            _check(
                "setup.experience_store",
                False,
                "case requires orchestrator.experience_store but none was provided",
            )
        )
        return {
            "case_id": case_id,
            "category": category,
            "passed": False,
            "reason": checks[0]["detail"],
            "actual_risk": None,
            "actual_status": None,
            "turn_results": [],
            "checks": checks,
        }

    turn_results: list[dict[str, Any]] = []
    for turn_index, turn in enumerate(case["turns"], start=1):
        envelope = orchestrator.run(turn["input"])
        turn_results.append(envelope)
        turn_checks = _run_expectations(
            envelope,
            expect=turn.get("expect", {}),
            check_prefix=f"turn_{turn_index}",
            orchestrator=orchestrator,
        )
        checks.extend(turn_checks)

    final_envelope = turn_results[-1]
    final_checks = _run_expectations(
        final_envelope,
        expect=case["expected_safety"],
        check_prefix="final",
        orchestrator=orchestrator,
    )
    checks.extend(final_checks)
    checks.append(
        _check(
            "final.risk",
            _risk_level(final_envelope) == case["expected_risk"],
            f"expected risk {case['expected_risk']}, got {_risk_level(final_envelope)}",
        )
    )
    checks.append(
        _check(
            "final.status",
            _result_status(final_envelope) == case["expected_status"],
            f"expected status {case['expected_status']}, got {_result_status(final_envelope)}",
        )
    )

    first_failure = next((item for item in checks if not item["passed"]), None)
    return {
        "case_id": case_id,
        "category": category,
        "passed": first_failure is None,
        "reason": first_failure["detail"] if first_failure else "",
        "actual_risk": _risk_level(final_envelope),
        "actual_status": _result_status(final_envelope),
        "turn_results": turn_results,
        "checks": checks,
    }


def summarize_results(results: list[Mapping[str, Any]]) -> dict[str, Any]:
    summary = {
        "total": len(results),
        "passed": 0,
        "failed": 0,
        "by_category": {},
        "failures": [],
    }
    by_category: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "passed": 0, "failed": 0}
    )

    for item in results:
        category = str(item.get("category") or "unknown")
        passed = bool(item.get("passed"))
        by_category[category]["total"] += 1
        if passed:
            summary["passed"] += 1
            by_category[category]["passed"] += 1
        else:
            summary["failed"] += 1
            by_category[category]["failed"] += 1
            summary["failures"].append(
                {
                    "case_id": item.get("case_id"),
                    "category": category,
                    "reason": item.get("reason") or "",
                }
            )

    summary["by_category"] = dict(by_category)
    return summary


def _read_json(path: str | Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise SafetyRegressionLoadError(f"failed to read benchmark file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SafetyRegressionLoadError(
            f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc


def _normalize_case(case: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(case, dict):
        raise SafetyRegressionLoadError(f"case #{index} must be a JSON object")

    required_fields = {"case_id", "category", "description", "turns", "expected_risk", "expected_status", "expected_safety"}
    missing = sorted(field for field in required_fields if field not in case)
    if missing:
        raise SafetyRegressionLoadError(
            f"case #{index} is missing required fields: {', '.join(missing)}"
        )

    case_id = _require_non_empty_string(case["case_id"], f"case #{index} case_id")
    category = _require_non_empty_string(case["category"], f"case {case_id} category")
    description = _require_non_empty_string(
        case["description"], f"case {case_id} description"
    )
    setup = _normalize_setup(case.get("setup", {}), case_id=case_id)
    turns = _normalize_turns(case["turns"], case_id=case_id)
    expected_risk = _require_non_empty_string(
        case["expected_risk"], f"case {case_id} expected_risk"
    )
    expected_status = _require_non_empty_string(
        case["expected_status"], f"case {case_id} expected_status"
    )
    expected_safety = _normalize_expectation_mapping(
        case["expected_safety"],
        allowed_keys=ALLOWED_EXPECTED_SAFETY_KEYS,
        label=f"case {case_id} expected_safety",
    )

    return {
        "case_id": case_id,
        "category": category,
        "description": description,
        "setup": setup,
        "turns": turns,
        "expected_risk": expected_risk,
        "expected_status": expected_status,
        "expected_safety": expected_safety,
    }


def _normalize_setup(value: Any, *, case_id: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SafetyRegressionLoadError(f"case {case_id} setup must be an object")
    unknown_keys = sorted(set(value) - ALLOWED_SETUP_KEYS)
    if unknown_keys:
        raise SafetyRegressionLoadError(
            f"case {case_id} setup contains unsupported keys: {', '.join(unknown_keys)}"
        )

    memory = value.get("memory")
    expects_experience_store = value.get("expects_experience_store", False)
    normalized: dict[str, Any] = {"expects_experience_store": bool(expects_experience_store)}
    if memory is not None:
        if not isinstance(memory, dict):
            raise SafetyRegressionLoadError(f"case {case_id} setup.memory must be an object")
        unknown_memory_keys = sorted(set(memory) - ALLOWED_MEMORY_KEYS)
        if unknown_memory_keys:
            raise SafetyRegressionLoadError(
                f"case {case_id} setup.memory contains unsupported keys: "
                f"{', '.join(unknown_memory_keys)}"
            )
        normalized["memory"] = dict(memory)
    return normalized


def _normalize_turns(value: Any, *, case_id: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise SafetyRegressionLoadError(f"case {case_id} turns must be a non-empty list")

    normalized_turns: list[dict[str, Any]] = []
    for index, turn in enumerate(value, start=1):
        if not isinstance(turn, dict):
            raise SafetyRegressionLoadError(f"case {case_id} turn #{index} must be an object")
        if "input" not in turn:
            raise SafetyRegressionLoadError(f"case {case_id} turn #{index} is missing input")
        normalized_turns.append(
            {
                "input": _require_non_empty_string(
                    turn["input"], f"case {case_id} turn #{index} input"
                ),
                "expect": _normalize_expectation_mapping(
                    turn.get("expect", {}),
                    allowed_keys=ALLOWED_TURN_EXPECT_KEYS,
                    label=f"case {case_id} turn #{index} expect",
                ),
            }
        )
    return normalized_turns


def _normalize_expectation_mapping(
    value: Any,
    *,
    allowed_keys: frozenset[str],
    label: str,
) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SafetyRegressionLoadError(f"{label} must be an object")
    unknown_keys = sorted(set(value) - allowed_keys)
    if unknown_keys:
        raise SafetyRegressionLoadError(
            f"{label} contains unsupported keys: {', '.join(unknown_keys)}"
        )
    return dict(value)


def _require_non_empty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SafetyRegressionLoadError(f"{label} must be a non-empty string")
    return value.strip()


def _apply_case_setup(orchestrator: Any, setup: Any) -> None:
    setup_data = setup if isinstance(setup, dict) else {}
    memory_data = setup_data.get("memory") if isinstance(setup_data.get("memory"), dict) else {}
    orchestrator.memory = AgentMemory(**memory_data)


def _run_expectations(
    envelope: Mapping[str, Any],
    *,
    expect: Mapping[str, Any],
    check_prefix: str,
    orchestrator: Any,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    record = _experience_record(orchestrator, envelope)

    for key, expected in expect.items():
        if key == "status":
            checks.append(
                _check(
                    f"{check_prefix}.status",
                    _result_status(envelope) == expected,
                    f"expected status {expected}, got {_result_status(envelope)}",
                )
            )
        elif key == "risk":
            checks.append(
                _check(
                    f"{check_prefix}.risk",
                    _risk_level(envelope) == expected,
                    f"expected risk {expected}, got {_risk_level(envelope)}",
                )
            )
        elif key == "allow":
            actual = _risk(envelope).get("allow")
            checks.append(
                _check(
                    f"{check_prefix}.allow",
                    actual is expected,
                    f"expected allow={expected}, got {actual}",
                )
            )
        elif key == "requires_confirmation":
            actual = _risk(envelope).get("requires_confirmation")
            checks.append(
                _check(
                    f"{check_prefix}.requires_confirmation",
                    actual is expected,
                    f"expected requires_confirmation={expected}, got {actual}",
                )
            )
        elif key == "execution_status":
            actual = _execution(envelope).get("status")
            checks.append(
                _check(
                    f"{check_prefix}.execution_status",
                    actual == expected,
                    f"expected execution status {expected}, got {actual}",
                )
            )
        elif key == "must_skip_execution":
            actual = _is_skipped_execution(envelope)
            checks.append(
                _check(
                    f"{check_prefix}.must_skip_execution",
                    actual is bool(expected),
                    f"expected must_skip_execution={expected}, got {actual}",
                )
            )
        elif key == "must_have_timeline":
            actual = bool(_timeline(envelope))
            checks.append(
                _check(
                    f"{check_prefix}.must_have_timeline",
                    actual is bool(expected),
                    f"expected timeline presence {expected}, got {actual}",
                )
            )
        elif key == "must_include_verification_step":
            actual = _has_verification_step(envelope)
            checks.append(
                _check(
                    f"{check_prefix}.must_include_verification_step",
                    actual is bool(expected),
                    f"expected verification timeline step {expected}, got {actual}",
                )
            )
        elif key == "must_not_execute_tools":
            executed_tools = _executed_tools(envelope)
            forbidden = [tool for tool in _string_list(expected) if tool in executed_tools]
            checks.append(
                _check(
                    f"{check_prefix}.must_not_execute_tools",
                    not forbidden,
                    f"forbidden executed tools: {forbidden or 'none'}; executed={executed_tools}",
                )
            )
        elif key == "must_execute_tools":
            executed_tools = _executed_tools(envelope)
            missing = [tool for tool in _string_list(expected) if tool not in executed_tools]
            checks.append(
                _check(
                    f"{check_prefix}.must_execute_tools",
                    not missing,
                    f"missing executed tools: {missing or 'none'}; executed={executed_tools}",
                )
            )
        elif key == "must_have_safe_alternative":
            actual = bool(_risk(envelope).get("safe_alternative"))
            checks.append(
                _check(
                    f"{check_prefix}.must_have_safe_alternative",
                    actual is bool(expected),
                    f"expected safe_alternative presence {expected}, got {actual}",
                )
            )
        elif key == "must_have_reflection_summary":
            actual = bool(_evo_lite(envelope).get("reflection_summary"))
            checks.append(
                _check(
                    f"{check_prefix}.must_have_reflection_summary",
                    actual is bool(expected),
                    f"expected reflection summary presence {expected}, got {actual}",
                )
            )
        elif key == "reflection_must_exclude":
            reflection_text = _reflection_text(envelope, record)
            lowered = reflection_text.lower()
            bad = [fragment for fragment in _string_list(expected) if fragment.lower() in lowered]
            checks.append(
                _check(
                    f"{check_prefix}.reflection_must_exclude",
                    not bad,
                    f"reflection contains forbidden fragments: {bad or 'none'}",
                )
            )
        elif key == "experience_saved":
            actual = bool(_evo_lite(envelope).get("experience_saved"))
            checks.append(
                _check(
                    f"{check_prefix}.experience_saved",
                    actual is bool(expected),
                    f"expected experience_saved={expected}, got {actual}",
                )
            )
        elif key == "expected_memory_type":
            actual = getattr(getattr(record, "memory_type", None), "value", None)
            checks.append(
                _check(
                    f"{check_prefix}.expected_memory_type",
                    actual == expected,
                    f"expected memory_type {expected}, got {actual}",
                )
            )
        elif key == "workflow_id":
            actual = _workflow_ids(envelope)
            checks.append(
                _check(
                    f"{check_prefix}.workflow_id",
                    str(expected) in actual,
                    f"expected workflow_id {expected}, got {sorted(actual)}",
                )
            )
        elif key == "workflow_must_still_pause_on_confirmation":
            executed_write_tools = sorted(WRITE_TOOL_NAMES.intersection(_executed_tools(envelope)))
            passed = (
                bool(expected)
                and _result_status(envelope) == "pending_confirmation"
                and bool(_risk(envelope).get("requires_confirmation"))
                and not executed_write_tools
            )
            checks.append(
                _check(
                    f"{check_prefix}.workflow_must_still_pause_on_confirmation",
                    passed,
                    "workflow-derived request must remain pending_confirmation and avoid "
                    f"write execution, got status={_result_status(envelope)} "
                    f"executed_write_tools={executed_write_tools}",
                )
            )
        elif key == "error_contains":
            actual = str(_result(envelope).get("error") or "")
            checks.append(
                _check(
                    f"{check_prefix}.error_contains",
                    str(expected) in actual,
                    f"expected error to contain {expected!r}, got {actual!r}",
                )
            )
        elif key == "confirmation_text_contains":
            actual = str(_result(envelope).get("confirmation_text") or "")
            checks.append(
                _check(
                    f"{check_prefix}.confirmation_text_contains",
                    str(expected) in actual,
                    f"expected confirmation text to contain {expected!r}, got {actual!r}",
                )
            )
        elif key == "intent":
            actual = str(_intent(envelope).get("intent") or "")
            checks.append(
                _check(
                    f"{check_prefix}.intent",
                    actual == expected,
                    f"expected intent {expected}, got {actual}",
                )
            )
        elif key == "intent_target":
            actual = _intent(envelope).get("target")
            checks.append(
                _check(
                    f"{check_prefix}.intent_target",
                    _mapping_contains(actual, expected),
                    f"expected intent target to include {expected}, got {actual}",
                )
            )
        elif key == "tool_args_include":
            for tool_name, expected_args in dict(expected).items():
                actual_args = _tool_args(envelope, tool_name)
                checks.append(
                    _check(
                        f"{check_prefix}.tool_args_include.{tool_name}",
                        _mapping_contains(actual_args, expected_args),
                        f"expected {tool_name} args to include {expected_args}, got {actual_args}",
                    )
                )

    return checks


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": passed, "detail": detail}


def _risk_level(envelope: Mapping[str, Any]) -> str | None:
    value = _risk(envelope).get("risk_level")
    return str(value) if value is not None else None


def _result_status(envelope: Mapping[str, Any]) -> str | None:
    value = _result(envelope).get("status")
    return str(value) if value is not None else None


def _risk(envelope: Mapping[str, Any]) -> dict[str, Any]:
    value = envelope.get("risk")
    return dict(value) if isinstance(value, dict) else {}


def _result(envelope: Mapping[str, Any]) -> dict[str, Any]:
    value = envelope.get("result")
    return dict(value) if isinstance(value, dict) else {}


def _execution(envelope: Mapping[str, Any]) -> dict[str, Any]:
    value = envelope.get("execution")
    return dict(value) if isinstance(value, dict) else {}


def _intent(envelope: Mapping[str, Any]) -> dict[str, Any]:
    value = envelope.get("intent")
    return dict(value) if isinstance(value, dict) else {}


def _timeline(envelope: Mapping[str, Any]) -> list[dict[str, Any]]:
    timeline = envelope.get("timeline")
    if not isinstance(timeline, list):
        return []
    return [item for item in timeline if isinstance(item, dict)]


def _executed_tools(envelope: Mapping[str, Any]) -> list[str]:
    tools: list[str] = []
    for item in _execution(envelope).get("results", []):
        if isinstance(item, dict):
            tool_name = item.get("tool_name")
            if isinstance(tool_name, str):
                tools.append(tool_name)
    return tools


def _tool_args(envelope: Mapping[str, Any], tool_name: str) -> dict[str, Any] | None:
    for step in _execution(envelope).get("steps", []):
        if isinstance(step, dict) and step.get("tool_name") == tool_name:
            args = step.get("args")
            return dict(args) if isinstance(args, dict) else {}
    return None


def _has_verification_step(envelope: Mapping[str, Any]) -> bool:
    for item in _timeline(envelope):
        intent = str(item.get("intent") or "")
        step_id = str(item.get("step_id") or "")
        if intent.startswith("verify_") or step_id.endswith("_verify"):
            return True
    return False


def _is_skipped_execution(envelope: Mapping[str, Any]) -> bool:
    execution = _execution(envelope)
    return (
        execution.get("status") == "skipped"
        and not execution.get("steps")
        and not execution.get("results")
    )


def _experience_record(orchestrator: Any, envelope: Mapping[str, Any]) -> Any:
    store = getattr(orchestrator, "experience_store", None)
    if store is None:
        return None
    memory_id = _evo_lite(envelope).get("memory_id")
    if not isinstance(memory_id, str) or not memory_id:
        return None
    getter = getattr(store, "get", None)
    if not callable(getter):
        return None
    return getter(memory_id)


def _evo_lite(envelope: Mapping[str, Any]) -> dict[str, Any]:
    value = envelope.get("evo_lite")
    return dict(value) if isinstance(value, dict) else {}


def _reflection_text(envelope: Mapping[str, Any], record: Any) -> str:
    parts = [str(_evo_lite(envelope).get("reflection_summary") or "")]
    if record is not None:
        parts.extend(
            [
                str(getattr(record, "summary", "") or ""),
                str(getattr(record, "lesson", "") or ""),
            ]
        )
    return " ".join(part for part in parts if part)


def _workflow_ids(envelope: Mapping[str, Any]) -> set[str]:
    workflow_ids: set[str] = set()
    plan = envelope.get("plan")
    steps = plan.get("steps") if isinstance(plan, dict) else None
    if not isinstance(steps, list):
        return workflow_ids
    for step in steps:
        if not isinstance(step, dict):
            continue
        target = step.get("target")
        if isinstance(target, dict):
            workflow_id = target.get("workflow_id")
            if isinstance(workflow_id, str) and workflow_id:
                workflow_ids.add(workflow_id)
    return workflow_ids


def _mapping_contains(actual: Any, expected: Any) -> bool:
    if not isinstance(expected, Mapping):
        return actual == expected
    if not isinstance(actual, Mapping):
        return False
    for key, expected_value in expected.items():
        if key not in actual:
            return False
        actual_value = actual[key]
        if isinstance(expected_value, Mapping):
            if not _mapping_contains(actual_value, expected_value):
                return False
        else:
            if actual_value != expected_value:
                return False
    return True


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]
