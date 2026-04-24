from __future__ import annotations

import inspect
import json
from collections import defaultdict
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from app.agent.memory import AgentMemory
from app.evolution import evaluate_execution


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
        "error_contains",
        "confirmation_text_contains",
        "intent",
        "intent_target",
        "tool_args_include",
        "status",
        "risk",
    }
)
ALLOWED_TURN_EXPECT_KEYS = frozenset(ALLOWED_EXPECTED_SAFETY_KEYS)
ALLOWED_BENCHMARK_KEYS = frozenset({"benchmark_id", "version", "cases"})
ALLOWED_MUTATION_FILE_KEYS = frozenset({"mutation_set_id", "version", "mutations"})
ALLOWED_REPLAY_CASE_KEYS = frozenset(
    {
        "case_id",
        "source_case_id",
        "mutation_id",
        "kind",
        "category",
        "description",
        "input",
        "turns",
        "environment_assumptions",
        "expected_risk",
        "expected_status",
        "assertions",
        "tags",
    }
)
ALLOWED_REPLAY_TURN_KEYS = frozenset({"input", "before_turn", "assertions", "expect"})
ALLOWED_ENVIRONMENT_KEYS = frozenset(
    {
        "executor",
        "users",
        "memory",
        "expects_experience_store",
        "experience_store_seed",
        "tool_behavior",
    }
)
ALLOWED_BEFORE_TURN_KEYS = frozenset(
    {
        "set_executor",
        "seed_users",
        "pending_action_update",
        "confirmation_token_update",
        "clear_checkpoint",
    }
)
ALLOWED_ASSERTION_GROUP_KEYS = frozenset(
    {"policy", "confirmation", "evidence", "evaluation", "timeline"}
)
ALLOWED_CONFIRMATION_ASSERTION_KEYS = frozenset(
    {"pending_action_present", "token_present", "token_error", "confirmation_status"}
)
ALLOWED_EVIDENCE_ASSERTION_KEYS = frozenset(
    {
        "required_sections_with_refs",
        "required_event_stages",
        "required_assertions",
        "refs_must_resolve",
    }
)
ALLOWED_EVALUATION_ASSERTION_KEYS = frozenset(
    {
        "task_success",
        "safety_success",
        "post_check_passed",
        "confirmation_ok",
        "needs_reflection",
        "experience_candidate",
        "tags_include",
        "tags_exclude",
        "evaluation_input_patch",
    }
)
ALLOWED_TIMELINE_ASSERTION_KEYS = frozenset(
    {
        "required_intents",
        "required_statuses",
        "final_status_must_not_be_success_if_any_of",
    }
)
WRITE_TOOL_NAMES = frozenset({"create_user_tool", "delete_user_tool"})
DATETIME_KEYS = frozenset({"created_at", "updated_at", "issued_at", "expires_at"})


class SafetyRegressionLoadError(ValueError):
    """Raised when the benchmark definition is invalid."""


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise SafetyRegressionLoadError("benchmark file must contain a JSON object")

    unknown_keys = sorted(set(payload) - ALLOWED_BENCHMARK_KEYS)
    if unknown_keys:
        raise SafetyRegressionLoadError(
            f"benchmark file contains unsupported keys: {', '.join(unknown_keys)}"
        )

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
        if _looks_like_replay_case(case):
            normalized = _normalize_replay_case(
                case,
                index=index,
                default_kind="base",
                require_mutation_fields=False,
            )
        else:
            normalized = _normalize_legacy_case(case, index=index)

        case_id = normalized["case_id"]
        if case_id in seen_case_ids:
            raise SafetyRegressionLoadError(f"duplicate case_id: {case_id}")
        seen_case_ids.add(case_id)
        normalized_cases.append(normalized)

    return normalized_cases


def load_mutations(path: str | Path) -> list[dict[str, Any]]:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise SafetyRegressionLoadError("mutation file must contain a JSON object")

    unknown_keys = sorted(set(payload) - ALLOWED_MUTATION_FILE_KEYS)
    if unknown_keys:
        raise SafetyRegressionLoadError(
            f"mutation file contains unsupported keys: {', '.join(unknown_keys)}"
        )

    mutation_set_id = payload.get("mutation_set_id")
    version = payload.get("version")
    mutations = payload.get("mutations")
    if not isinstance(mutation_set_id, str) or not mutation_set_id.strip():
        raise SafetyRegressionLoadError("mutation_set_id must be a non-empty string")
    if not isinstance(version, int):
        raise SafetyRegressionLoadError("version must be an integer")
    if not isinstance(mutations, list) or not mutations:
        raise SafetyRegressionLoadError("mutations must be a non-empty list")

    normalized_mutations: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()
    for index, mutation in enumerate(mutations, start=1):
        normalized = _normalize_replay_case(
            mutation,
            index=index,
            default_kind="mutation",
            require_mutation_fields=True,
        )
        case_id = normalized["case_id"]
        if case_id in seen_case_ids:
            raise SafetyRegressionLoadError(f"duplicate case_id: {case_id}")
        seen_case_ids.add(case_id)
        normalized_mutations.append(normalized)

    return normalized_mutations


def run_case(case: Mapping[str, Any], orchestrator: Any) -> dict[str, Any]:
    if case.get("schema_version") == "v2":
        return _run_replay_case(case, orchestrator)
    return _run_legacy_case(case, orchestrator)


def run_suite(
    cases: list[Mapping[str, Any]],
    orchestrator_factory: Any,
) -> dict[str, Any]:
    if not isinstance(cases, list) or not cases:
        raise SafetyRegressionLoadError("cases must be a non-empty list")

    known_case_ids = {
        str(case.get("case_id"))
        for case in cases
        if str(case.get("kind") or "base") != "mutation"
    }
    for case in cases:
        if str(case.get("kind") or "base") != "mutation":
            continue
        source_case_id = str(case.get("source_case_id") or "")
        if not source_case_id or source_case_id not in known_case_ids:
            raise SafetyRegressionLoadError(
                f"mutation case {case.get('case_id')} references unknown source_case_id {source_case_id!r}"
            )

    results: list[dict[str, Any]] = []
    for case in cases:
        orchestrator = _build_orchestrator(orchestrator_factory, case)
        results.append(run_case(case, orchestrator))

    summary = summarize_results(results)
    by_kind: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "passed": 0, "failed": 0}
    )
    for item in results:
        kind = str(item.get("kind") or "legacy")
        by_kind[kind]["total"] += 1
        if item.get("passed"):
            by_kind[kind]["passed"] += 1
        else:
            by_kind[kind]["failed"] += 1
    summary["by_kind"] = dict(by_kind)
    summary["case_results"] = results
    return summary


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


def _looks_like_replay_case(case: Any) -> bool:
    if not isinstance(case, dict):
        return False
    return any(key in case for key in ("input", "assertions", "environment_assumptions", "kind"))


def _normalize_legacy_case(case: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(case, dict):
        raise SafetyRegressionLoadError(f"case #{index} must be a JSON object")

    required_fields = {
        "case_id",
        "category",
        "description",
        "turns",
        "expected_risk",
        "expected_status",
        "expected_safety",
    }
    missing = sorted(field for field in required_fields if field not in case)
    if missing:
        raise SafetyRegressionLoadError(
            f"case #{index} is missing required fields: {', '.join(missing)}"
        )

    case_id = _require_non_empty_string(case["case_id"], f"case #{index} case_id")
    category = _require_non_empty_string(case["category"], f"case {case_id} category")
    description = _require_non_empty_string(case["description"], f"case {case_id} description")
    setup = _normalize_setup(case.get("setup", {}), case_id=case_id)
    turns = _normalize_legacy_turns(case["turns"], case_id=case_id)
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
        "schema_version": "v1",
        "case_id": case_id,
        "kind": "legacy",
        "category": category,
        "description": description,
        "setup": setup,
        "turns": turns,
        "expected_risk": expected_risk,
        "expected_status": expected_status,
        "expected_safety": expected_safety,
    }


def _normalize_replay_case(
    case: Any,
    *,
    index: int,
    default_kind: str,
    require_mutation_fields: bool,
) -> dict[str, Any]:
    if not isinstance(case, dict):
        raise SafetyRegressionLoadError(f"replay case #{index} must be a JSON object")

    unknown_keys = sorted(set(case) - ALLOWED_REPLAY_CASE_KEYS)
    if unknown_keys:
        raise SafetyRegressionLoadError(
            f"replay case #{index} contains unsupported keys: {', '.join(unknown_keys)}"
        )

    required_fields = {
        "case_id",
        "category",
        "description",
        "input",
        "turns",
        "environment_assumptions",
        "expected_risk",
        "expected_status",
        "assertions",
    }
    if require_mutation_fields:
        required_fields.update({"source_case_id", "mutation_id"})

    missing = sorted(field for field in required_fields if field not in case)
    if missing:
        raise SafetyRegressionLoadError(
            f"replay case #{index} is missing required fields: {', '.join(missing)}"
        )

    case_id = _require_non_empty_string(case["case_id"], f"replay case #{index} case_id")
    kind = _require_non_empty_string(case.get("kind", default_kind), f"case {case_id} kind")
    category = _require_non_empty_string(case["category"], f"case {case_id} category")
    description = _require_non_empty_string(case["description"], f"case {case_id} description")
    input_text = _require_non_empty_string(case["input"], f"case {case_id} input")
    turns = _normalize_replay_turns(case["turns"], case_id=case_id, input_text=input_text)
    environment_assumptions = _normalize_environment_assumptions(
        case["environment_assumptions"],
        case_id=case_id,
    )
    expected_risk = _require_non_empty_string(
        case["expected_risk"], f"case {case_id} expected_risk"
    )
    expected_status = _require_non_empty_string(
        case["expected_status"], f"case {case_id} expected_status"
    )
    assertions = _normalize_assertion_groups(
        case["assertions"],
        label=f"case {case_id} assertions",
    )
    tags = _normalize_string_list(case.get("tags", []), label=f"case {case_id} tags")

    source_case_id = None
    mutation_id = None
    if "source_case_id" in case:
        source_case_id = _require_non_empty_string(
            case["source_case_id"], f"case {case_id} source_case_id"
        )
    if "mutation_id" in case:
        mutation_id = _require_non_empty_string(case["mutation_id"], f"case {case_id} mutation_id")

    if kind == "mutation" and (not source_case_id or not mutation_id):
        raise SafetyRegressionLoadError(
            f"mutation case {case_id} must declare source_case_id and mutation_id"
        )

    return {
        "schema_version": "v2",
        "case_id": case_id,
        "source_case_id": source_case_id,
        "mutation_id": mutation_id,
        "kind": kind,
        "category": category,
        "description": description,
        "input": input_text,
        "turns": turns,
        "environment_assumptions": environment_assumptions,
        "expected_risk": expected_risk,
        "expected_status": expected_status,
        "assertions": assertions,
        "tags": tags,
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


def _normalize_legacy_turns(value: Any, *, case_id: str) -> list[dict[str, Any]]:
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


def _normalize_replay_turns(
    value: Any,
    *,
    case_id: str,
    input_text: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise SafetyRegressionLoadError(f"case {case_id} turns must be a non-empty list")

    normalized_turns: list[dict[str, Any]] = []
    for index, turn in enumerate(value, start=1):
        if not isinstance(turn, dict):
            raise SafetyRegressionLoadError(f"case {case_id} turn #{index} must be an object")

        unknown_keys = sorted(set(turn) - ALLOWED_REPLAY_TURN_KEYS)
        if unknown_keys:
            raise SafetyRegressionLoadError(
                f"case {case_id} turn #{index} contains unsupported keys: {', '.join(unknown_keys)}"
            )

        if "input" not in turn:
            raise SafetyRegressionLoadError(f"case {case_id} turn #{index} is missing input")
        if "expect" in turn and "assertions" in turn:
            raise SafetyRegressionLoadError(
                f"case {case_id} turn #{index} cannot contain both expect and assertions"
            )

        turn_input = _require_non_empty_string(
            turn["input"], f"case {case_id} turn #{index} input"
        )
        if index == 1 and turn_input != input_text:
            raise SafetyRegressionLoadError(
                f"case {case_id} first turn input must match the top-level input"
            )

        if "assertions" in turn:
            turn_assertions = _normalize_assertion_groups(
                turn["assertions"],
                label=f"case {case_id} turn #{index} assertions",
            )
        else:
            turn_assertions = {}
            if "expect" in turn:
                turn_assertions["policy"] = _normalize_expectation_mapping(
                    turn["expect"],
                    allowed_keys=ALLOWED_TURN_EXPECT_KEYS,
                    label=f"case {case_id} turn #{index} expect",
                )

        normalized_turns.append(
            {
                "input": turn_input,
                "before_turn": _normalize_before_turn(
                    turn.get("before_turn"),
                    case_id=case_id,
                    turn_index=index,
                ),
                "assertions": turn_assertions,
            }
        )
    return normalized_turns


def _normalize_environment_assumptions(value: Any, *, case_id: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SafetyRegressionLoadError(
            f"case {case_id} environment_assumptions must be an object"
        )

    unknown_keys = sorted(set(value) - ALLOWED_ENVIRONMENT_KEYS)
    if unknown_keys:
        raise SafetyRegressionLoadError(
            f"case {case_id} environment_assumptions contains unsupported keys: "
            f"{', '.join(unknown_keys)}"
        )

    normalized: dict[str, Any] = {}
    if "executor" in value:
        if not isinstance(value["executor"], dict):
            raise SafetyRegressionLoadError(
                f"case {case_id} environment_assumptions.executor must be an object"
            )
        normalized["executor"] = dict(value["executor"])
    if "users" in value:
        normalized["users"] = _normalize_users(value["users"], label=f"case {case_id} users")
    if "memory" in value:
        memory = value["memory"]
        if not isinstance(memory, dict):
            raise SafetyRegressionLoadError(
                f"case {case_id} environment_assumptions.memory must be an object"
            )
        unknown_memory_keys = sorted(set(memory) - ALLOWED_MEMORY_KEYS)
        if unknown_memory_keys:
            raise SafetyRegressionLoadError(
                f"case {case_id} environment_assumptions.memory contains unsupported keys: "
                f"{', '.join(unknown_memory_keys)}"
            )
        normalized["memory"] = dict(memory)
    normalized["expects_experience_store"] = bool(value.get("expects_experience_store", False))
    if "experience_store_seed" in value:
        seed = value["experience_store_seed"]
        if not isinstance(seed, list):
            raise SafetyRegressionLoadError(
                f"case {case_id} environment_assumptions.experience_store_seed must be a list"
            )
        if not all(isinstance(item, dict) for item in seed):
            raise SafetyRegressionLoadError(
                f"case {case_id} environment_assumptions.experience_store_seed must contain objects"
            )
        normalized["experience_store_seed"] = [dict(item) for item in seed]
    if "tool_behavior" in value:
        if not isinstance(value["tool_behavior"], dict):
            raise SafetyRegressionLoadError(
                f"case {case_id} environment_assumptions.tool_behavior must be an object"
            )
        normalized["tool_behavior"] = dict(value["tool_behavior"])
    return normalized


def _normalize_users(value: Any, *, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise SafetyRegressionLoadError(f"{label} must be an object")
    normalized: dict[str, dict[str, Any]] = {}
    for username, metadata in value.items():
        clean_username = _require_non_empty_string(username, f"{label} username")
        if metadata is None:
            normalized[clean_username] = {}
            continue
        if not isinstance(metadata, dict):
            raise SafetyRegressionLoadError(f"{label} user metadata must be an object")
        normalized[clean_username] = dict(metadata)
    return normalized


def _normalize_before_turn(value: Any, *, case_id: str, turn_index: int) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SafetyRegressionLoadError(
            f"case {case_id} turn #{turn_index} before_turn must be an object"
        )
    unknown_keys = sorted(set(value) - ALLOWED_BEFORE_TURN_KEYS)
    if unknown_keys:
        raise SafetyRegressionLoadError(
            f"case {case_id} turn #{turn_index} before_turn contains unsupported keys: "
            f"{', '.join(unknown_keys)}"
        )

    normalized: dict[str, Any] = {}
    if "set_executor" in value:
        if not isinstance(value["set_executor"], dict):
            raise SafetyRegressionLoadError(
                f"case {case_id} turn #{turn_index} before_turn.set_executor must be an object"
            )
        normalized["set_executor"] = dict(value["set_executor"])
    if "seed_users" in value:
        normalized["seed_users"] = _normalize_users(
            value["seed_users"],
            label=f"case {case_id} turn #{turn_index} before_turn.seed_users",
        )
    if "pending_action_update" in value:
        if not isinstance(value["pending_action_update"], dict):
            raise SafetyRegressionLoadError(
                f"case {case_id} turn #{turn_index} before_turn.pending_action_update must be an object"
            )
        normalized["pending_action_update"] = dict(value["pending_action_update"])
    if "confirmation_token_update" in value:
        if not isinstance(value["confirmation_token_update"], dict):
            raise SafetyRegressionLoadError(
                "case "
                f"{case_id} turn #{turn_index} before_turn.confirmation_token_update must be an object"
            )
        normalized["confirmation_token_update"] = dict(value["confirmation_token_update"])
    if "clear_checkpoint" in value:
        normalized["clear_checkpoint"] = bool(value["clear_checkpoint"])
    return normalized


def _normalize_assertion_groups(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SafetyRegressionLoadError(f"{label} must be an object")
    unknown_groups = sorted(set(value) - ALLOWED_ASSERTION_GROUP_KEYS)
    if unknown_groups:
        raise SafetyRegressionLoadError(
            f"{label} contains unsupported assertion groups: {', '.join(unknown_groups)}"
        )

    normalized: dict[str, Any] = {}
    if "policy" in value:
        normalized["policy"] = _normalize_expectation_mapping(
            value["policy"],
            allowed_keys=ALLOWED_EXPECTED_SAFETY_KEYS,
            label=f"{label}.policy",
        )
    if "confirmation" in value:
        normalized["confirmation"] = _normalize_expectation_mapping(
            value["confirmation"],
            allowed_keys=ALLOWED_CONFIRMATION_ASSERTION_KEYS,
            label=f"{label}.confirmation",
        )
    if "evidence" in value:
        normalized["evidence"] = _normalize_expectation_mapping(
            value["evidence"],
            allowed_keys=ALLOWED_EVIDENCE_ASSERTION_KEYS,
            label=f"{label}.evidence",
        )
    if "evaluation" in value:
        normalized["evaluation"] = _normalize_expectation_mapping(
            value["evaluation"],
            allowed_keys=ALLOWED_EVALUATION_ASSERTION_KEYS,
            label=f"{label}.evaluation",
        )
    if "timeline" in value:
        normalized["timeline"] = _normalize_expectation_mapping(
            value["timeline"],
            allowed_keys=ALLOWED_TIMELINE_ASSERTION_KEYS,
            label=f"{label}.timeline",
        )
    return normalized


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


def _normalize_string_list(value: Any, *, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SafetyRegressionLoadError(f"{label} must be a list")
    return [str(item).strip() for item in value if str(item).strip()]


def _require_non_empty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SafetyRegressionLoadError(f"{label} must be a non-empty string")
    return value.strip()


def _run_legacy_case(case: Mapping[str, Any], orchestrator: Any) -> dict[str, Any]:
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
            "kind": str(case.get("kind") or "legacy"),
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
        "kind": str(case.get("kind") or "legacy"),
        "category": category,
        "passed": first_failure is None,
        "reason": first_failure["detail"] if first_failure else "",
        "actual_risk": _risk_level(final_envelope),
        "actual_status": _result_status(final_envelope),
        "turn_results": turn_results,
        "checks": checks,
    }


def _run_replay_case(case: Mapping[str, Any], orchestrator: Any) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    case_id = str(case["case_id"])
    category = str(case["category"])
    kind = str(case.get("kind") or "base")

    _apply_environment_assumptions(orchestrator, case.get("environment_assumptions"))
    if bool(case.get("environment_assumptions", {}).get("expects_experience_store")) and getattr(
        orchestrator, "experience_store", None
    ) is None:
        checks.append(
            _check(
                "environment.experience_store",
                False,
                "case requires orchestrator.experience_store but none was provided",
            )
        )
        return {
            "case_id": case_id,
            "kind": kind,
            "source_case_id": case.get("source_case_id"),
            "mutation_id": case.get("mutation_id"),
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
        _apply_before_turn(
            orchestrator,
            turn.get("before_turn"),
            case_id=case_id,
            turn_index=turn_index,
        )
        envelope = orchestrator.run(turn["input"])
        turn_results.append(envelope)
        checks.extend(
            _run_assertion_groups(
                envelope,
                assertions=turn.get("assertions", {}),
                check_prefix=f"turn_{turn_index}",
                orchestrator=orchestrator,
            )
        )

    final_envelope = turn_results[-1]
    checks.extend(
        _run_assertion_groups(
            final_envelope,
            assertions=case["assertions"],
            check_prefix="final",
            orchestrator=orchestrator,
        )
    )
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
        "kind": kind,
        "source_case_id": case.get("source_case_id"),
        "mutation_id": case.get("mutation_id"),
        "category": category,
        "passed": first_failure is None,
        "reason": first_failure["detail"] if first_failure else "",
        "actual_risk": _risk_level(final_envelope),
        "actual_status": _result_status(final_envelope),
        "turn_results": turn_results,
        "checks": checks,
    }


def _apply_case_setup(orchestrator: Any, setup: Any) -> None:
    setup_data = setup if isinstance(setup, dict) else {}
    memory_data = setup_data.get("memory") if isinstance(setup_data.get("memory"), dict) else {}
    orchestrator.memory = AgentMemory(**memory_data)


def _apply_environment_assumptions(orchestrator: Any, assumptions: Any) -> None:
    data = assumptions if isinstance(assumptions, dict) else {}
    handler = getattr(orchestrator, "apply_replay_environment", None)
    if callable(handler):
        handler(data)
        return

    memory_data = data.get("memory") if isinstance(data.get("memory"), dict) else {}
    orchestrator.memory = AgentMemory(**memory_data)


def _apply_before_turn(
    orchestrator: Any,
    before_turn: Any,
    *,
    case_id: str,
    turn_index: int,
) -> None:
    hook = before_turn if isinstance(before_turn, dict) else {}
    if not hook:
        return

    handler = getattr(orchestrator, "apply_replay_before_turn", None)
    if callable(handler):
        handler(hook)
        return

    if "set_executor" in hook:
        executor = getattr(orchestrator, "executor", None)
        for key, value in dict(hook["set_executor"]).items():
            if executor is None:
                raise SafetyRegressionLoadError(
                    f"case {case_id} turn #{turn_index} before_turn.set_executor requires orchestrator.executor"
                )
            setattr(executor, key, value)

    if "seed_users" in hook:
        executor = getattr(orchestrator, "executor", None)
        if executor is None or not hasattr(executor, "users"):
            raise SafetyRegressionLoadError(
                f"case {case_id} turn #{turn_index} before_turn.seed_users requires executor.users"
            )
        executor.users = _coerce_user_seed(hook["seed_users"])

    if hook.get("clear_checkpoint"):
        memory = getattr(orchestrator, "memory", None)
        clearer = getattr(memory, "clear_pending_checkpoint", None)
        if not callable(clearer):
            raise SafetyRegressionLoadError(
                f"case {case_id} turn #{turn_index} before_turn.clear_checkpoint requires memory.clear_pending_checkpoint()"
            )
        clearer()

    if "pending_action_update" in hook:
        _apply_pending_action_update(
            orchestrator,
            dict(hook["pending_action_update"]),
            case_id=case_id,
            turn_index=turn_index,
        )

    if "confirmation_token_update" in hook:
        _apply_confirmation_token_update(
            orchestrator,
            dict(hook["confirmation_token_update"]),
            case_id=case_id,
            turn_index=turn_index,
        )


def _apply_pending_action_update(
    orchestrator: Any,
    updates: dict[str, Any],
    *,
    case_id: str,
    turn_index: int,
) -> None:
    memory = getattr(orchestrator, "memory", None)
    pending_action = getattr(memory, "pending_action", None)
    if pending_action is None:
        raise SafetyRegressionLoadError(
            f"case {case_id} turn #{turn_index} before_turn.pending_action_update requires a pending action"
        )
    memory.set_pending_action(
        pending_action.model_copy(update=_coerce_update_values(dict(updates)))
    )


def _apply_confirmation_token_update(
    orchestrator: Any,
    updates: dict[str, Any],
    *,
    case_id: str,
    turn_index: int,
) -> None:
    memory = getattr(orchestrator, "memory", None)
    pending_action = getattr(memory, "pending_action", None)
    if pending_action is None or pending_action.confirmation_token is None:
        raise SafetyRegressionLoadError(
            "case "
            f"{case_id} turn #{turn_index} before_turn.confirmation_token_update requires a pending confirmation token"
        )
    token = pending_action.confirmation_token.model_copy(
        update=_coerce_update_values(dict(updates))
    )
    memory.set_pending_action(pending_action.model_copy(update={"confirmation_token": token}))


def _coerce_user_seed(seed: Any) -> dict[str, dict[str, Any]]:
    normalized = _normalize_users(seed, label="seed_users")
    users: dict[str, dict[str, Any]] = {}
    for username, metadata in normalized.items():
        users[username] = {
            "uid": int(metadata.get("uid", 1001)),
            "gid": int(metadata.get("gid", metadata.get("uid", 1001))),
            "home": str(metadata.get("home", f"/home/{username}")),
            "shell": str(metadata.get("shell", "/bin/bash")),
        }
    return users


def _coerce_update_values(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {name: _coerce_update_values(item, key=name) for name, item in value.items()}
    if isinstance(value, list):
        return [_coerce_update_values(item, key=key) for item in value]
    if isinstance(value, str) and key in DATETIME_KEYS:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value
    return value


def _run_assertion_groups(
    envelope: Mapping[str, Any],
    *,
    assertions: Mapping[str, Any],
    check_prefix: str,
    orchestrator: Any,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if not assertions:
        return checks

    if "policy" in assertions:
        checks.extend(
            _run_expectations(
                envelope,
                expect=assertions["policy"],
                check_prefix=f"{check_prefix}.policy",
                orchestrator=orchestrator,
            )
        )
    if "confirmation" in assertions:
        checks.extend(
            _run_confirmation_assertions(
                envelope,
                expect=assertions["confirmation"],
                check_prefix=f"{check_prefix}.confirmation",
                orchestrator=orchestrator,
            )
        )
    if "evidence" in assertions:
        checks.extend(
            _run_evidence_assertions(
                envelope,
                expect=assertions["evidence"],
                check_prefix=f"{check_prefix}.evidence",
            )
        )
    if "evaluation" in assertions:
        checks.extend(
            _run_evaluation_assertions(
                envelope,
                expect=assertions["evaluation"],
                check_prefix=f"{check_prefix}.evaluation",
            )
        )
    if "timeline" in assertions:
        checks.extend(
            _run_timeline_assertions(
                envelope,
                expect=assertions["timeline"],
                check_prefix=f"{check_prefix}.timeline",
            )
        )
    return checks


def _run_confirmation_assertions(
    envelope: Mapping[str, Any],
    *,
    expect: Mapping[str, Any],
    check_prefix: str,
    orchestrator: Any,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    pending_action = _pending_action_payload(envelope, orchestrator)
    token = _confirmation_token_payload(envelope, orchestrator)
    confirmation_status = _confirmation_status_from_envelope(envelope)
    token_error = str(_result(envelope).get("error") or "")

    for key, expected in expect.items():
        if key == "pending_action_present":
            actual = bool(pending_action)
            checks.append(
                _check(
                    f"{check_prefix}.pending_action_present",
                    actual is bool(expected),
                    f"expected pending_action_present={expected}, got {actual}",
                )
            )
        elif key == "token_present":
            actual = bool(token)
            checks.append(
                _check(
                    f"{check_prefix}.token_present",
                    actual is bool(expected),
                    f"expected token_present={expected}, got {actual}",
                )
            )
        elif key == "token_error":
            checks.append(
                _check(
                    f"{check_prefix}.token_error",
                    token_error == str(expected),
                    f"expected token_error {expected}, got {token_error}",
                )
            )
        elif key == "confirmation_status":
            checks.append(
                _check(
                    f"{check_prefix}.confirmation_status",
                    confirmation_status == str(expected),
                    f"expected confirmation_status {expected}, got {confirmation_status}",
                )
            )
    return checks


def _run_evidence_assertions(
    envelope: Mapping[str, Any],
    *,
    expect: Mapping[str, Any],
    check_prefix: str,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    explanation_card = _explanation_card(envelope)
    evidence_chain = _evidence_chain(envelope)
    stages = {
        str(item.get("stage") or "")
        for item in evidence_chain.get("events", [])
        if isinstance(item, dict)
    }
    assertion_names = {
        str(item.get("name") or "")
        for item in evidence_chain.get("state_assertions", [])
        if isinstance(item, dict)
    }
    unresolved_refs = _unresolved_evidence_refs(envelope)

    for key, expected in expect.items():
        if key == "required_sections_with_refs":
            for section in _string_list(expected):
                section_payload = explanation_card.get(section)
                refs = []
                if isinstance(section_payload, dict):
                    refs = [item for item in section_payload.get("evidence_refs", []) if isinstance(item, str)]
                checks.append(
                    _check(
                        f"{check_prefix}.required_sections_with_refs.{section}",
                        bool(refs),
                        f"expected section {section} to contain evidence_refs, got {refs}",
                    )
                )
        elif key == "required_event_stages":
            missing = [stage for stage in _string_list(expected) if stage not in stages]
            checks.append(
                _check(
                    f"{check_prefix}.required_event_stages",
                    not missing,
                    f"missing evidence stages: {missing or 'none'}; got {sorted(stages)}",
                )
            )
        elif key == "required_assertions":
            missing = [name for name in _string_list(expected) if name not in assertion_names]
            checks.append(
                _check(
                    f"{check_prefix}.required_assertions",
                    not missing,
                    f"missing evidence assertions: {missing or 'none'}; got {sorted(assertion_names)}",
                )
            )
        elif key == "refs_must_resolve":
            actual = not unresolved_refs
            checks.append(
                _check(
                    f"{check_prefix}.refs_must_resolve",
                    actual is bool(expected),
                    f"expected refs_must_resolve={expected}, unresolved refs={sorted(unresolved_refs)}",
                )
            )
    return checks


def _run_evaluation_assertions(
    envelope: Mapping[str, Any],
    *,
    expect: Mapping[str, Any],
    check_prefix: str,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    evaluation_input = deepcopy(dict(envelope))
    patch = expect.get("evaluation_input_patch")
    if isinstance(patch, dict):
        evaluation_input = _deep_merge(evaluation_input, patch)
    evaluation = evaluate_execution(evaluation_input).model_dump(mode="json")
    actual_tags = {str(tag) for tag in evaluation.get("tags", [])}

    for key, expected in expect.items():
        if key == "evaluation_input_patch":
            continue
        if key == "tags_include":
            missing = [tag for tag in _string_list(expected) if tag not in actual_tags]
            checks.append(
                _check(
                    f"{check_prefix}.tags_include",
                    not missing,
                    f"missing evaluation tags: {missing or 'none'}; got {sorted(actual_tags)}",
                )
            )
            continue
        if key == "tags_exclude":
            forbidden = [tag for tag in _string_list(expected) if tag in actual_tags]
            checks.append(
                _check(
                    f"{check_prefix}.tags_exclude",
                    not forbidden,
                    f"forbidden evaluation tags present: {forbidden or 'none'}; got {sorted(actual_tags)}",
                )
            )
            continue

        actual = evaluation.get(key)
        checks.append(
            _check(
                f"{check_prefix}.{key}",
                actual is expected,
                f"expected {key}={expected}, got {actual}",
            )
        )
    return checks


def _run_timeline_assertions(
    envelope: Mapping[str, Any],
    *,
    expect: Mapping[str, Any],
    check_prefix: str,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    timeline = _timeline(envelope)
    intents = [str(item.get("intent") or "") for item in timeline]
    statuses = [str(item.get("status") or "") for item in timeline]
    final_status = _result_status(envelope) or ""

    for key, expected in expect.items():
        if key == "required_intents":
            missing = [intent for intent in _string_list(expected) if intent not in intents]
            checks.append(
                _check(
                    f"{check_prefix}.required_intents",
                    not missing,
                    f"missing timeline intents: {missing or 'none'}; got {intents}",
                )
            )
        elif key == "required_statuses":
            missing = [status for status in _string_list(expected) if status not in statuses]
            checks.append(
                _check(
                    f"{check_prefix}.required_statuses",
                    not missing,
                    f"missing timeline statuses: {missing or 'none'}; got {statuses}",
                )
            )
        elif key == "final_status_must_not_be_success_if_any_of":
            blockers = [status for status in _string_list(expected) if status in statuses]
            passed = not blockers or final_status != "success"
            checks.append(
                _check(
                    f"{check_prefix}.final_status_must_not_be_success_if_any_of",
                    passed,
                    "timeline blockers "
                    f"{blockers or 'none'} observed with final status {final_status or 'unknown'}",
                )
            )
    return checks


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


def _build_orchestrator(orchestrator_factory: Any, case: Mapping[str, Any]) -> Any:
    try:
        signature = inspect.signature(orchestrator_factory)
    except (TypeError, ValueError):
        return orchestrator_factory(case)

    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind
        in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
    ]
    has_varargs = any(
        parameter.kind == inspect.Parameter.VAR_POSITIONAL
        for parameter in signature.parameters.values()
    )
    if positional or has_varargs:
        return orchestrator_factory(case)
    return orchestrator_factory()


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


def _pending_action_payload(envelope: Mapping[str, Any], orchestrator: Any) -> dict[str, Any] | None:
    result_data = _result(envelope)
    pending_action = result_data.get("pending_action")
    if isinstance(pending_action, dict):
        return dict(pending_action)
    memory = getattr(orchestrator, "memory", None)
    stored = getattr(memory, "pending_action", None)
    if stored is None:
        return None
    payload = getattr(stored, "public_payload", None)
    if callable(payload):
        return dict(payload())
    if hasattr(stored, "model_dump"):
        return dict(stored.model_dump(mode="json"))
    return None


def _confirmation_token_payload(
    envelope: Mapping[str, Any],
    orchestrator: Any,
) -> dict[str, Any] | None:
    pending_action = _pending_action_payload(envelope, orchestrator)
    if not isinstance(pending_action, dict):
        return None
    token = pending_action.get("confirmation_token")
    return dict(token) if isinstance(token, dict) else None


def _confirmation_status_from_envelope(envelope: Mapping[str, Any]) -> str:
    risk_data = _risk(envelope)
    plan_data = envelope.get("plan")
    execution_data = _execution(envelope)
    result_data = _result(envelope)
    timeline = _timeline(envelope)

    plan_status = str(plan_data.get("status") or "").lower() if isinstance(plan_data, dict) else ""
    result_status = str(result_data.get("status") or "").lower()
    result_error = str(result_data.get("error") or "").lower()
    execution_results = execution_data.get("results") or []

    if result_error == "confirmation_text_mismatch" or result_error.startswith(
        "confirmation_token_"
    ) or result_error == "missing_confirmation_token":
        return "mismatch"
    if result_status == "cancelled" or plan_status == "cancelled":
        return "cancelled"
    if result_status == "pending_confirmation" or plan_status == "pending_confirmation":
        return "pending"
    if plan_status == "confirmed":
        return "confirmed"
    if any(str(item.get("status") or "").lower() == "pending_confirmation" for item in timeline):
        return "pending"
    if risk_data.get("requires_confirmation") and execution_results:
        return "confirmed"
    return "not_required"


def _explanation_card(envelope: Mapping[str, Any]) -> dict[str, Any]:
    value = envelope.get("explanation_card")
    return dict(value) if isinstance(value, dict) else {}


def _evidence_chain(envelope: Mapping[str, Any]) -> dict[str, Any]:
    value = envelope.get("evidence_chain")
    return dict(value) if isinstance(value, dict) else {"events": [], "state_assertions": []}


def _all_evidence_ids(envelope: Mapping[str, Any]) -> set[str]:
    evidence_chain = _evidence_chain(envelope)
    ids: set[str] = set()
    for event in evidence_chain.get("events", []):
        if isinstance(event, dict):
            event_id = event.get("event_id")
            if isinstance(event_id, str) and event_id:
                ids.add(event_id)
    for assertion in evidence_chain.get("state_assertions", []):
        if isinstance(assertion, dict):
            assertion_id = assertion.get("assertion_id")
            if isinstance(assertion_id, str) and assertion_id:
                ids.add(assertion_id)
    return ids


def _unresolved_evidence_refs(envelope: Mapping[str, Any]) -> set[str]:
    valid_ids = _all_evidence_ids(envelope)
    refs: set[str] = set()
    for section in _explanation_card(envelope).values():
        if not isinstance(section, dict):
            continue
        refs.update(
            str(item)
            for item in section.get("evidence_refs", [])
            if isinstance(item, str) and item.strip()
        )
    for assertion in _evidence_chain(envelope).get("state_assertions", []):
        if not isinstance(assertion, dict):
            continue
        refs.update(
            str(item)
            for item in assertion.get("evidence_refs", [])
            if isinstance(item, str) and item.strip()
        )
    return {ref for ref in refs if ref not in valid_ids}


def _deep_merge(base: Any, patch: Any) -> Any:
    if isinstance(base, dict) and isinstance(patch, dict):
        merged = deepcopy(base)
        for key, value in patch.items():
            if key in merged:
                merged[key] = _deep_merge(merged[key], value)
            else:
                merged[key] = deepcopy(value)
        return merged
    return deepcopy(patch)


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
