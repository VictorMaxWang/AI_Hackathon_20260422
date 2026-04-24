from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agent.confirmation import (
    PendingAction,
    confirmation_text_for,
    issue_confirmation_token,
    is_cancel_pending_text,
    stable_hash,
    stable_file_content_hash,
    validate_confirmation_token,
)
from app.agent.memory import AgentMemory
from app.agent.parser import ReadonlyParser
from app.agent.planner import (
    MultistepPlanner,
    PlannedToolCall,
    ReadonlyPlan,
    ReadonlyPlanner,
)
from app.agent.previews import build_blast_radius_preview, build_policy_simulator
from app.agent.recovery import build_recovery_suggestion
from app.agent.summarizer import ReadonlySummarizer
from app.evolution.experience_store import ExperienceStore
from app.evolution.init import apply_evo_lite_hook
from app.evolution.workflows import match_workflow_template
from app.models import (
    EnvironmentSnapshot,
    IntentTarget,
    ParsedIntent,
    PolicyDecision,
    RiskLevel,
    ToolResult,
)
from app.models.evidence import (
    EvidenceBuilder,
    EvidenceChain,
    EvidenceSeverity,
    EvidenceStage,
)
from app.models.intent import ExecutionPlan, PlanStep
from app.policy import evaluate as evaluate_policy
from app.tools.disk import disk_usage_tool
from app.tools.env_probe import env_probe_tool
from app.tools.file_search import file_search_tool
from app.tools.port import port_query_tool
from app.tools.process import process_query_tool
from app.tools.user import MIN_NORMAL_UID, _lookup_user, create_user_tool, delete_user_tool


ToolCallable = Callable[..., ToolResult]
EnvProbeCallable = Callable[[Any], EnvironmentSnapshot]

CREATE_USER_INTENT = "create_user"
DELETE_USER_INTENT = "delete_user"
ENV_PROBE_INTENT = "env_probe"
PORT_QUERY_INTENT = "query_port"
PROCESS_QUERY_INTENT = "query_process"
CREATE_USER_TOOL_NAME = "create_user_tool"
DELETE_USER_TOOL_NAME = "delete_user_tool"
ENV_PROBE_TOOL_NAME = "env_probe_tool"
PORT_QUERY_TOOL_NAME = "port_query_tool"
PROCESS_QUERY_TOOL_NAME = "process_query_tool"
CONTINUOUS_PENDING_KEY = "continuous_task"
CHECKPOINT_KEY = "checkpoint"
VERIFY_USER_EXISTS_INTENT = "verify_user_exists"
VERIFY_USER_ABSENT_INTENT = "verify_user_absent"
CHECKPOINT_SAVED_INTENT = "checkpoint_saved"
CONTRACT_REVALIDATED_INTENT = "contract_revalidated"
CONTRACT_DRIFT_INTENT = "contract_drift"
POLICY_DIR = Path(__file__).resolve().parents[1] / "policy"


class ReadonlyOrchestrator:
    """Phase 1 read-only natural language closed loop."""

    def __init__(
        self,
        executor: Any,
        *,
        parser: ReadonlyParser | None = None,
        planner: ReadonlyPlanner | None = None,
        multistep_planner: MultistepPlanner | None = None,
        summarizer: ReadonlySummarizer | None = None,
        memory: AgentMemory | None = None,
        env_probe: EnvProbeCallable = env_probe_tool,
        disk_tool: ToolCallable = disk_usage_tool,
        file_search_tool_fn: ToolCallable = file_search_tool,
        process_query_tool_fn: ToolCallable = process_query_tool,
        port_query_tool_fn: ToolCallable = port_query_tool,
        create_user_tool_fn: ToolCallable = create_user_tool,
        delete_user_tool_fn: ToolCallable = delete_user_tool,
        evo_lite_enabled: bool = True,
        experience_store: ExperienceStore | None = None,
    ) -> None:
        self.executor = executor
        self.parser = parser or ReadonlyParser()
        self.planner = planner or ReadonlyPlanner()
        self.multistep_planner = multistep_planner or MultistepPlanner()
        self.summarizer = summarizer or ReadonlySummarizer()
        self.memory = memory or AgentMemory()
        self.env_probe = env_probe
        self.evo_lite_enabled = evo_lite_enabled
        self.experience_store = experience_store
        self.tools: dict[str, ToolCallable] = {
            "disk_usage_tool": disk_tool,
            "file_search_tool": file_search_tool_fn,
            "process_query_tool": process_query_tool_fn,
            "port_query_tool": port_query_tool_fn,
            CREATE_USER_TOOL_NAME: create_user_tool_fn,
            DELETE_USER_TOOL_NAME: delete_user_tool_fn,
        }

    def run(self, raw_user_input: str) -> dict[str, Any]:
        pending_action = self.memory.pending_action
        if pending_action is not None:
            if is_cancel_pending_text(raw_user_input):
                return self._cancel_pending_action(raw_user_input, pending_action)
            if pending_action.matches_confirmation(raw_user_input):
                if _is_continuous_pending(pending_action):
                    return self._resume_continuous_action(raw_user_input, pending_action)
                return self._execute_pending_action(raw_user_input, pending_action)
            if _is_continuous_pending(pending_action):
                return self._continuous_confirmation_mismatch(raw_user_input, pending_action)
            return self._pending_confirmation_mismatch(raw_user_input, pending_action)

        if _should_try_continuous_plan(raw_user_input):
            continuous_plan = self.multistep_planner.plan(raw_user_input, memory=self.memory)
            if _looks_like_contextual_delete(raw_user_input):
                contextual_plan = self.multistep_planner.plan(
                    _strip_delete_sensitivity_phrase(raw_user_input),
                    memory=self.memory,
                )
                if _should_prefer_contextual_delete_plan(continuous_plan, contextual_plan):
                    continuous_plan = contextual_plan.model_copy(
                        update={"raw_user_input": raw_user_input}
                    )
            if continuous_plan.supported:
                return self._run_continuous_plan(continuous_plan)

        parsed_intent = _parse_confirmable_user_request(raw_user_input) or self.parser.parse(
            raw_user_input,
            memory=self.memory,
        )
        if _has_unresolved_context_ref(parsed_intent):
            return self._unresolved_context_response(parsed_intent)

        risk = evaluate_policy(parsed_intent)
        if risk.risk_level == RiskLevel.S3:
            plan = ReadonlyPlan(status="refused", reason=_policy_refusal_reason(risk))
            explanation = self.summarizer.summarize(
                parsed_intent,
                status="refused",
                reason=plan.reason,
                risk=risk,
            )
            return self._envelope(
                parsed_intent=parsed_intent,
                environment={
                    "status": "not_collected",
                    "reason": "s3_refused_before_execution",
                },
                risk=risk,
                plan=plan,
                execution={"status": "skipped", "steps": [], "results": []},
                result={
                    "status": "refused",
                    "data": None,
                    "error": plan.reason,
                },
                explanation=explanation,
            )

        if _requires_pending_confirmation(risk):
            pending_action = self._build_pending_action(parsed_intent, risk)
            if pending_action is not None:
                self.memory.remember_intent(parsed_intent, risk_level=risk.risk_level)
                self.memory.set_pending_action(pending_action)
                return self._pending_confirmation_response(
                    parsed_intent,
                    risk,
                    pending_action,
                )

        plan = self.planner.plan(parsed_intent)
        if not plan.ready and risk.allow:
            risk = _risk_decision(plan)

        if not plan.ready:
            explanation = self.summarizer.summarize(
                parsed_intent,
                status=plan.status,
                reason=plan.reason,
            )
            return self._envelope(
                parsed_intent=parsed_intent,
                environment={
                    "status": "not_collected",
                    "reason": "unsupported_or_refused_before_execution",
                },
                risk=risk,
                plan=plan,
                execution={"status": "skipped", "steps": [], "results": []},
                result={
                    "status": plan.status,
                    "data": None,
                    "error": plan.reason,
                },
                explanation=explanation,
            )

        execution_steps: list[dict[str, Any]] = []
        execution_results: list[dict[str, Any]] = []

        env_started = _utc_now()
        try:
            environment_snapshot = self.env_probe(self.executor)
        except Exception as exc:
            environment = {
                "status": "error",
                "snapshot": None,
                "error": str(exc),
            }
            execution_steps.append(
                _execution_step("env_probe_tool", {}, env_started, False, str(exc))
            )
            explanation = self.summarizer.summarize(
                parsed_intent,
                status="failed",
                reason="环境探测失败，未继续执行只读工具",
            )
            return self._envelope(
                parsed_intent=parsed_intent,
                environment=environment,
                risk=PolicyDecision(
                    risk_level=RiskLevel.S0,
                    allow=False,
                    requires_confirmation=False,
                    reasons=["environment probe failed"],
                ),
                plan=plan,
                execution={
                    "status": "failed",
                    "steps": execution_steps,
                    "results": execution_results,
                },
                result={"status": "failed", "data": None, "error": str(exc)},
                explanation=explanation,
            )

        environment = {
            "status": "ok",
            "snapshot": environment_snapshot.model_dump(mode="json"),
        }
        execution_steps.append(_execution_step("env_probe_tool", {}, env_started, True, None))
        execution_results.append(
            {
                "tool_name": "env_probe_tool",
                "success": True,
                "data": environment["snapshot"],
                "error": None,
            }
        )

        tool_result: ToolResult | None = None
        for step in plan.steps:
            tool_started = _utc_now()
            tool = self.tools.get(step.tool_name)
            if tool is None:
                error = f"tool is not whitelisted: {step.tool_name}"
                execution_steps.append(
                    _execution_step(step.tool_name, step.args, tool_started, False, error)
                )
                tool_result = ToolResult(
                    tool_name=step.tool_name,
                    success=False,
                    data=None,
                    error=error,
                )
                break

            try:
                tool_result = tool(self.executor, **step.args)
            except Exception as exc:
                tool_result = ToolResult(
                    tool_name=step.tool_name,
                    success=False,
                    data=None,
                    error=str(exc),
                )

            execution_steps.append(
                _execution_step(
                    step.tool_name,
                    step.args,
                    tool_started,
                    tool_result.success,
                    tool_result.error,
                )
            )
            execution_results.append(tool_result.model_dump(mode="json"))
            if not tool_result.success:
                break

        final_status = "success" if tool_result is not None and tool_result.success else "failed"
        explanation = self.summarizer.summarize(
            parsed_intent,
            status=final_status,
            tool_result=tool_result,
            reason=tool_result.error if tool_result else "没有执行任何只读工具",
        )
        if final_status == "success":
            self.memory.remember_intent(parsed_intent, risk_level=risk.risk_level)

        return self._envelope(
            parsed_intent=parsed_intent,
            environment=environment,
            risk=risk,
            plan=plan,
            execution={
                "status": final_status,
                "steps": execution_steps,
                "results": execution_results,
            },
            result={
                "status": final_status,
                "tool_name": tool_result.tool_name if tool_result else None,
                "data": tool_result.data if tool_result else None,
                "error": tool_result.error if tool_result else None,
            },
            explanation=explanation,
        )

    def _run_continuous_plan(
        self,
        plan: ExecutionPlan,
        *,
        start_index: int = 0,
        timeline: list[dict[str, Any]] | None = None,
        execution_steps: list[dict[str, Any]] | None = None,
        execution_results: list[dict[str, Any]] | None = None,
        environment: dict[str, Any] | None = None,
        step_results: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        timeline = list(timeline or [])
        execution_steps = list(execution_steps or [])
        execution_results = list(execution_results or [])
        environment = dict(environment or {"status": "not_collected", "snapshot": None})
        step_results = dict(step_results or {})
        last_risk = _continuous_risk_from_timeline(timeline)

        for index in range(start_index, len(plan.steps)):
            step = plan.steps[index]
            dependency_abort = _dependency_abort_reason(step, timeline)
            if dependency_abort is not None:
                timeline.append(
                    _timeline_entry(
                        step_id=step.step_id,
                        intent=step.intent,
                        risk=RiskLevel.S0,
                        status="aborted",
                        result_summary=dependency_abort,
                    )
                )
                step_results[step.step_id] = _stored_step_result(
                    step,
                    success=False,
                    status="aborted",
                    error=dependency_abort,
                )
                continue

            condition_skip = _condition_skip_reason(step, environment, step_results)
            if condition_skip is not None:
                timeline.append(
                    _timeline_entry(
                        step_id=step.step_id,
                        intent=step.intent,
                        risk=RiskLevel.S0,
                        status="skipped",
                        result_summary=condition_skip,
                    )
                )
                step_results[step.step_id] = _stored_step_result(
                    step,
                    success=False,
                    status="skipped",
                    error=condition_skip,
                )
                continue

            parsed_intent = _parsed_intent_from_plan_step(
                step,
                raw_user_input=plan.raw_user_input,
                step_results=step_results,
            )
            risk = evaluate_policy(parsed_intent)
            last_risk = risk

            if not risk.allow:
                reason = _policy_refusal_reason(risk)
                timeline.append(
                    _timeline_entry(
                        step_id=step.step_id,
                        intent=step.intent,
                        risk=risk.risk_level,
                        status="refused",
                        result_summary=reason,
                    )
                )
                step_results[step.step_id] = _stored_step_result(
                    step,
                    success=False,
                    status="refused",
                    error=reason,
                )
                _append_aborted_remaining_steps(
                    plan.steps[index + 1 :],
                    timeline,
                    failed_step_id=step.step_id,
                    reason=reason,
                )
                return self._continuous_finished_response(
                    plan=plan,
                    environment=environment,
                    risk=risk,
                    timeline=timeline,
                    execution_steps=execution_steps,
                    execution_results=execution_results,
                )

            if _requires_pending_confirmation(risk):
                checkpoint = self._build_continuous_step_checkpoint(
                    step,
                    step_index=index,
                    environment=environment,
                )
                pending_context_timeline = list(timeline)
                if checkpoint is not None:
                    pending_context_timeline.append(_checkpoint_timeline_entry(step, checkpoint))
                pending_action = _pending_action_from_continuous_step(
                    parsed_intent=parsed_intent,
                    risk=risk,
                    step=step,
                    plan=plan,
                    step_index=index,
                    timeline=pending_context_timeline,
                    execution_steps=execution_steps,
                    execution_results=execution_results,
                    environment=environment,
                    step_results=step_results,
                    checkpoint=checkpoint,
                    host_id=_probe_confirmation_host_id(self.executor),
                    policy_version=_current_policy_version(),
                )
                self.memory.remember_intent(parsed_intent, risk_level=risk.risk_level)
                self.memory.set_pending_action(pending_action)

                pending_timeline = list(pending_context_timeline)
                pending_timeline.append(
                    _timeline_entry(
                        step_id=step.step_id,
                        intent=step.intent,
                        risk=risk.risk_level,
                        status="pending_confirmation",
                        result_summary=_pending_step_summary(
                            step,
                            risk,
                            pending_action.confirmation_text,
                        ),
                    )
                )
                explanation = self.summarizer.summarize_continuous(
                    status="pending_confirmation",
                    timeline=pending_timeline,
                    confirmation_text=pending_action.confirmation_text,
                    pending_intent=step.intent,
                )
                return self._continuous_envelope(
                    plan=plan,
                    environment=environment,
                    risk=risk,
                    timeline=pending_timeline,
                    execution={
                        "status": "pending_confirmation",
                        "steps": execution_steps,
                        "results": execution_results,
                    },
                    result={
                        "status": "pending_confirmation",
                        "data": None,
                        "error": None,
                        "confirmation_text": pending_action.confirmation_text,
                        "pending_action": pending_action.public_payload(),
                    },
                    explanation=explanation,
                )

            tool_result, execution_step = self._execute_continuous_step(
                step,
                parsed_intent,
                step_results=step_results,
            )
            execution_steps.append(execution_step)
            execution_results.append(tool_result.model_dump(mode="json"))
            if step.intent == ENV_PROBE_INTENT and tool_result.success:
                environment = {"status": "ok", "snapshot": tool_result.data}

            step_results[step.step_id] = _stored_step_result(
                step,
                success=tool_result.success,
                status="success" if tool_result.success else "failed",
                data=tool_result.data,
                error=tool_result.error,
            )
            timeline.append(_timeline_entry_from_tool_result(step, risk, tool_result))

            if tool_result.success:
                self.memory.remember_intent(parsed_intent, risk_level=risk.risk_level)
                verify_entry = _verification_timeline_entry(step, risk, tool_result)
                if verify_entry is not None:
                    timeline.append(verify_entry)
                continue

            _append_aborted_remaining_steps(
                plan.steps[index + 1 :],
                timeline,
                failed_step_id=step.step_id,
                reason=tool_result.error or "previous step failed",
            )
            return self._continuous_finished_response(
                plan=plan,
                environment=environment,
                risk=risk,
                timeline=timeline,
                execution_steps=execution_steps,
                execution_results=execution_results,
            )

        return self._continuous_finished_response(
            plan=plan,
            environment=environment,
            risk=last_risk,
            timeline=timeline,
            execution_steps=execution_steps,
            execution_results=execution_results,
        )

    def _build_continuous_step_checkpoint(
        self,
        step: PlanStep,
        *,
        step_index: int,
        environment: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not step.checkpointable:
            return None
        return _build_step_checkpoint(
            step=step,
            step_index=step_index,
            environment=environment,
            executor=self.executor,
        )

    def _revalidate_continuous_step_checkpoint(
        self,
        step: PlanStep,
        *,
        checkpoint: dict[str, Any],
        environment: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
        return _revalidate_step_checkpoint(
            step=step,
            checkpoint=checkpoint,
            environment=environment,
            executor=self.executor,
            env_probe=self.env_probe,
        )

    def _resume_continuous_action(
        self,
        raw_user_input: str,
        pending_action: PendingAction,
    ) -> dict[str, Any]:
        plan = ExecutionPlan.model_validate(pending_action.context["plan"])
        pending_step_index = int(pending_action.context["pending_step_index"])
        step = plan.steps[pending_step_index]
        timeline = list(pending_action.context.get("timeline") or [])
        execution_steps = list(pending_action.context.get("execution_steps") or [])
        execution_results = list(pending_action.context.get("execution_results") or [])
        environment = dict(
            pending_action.context.get("environment")
            or {"status": "not_collected", "snapshot": None}
        )
        step_results = dict(pending_action.context.get("step_results") or {})

        parsed_intent = _parsed_intent_from_pending(pending_action, raw_user_input)
        risk = evaluate_policy(parsed_intent)
        if not _requires_pending_confirmation(risk):
            self.memory.clear_pending_action()
            reason = _policy_refusal_reason(risk)
            timeline.append(
                _timeline_entry(
                    step_id=step.step_id,
                    intent=step.intent,
                    risk=risk.risk_level,
                    status="refused",
                    result_summary=reason,
                )
            )
            return self._continuous_envelope(
                plan=plan,
                environment=environment,
                risk=risk,
                timeline=timeline,
                execution={"status": "refused", "steps": execution_steps, "results": execution_results},
                result={"status": "refused", "data": None, "error": reason},
                explanation=self.summarizer.summarize_continuous(
                    status="refused",
                    timeline=timeline,
                    reason=reason,
                    pending_intent=step.intent,
                ),
            )

        token_error = _validate_pending_confirmation_token_binding(
            pending_action,
            risk=risk,
            host_id=_probe_confirmation_host_id(self.executor),
            policy_version=_current_policy_version(),
        )
        if token_error is not None:
            return self._invalid_continuous_confirmation_token_response(
                raw_user_input=raw_user_input,
                pending_action=pending_action,
                error_code=token_error,
            )

        checkpoint = self.memory.get_pending_checkpoint()
        if checkpoint is None:
            self.memory.clear_pending_action()
            reason = "safe checkpoint is missing; resume is refused"
            timeline.append(_drift_timeline_entry(step, reason))
            _append_aborted_remaining_steps(
                plan.steps[pending_step_index + 1 :],
                timeline,
                failed_step_id=step.step_id,
                reason=reason,
            )
            return self._continuous_finished_response(
                plan=plan,
                environment=environment,
                risk=PolicyDecision(
                    risk_level=pending_action.risk_level,
                    allow=False,
                    requires_confirmation=False,
                    reasons=[reason],
                ),
                timeline=timeline,
                execution_steps=execution_steps,
                execution_results=execution_results,
            )

        environment, revalidation_entries, revalidation_error = (
            self._revalidate_continuous_step_checkpoint(
                step,
                checkpoint=checkpoint,
                environment=environment,
            )
        )
        timeline.extend(revalidation_entries)
        if revalidation_error is not None:
            self.memory.clear_pending_action()
            _append_aborted_remaining_steps(
                plan.steps[pending_step_index + 1 :],
                timeline,
                failed_step_id=step.step_id,
                reason=revalidation_error,
            )
            return self._continuous_finished_response(
                plan=plan,
                environment=environment,
                risk=PolicyDecision(
                    risk_level=pending_action.risk_level,
                    allow=False,
                    requires_confirmation=False,
                    reasons=[revalidation_error],
                ),
                timeline=timeline,
                execution_steps=execution_steps,
                execution_results=execution_results,
            )

        self.memory.clear_pending_action()
        tool_result, execution_step = self._execute_continuous_step(
            step,
            parsed_intent,
            step_results=step_results,
        )
        execution_steps.append(execution_step)
        execution_results.append(tool_result.model_dump(mode="json"))
        step_results[step.step_id] = _stored_step_result(
            step,
            success=tool_result.success,
            status="success" if tool_result.success else "failed",
            data=tool_result.data,
            error=tool_result.error,
        )
        timeline.append(_timeline_entry_from_tool_result(step, risk, tool_result))

        if tool_result.success:
            self.memory.remember_intent(parsed_intent, risk_level=risk.risk_level)
            verify_entry = _verification_timeline_entry(step, risk, tool_result)
            if verify_entry is not None:
                timeline.append(verify_entry)
            return self._run_continuous_plan(
                plan,
                start_index=pending_step_index + 1,
                timeline=timeline,
                execution_steps=execution_steps,
                execution_results=execution_results,
                environment=environment,
                step_results=step_results,
            )

        _append_aborted_remaining_steps(
            plan.steps[pending_step_index + 1 :],
            timeline,
            failed_step_id=step.step_id,
            reason=tool_result.error or "confirmed step failed",
        )
        return self._continuous_finished_response(
            plan=plan,
            environment=environment,
            risk=risk,
            timeline=timeline,
            execution_steps=execution_steps,
            execution_results=execution_results,
        )

    def _continuous_confirmation_mismatch(
        self,
        raw_user_input: str,
        pending_action: PendingAction,
    ) -> dict[str, Any]:
        del raw_user_input

        plan = ExecutionPlan.model_validate(pending_action.context["plan"])
        step = plan.steps[int(pending_action.context["pending_step_index"])]
        timeline = list(pending_action.context.get("timeline") or [])
        timeline.append(
            _timeline_entry(
                step_id=step.step_id,
                intent=step.intent,
                risk=pending_action.risk_level,
                status="pending_confirmation",
                result_summary=(
                    "确认语不匹配，未执行任何工具。"
                    f"请重新输入精确确认语：{pending_action.confirmation_text}"
                ),
            )
        )
        risk = PolicyDecision(
            risk_level=pending_action.risk_level,
            allow=False,
            requires_confirmation=True,
            confirmation_text=pending_action.confirmation_text,
            reasons=["pending continuous step requires exact confirmation text"],
        )
        return self._continuous_envelope(
            plan=plan,
            environment=dict(
                pending_action.context.get("environment")
                or {"status": "not_collected", "snapshot": None}
            ),
            risk=risk,
            timeline=timeline,
            execution={
                "status": "pending_confirmation",
                "steps": list(pending_action.context.get("execution_steps") or []),
                "results": list(pending_action.context.get("execution_results") or []),
            },
            result={
                "status": "pending_confirmation",
                "data": None,
                "error": "confirmation_text_mismatch",
                "confirmation_text": pending_action.confirmation_text,
                "pending_action": pending_action.public_payload(),
            },
            explanation=self.summarizer.summarize_continuous(
                status="pending_confirmation",
                timeline=timeline,
                confirmation_text=pending_action.confirmation_text,
                pending_intent=step.intent,
                reason="confirmation_text_mismatch",
            ),
        )

    def _invalid_continuous_confirmation_token_response(
        self,
        *,
        raw_user_input: str,
        pending_action: PendingAction,
        error_code: str,
    ) -> dict[str, Any]:
        self.memory.clear_pending_action()
        plan = ExecutionPlan.model_validate(pending_action.context["plan"])
        step = plan.steps[int(pending_action.context["pending_step_index"])]
        timeline = list(pending_action.context.get("timeline") or [])
        reason = _confirmation_token_error_reason(error_code)
        timeline.append(
            _timeline_entry(
                step_id=step.step_id,
                intent=step.intent,
                risk=pending_action.risk_level,
                status="refused",
                result_summary=reason,
            )
        )
        if error_code in {
            "confirmation_token_host_mismatch",
            "confirmation_token_target_mismatch",
        }:
            timeline.append(_drift_timeline_entry(step, reason))
        parsed_intent = _parsed_intent_from_pending(pending_action, raw_user_input)
        risk = PolicyDecision(
            risk_level=pending_action.risk_level,
            allow=False,
            requires_confirmation=False,
            confirmation_text=None,
            reasons=[reason],
        )
        return self._continuous_envelope(
            plan=plan,
            environment=dict(
                pending_action.context.get("environment")
                or {"status": "not_collected", "snapshot": None}
            ),
            risk=risk,
            timeline=timeline,
            execution={
                "status": "refused",
                "steps": list(pending_action.context.get("execution_steps") or []),
                "results": list(pending_action.context.get("execution_results") or []),
            },
            result={
                "status": "refused",
                "data": {
                    "invalidated_pending_action": pending_action.public_payload(),
                    "raw_user_input": raw_user_input,
                    "intent": parsed_intent.intent,
                },
                "error": error_code,
            },
            explanation=self.summarizer.summarize_continuous(
                status="refused",
                timeline=timeline,
                reason=reason,
                pending_intent=step.intent,
            ),
        )

    def _execute_continuous_step(
        self,
        step: PlanStep,
        parsed_intent: ParsedIntent,
        *,
        step_results: dict[str, dict[str, Any]],
    ) -> tuple[ToolResult, dict[str, Any]]:
        del parsed_intent

        tool_name, args = _tool_for_plan_step(step, step_results)
        tool_started = _utc_now()
        if tool_name == ENV_PROBE_TOOL_NAME:
            try:
                snapshot = self.env_probe(self.executor)
                tool_result = ToolResult(
                    tool_name=ENV_PROBE_TOOL_NAME,
                    success=True,
                    data=snapshot.model_dump(mode="json"),
                    error=None,
                )
            except Exception as exc:
                tool_result = ToolResult(
                    tool_name=ENV_PROBE_TOOL_NAME,
                    success=False,
                    data=None,
                    error=str(exc),
                )
        else:
            tool = self.tools.get(tool_name)
            if tool is None:
                tool_result = ToolResult(
                    tool_name=tool_name,
                    success=False,
                    data=None,
                    error=f"tool is not whitelisted: {tool_name}",
                )
            else:
                try:
                    tool_result = tool(self.executor, **args)
                except Exception as exc:
                    tool_result = ToolResult(
                        tool_name=tool_name,
                        success=False,
                        data=None,
                        error=str(exc),
                    )

        return (
            tool_result,
            _execution_step(
                tool_result.tool_name,
                args,
                tool_started,
                tool_result.success,
                tool_result.error,
            ),
        )

    def _continuous_finished_response(
        self,
        *,
        plan: ExecutionPlan,
        environment: dict[str, Any],
        risk: PolicyDecision,
        timeline: list[dict[str, Any]],
        execution_steps: list[dict[str, Any]],
        execution_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        final_status = _continuous_final_status(timeline)
        error = _continuous_error(timeline)
        explanation = self.summarizer.summarize_continuous(
            status=final_status,
            timeline=timeline,
            reason=error,
        )
        return self._continuous_envelope(
            plan=plan,
            environment=environment,
            risk=risk,
            timeline=timeline,
            execution={
                "status": final_status,
                "steps": execution_steps,
                "results": execution_results,
            },
            result={
                "status": final_status,
                "data": {"timeline": timeline},
                "error": error,
            },
            explanation=explanation,
        )

    def _continuous_envelope(
        self,
        *,
        plan: ExecutionPlan,
        environment: dict[str, Any],
        risk: PolicyDecision,
        timeline: list[dict[str, Any]],
        execution: dict[str, Any],
        result: dict[str, Any],
        explanation: str,
    ) -> dict[str, Any]:
        if len(plan.steps) == 1:
            parsed_intent = _parsed_intent_from_plan_step(
                plan.steps[0],
                raw_user_input=plan.raw_user_input,
                step_results={},
            )
        else:
            parsed_intent = ParsedIntent(
                intent="continuous_task",
                raw_user_input=plan.raw_user_input,
                confidence=1.0,
            )
        return self._build_enriched_envelope(
            parsed_intent=parsed_intent,
            environment=environment,
            risk=risk,
            plan_payload=plan.to_dict(),
            execution=execution,
            result=result,
            explanation=explanation,
            timeline=timeline,
        )

    def _envelope(
        self,
        *,
        parsed_intent: ParsedIntent,
        environment: dict[str, Any],
        risk: PolicyDecision,
        plan: ReadonlyPlan,
        execution: dict[str, Any],
        result: dict[str, Any],
        explanation: str,
    ) -> dict[str, Any]:
        return self._build_enriched_envelope(
            parsed_intent=parsed_intent,
            environment=environment,
            risk=risk,
            plan_payload=_plan_payload(plan),
            execution=execution,
            result=result,
            explanation=explanation,
        )

    def _build_enriched_envelope(
        self,
        *,
        parsed_intent: ParsedIntent,
        environment: dict[str, Any],
        risk: PolicyDecision,
        plan_payload: dict[str, Any],
        execution: dict[str, Any],
        result: dict[str, Any],
        explanation: str,
        timeline: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        policy_version = _current_policy_version()
        evidence_chain = _build_evidence_chain(
            parsed_intent=parsed_intent,
            environment=environment,
            risk=risk,
            plan_payload=plan_payload,
            execution=execution,
            result=result,
            timeline=timeline,
        )
        recovery = build_recovery_suggestion(
            parsed_intent=parsed_intent,
            environment=environment,
            risk=risk,
            plan=plan_payload,
            execution=execution,
            result=result,
            timeline=timeline,
        )
        explanation_card = self.summarizer.build_explanation_card(
            parsed_intent=parsed_intent,
            environment=environment,
            risk=risk,
            plan=plan_payload,
            execution=execution,
            result=result,
            evidence_chain=evidence_chain,
            recovery=recovery,
            legacy_explanation=explanation,
            timeline=timeline,
        )
        rendered_explanation = self.summarizer.render_explanation_card(
            explanation_card,
            fallback=explanation,
        )
        blast_radius_preview = build_blast_radius_preview(
            parsed_intent=parsed_intent,
            risk=risk,
            plan=plan_payload,
            execution=execution,
            result=result,
            environment=environment,
        )
        policy_simulator = build_policy_simulator(
            parsed_intent=parsed_intent,
            risk=risk,
            policy_version=policy_version,
        )
        envelope = {
            "intent": parsed_intent.model_dump(mode="json"),
            "environment": environment,
            "risk": risk.model_dump(mode="json"),
            "plan": plan_payload,
            "execution": execution,
            "result": result,
            "recovery": recovery,
            "blast_radius_preview": blast_radius_preview,
            "policy_simulator": policy_simulator,
            "explanation": rendered_explanation,
            "evidence_chain": evidence_chain.model_dump(mode="json"),
            "explanation_card": explanation_card.model_dump(mode="json"),
        }
        if timeline is not None:
            envelope["timeline"] = timeline
        return apply_evo_lite_hook(
            envelope,
            memory=self.memory,
            experience_store=self.experience_store,
            enabled=self.evo_lite_enabled,
        )

    def _build_pending_action(
        self,
        parsed_intent: ParsedIntent,
        risk: PolicyDecision,
    ) -> PendingAction | None:
        pending_action = _pending_action_from_intent(parsed_intent, risk)
        if pending_action is None:
            return None
        return _bind_confirmation_token_to_pending_action(
            pending_action,
            host_id=_probe_confirmation_host_id(self.executor),
            policy_version=_current_policy_version(),
        )

    def _unresolved_context_response(self, parsed_intent: ParsedIntent) -> dict[str, Any]:
        reason = _unresolved_context_reason(parsed_intent)
        risk = PolicyDecision(
            risk_level=RiskLevel.S0,
            allow=False,
            requires_confirmation=False,
            reasons=["unresolved context reference"],
        )
        return self._envelope(
            parsed_intent=parsed_intent,
            environment={
                "status": "not_collected",
                "reason": "unresolved_context_reference",
            },
            risk=risk,
            plan=ReadonlyPlan(status="refused", reason=reason),
            execution={"status": "skipped", "steps": [], "results": []},
            result={"status": "refused", "data": None, "error": reason},
            explanation=f"{reason}，未执行任何命令。",
        )

    def _pending_confirmation_response(
        self,
        parsed_intent: ParsedIntent,
        risk: PolicyDecision,
        pending_action: PendingAction,
    ) -> dict[str, Any]:
        plan = ReadonlyPlan(
            status="pending_confirmation",
            steps=[
                PlannedToolCall(
                    pending_action.tool_name,
                    dict(pending_action.tool_args),
                )
            ],
            reason="write operation requires exact confirmation",
        )
        confirmation_text = pending_action.confirmation_text
        explanation = f"该写操作需要确认。请输入精确确认语：{confirmation_text}"
        return self._envelope(
            parsed_intent=parsed_intent,
            environment={
                "status": "not_collected",
                "reason": "pending_confirmation_before_execution",
            },
            risk=risk,
            plan=plan,
            execution={"status": "skipped", "steps": [], "results": []},
            result={
                "status": "pending_confirmation",
                "data": None,
                "error": None,
                "confirmation_text": confirmation_text,
                "pending_action": pending_action.public_payload(),
            },
            explanation=explanation,
        )

    def _pending_confirmation_mismatch(
        self,
        raw_user_input: str,
        pending_action: PendingAction,
    ) -> dict[str, Any]:
        parsed_intent = _parsed_intent_from_pending(pending_action, raw_user_input)
        risk = PolicyDecision(
            risk_level=pending_action.risk_level,
            allow=False,
            requires_confirmation=True,
            confirmation_text=pending_action.confirmation_text,
            reasons=["pending action requires exact confirmation text"],
        )
        plan = ReadonlyPlan(
            status="pending_confirmation",
            steps=[PlannedToolCall(pending_action.tool_name, dict(pending_action.tool_args))],
            reason="confirmation text mismatch",
        )
        explanation = (
            "确认语不匹配，未执行任何工具。"
            f"如需继续，请输入精确确认语：{pending_action.confirmation_text}"
        )
        return self._envelope(
            parsed_intent=parsed_intent,
            environment={
                "status": "not_collected",
                "reason": "pending_action_waiting_for_confirmation",
            },
            risk=risk,
            plan=plan,
            execution={"status": "skipped", "steps": [], "results": []},
            result={
                "status": "pending_confirmation",
                "data": None,
                "error": "confirmation_text_mismatch",
                "confirmation_text": pending_action.confirmation_text,
                "pending_action": pending_action.public_payload(),
            },
            explanation=explanation,
        )

    def _invalid_confirmation_token_response(
        self,
        *,
        raw_user_input: str,
        pending_action: PendingAction,
        error_code: str,
    ) -> dict[str, Any]:
        self.memory.clear_pending_action()
        parsed_intent = _parsed_intent_from_pending(pending_action, raw_user_input)
        reason = _confirmation_token_error_reason(error_code)
        risk = PolicyDecision(
            risk_level=pending_action.risk_level,
            allow=False,
            requires_confirmation=False,
            confirmation_text=None,
            reasons=[reason],
        )
        explanation = f"{reason}\uff0c\u65e7\u786e\u8ba4\u5df2\u5931\u6548\uff0c\u8bf7\u91cd\u65b0\u53d1\u8d77\u539f\u59cb\u8bf7\u6c42\u3002"
        return self._envelope(
            parsed_intent=parsed_intent,
            environment={
                "status": "not_collected",
                "reason": "invalid_confirmation_token",
            },
            risk=risk,
            plan=ReadonlyPlan(status="refused", reason=reason),
            execution={"status": "skipped", "steps": [], "results": []},
            result={
                "status": "refused",
                "data": {"invalidated_pending_action": pending_action.public_payload()},
                "error": error_code,
            },
            explanation=explanation,
        )

    def _cancel_pending_action(
        self,
        raw_user_input: str,
        pending_action: PendingAction,
    ) -> dict[str, Any]:
        self.memory.clear_pending_action()
        parsed_intent = _parsed_intent_from_pending(pending_action, raw_user_input)
        risk = PolicyDecision(
            risk_level=pending_action.risk_level,
            allow=False,
            requires_confirmation=False,
            confirmation_text=None,
            reasons=["pending action cancelled by user"],
        )
        plan = ReadonlyPlan(status="cancelled", reason="pending action cancelled")
        return self._envelope(
            parsed_intent=parsed_intent,
            environment={
                "status": "not_collected",
                "reason": "pending_action_cancelled",
            },
            risk=risk,
            plan=plan,
            execution={"status": "skipped", "steps": [], "results": []},
            result={
                "status": "cancelled",
                "data": {"cancelled_pending_action": pending_action.public_payload()},
                "error": None,
            },
            explanation="已取消待确认操作，未执行任何工具。",
        )

    def _execute_pending_action(
        self,
        raw_user_input: str,
        pending_action: PendingAction,
    ) -> dict[str, Any]:
        parsed_intent = _parsed_intent_from_pending(pending_action, raw_user_input)
        risk = evaluate_policy(parsed_intent)
        plan = ReadonlyPlan(
            status="confirmed",
            steps=[PlannedToolCall(pending_action.tool_name, dict(pending_action.tool_args))],
            reason="exact confirmation matched",
        )

        if not _requires_pending_confirmation(risk):
            self.memory.clear_pending_action()
            explanation = "确认语已匹配，但策略重新评估拒绝执行，未调用任何工具。"
            return self._envelope(
                parsed_intent=parsed_intent,
                environment={
                    "status": "not_collected",
                    "reason": "policy_refused_after_confirmation",
                },
                risk=risk,
                plan=ReadonlyPlan(status="refused", reason=_policy_refusal_reason(risk)),
                execution={"status": "skipped", "steps": [], "results": []},
                result={
                    "status": "refused",
                    "data": None,
                    "error": _policy_refusal_reason(risk),
                },
                explanation=explanation,
            )

        token_error = _validate_pending_confirmation_token_binding(
            pending_action,
            risk=risk,
            host_id=_probe_confirmation_host_id(self.executor),
            policy_version=_current_policy_version(),
        )
        if token_error is not None:
            return self._invalid_confirmation_token_response(
                raw_user_input=raw_user_input,
                pending_action=pending_action,
                error_code=token_error,
            )

        tool_started = _utc_now()
        tool = self.tools.get(pending_action.tool_name)
        if tool is None:
            tool_result = ToolResult(
                tool_name=pending_action.tool_name,
                success=False,
                data=None,
                error=f"tool is not whitelisted: {pending_action.tool_name}",
            )
        else:
            try:
                tool_result = tool(self.executor, **pending_action.tool_args)
            except Exception as exc:
                tool_result = ToolResult(
                    tool_name=pending_action.tool_name,
                    success=False,
                    data=None,
                    error=str(exc),
                )
        self.memory.clear_pending_action()
        if tool_result.success:
            self.memory.remember_intent(parsed_intent, risk_level=risk.risk_level)

        execution_step = _execution_step(
            pending_action.tool_name,
            dict(pending_action.tool_args),
            tool_started,
            tool_result.success,
            tool_result.error,
        )
        final_status = "success" if tool_result.success else "failed"
        return self._envelope(
            parsed_intent=parsed_intent,
            environment={
                "status": "not_collected",
                "reason": "confirmed_write_execution",
            },
            risk=risk,
            plan=plan,
            execution={
                "status": final_status,
                "steps": [execution_step],
                "results": [tool_result.model_dump(mode="json")],
            },
            result={
                "status": final_status,
                "tool_name": tool_result.tool_name,
                "data": tool_result.data,
                "error": tool_result.error,
            },
            explanation=_write_execution_explanation(pending_action, tool_result),
        )


def run_readonly_request(executor: Any, raw_user_input: str) -> dict[str, Any]:
    return ReadonlyOrchestrator(executor).run(raw_user_input)


def _should_try_continuous_plan(raw_user_input: str) -> bool:
    text = str(raw_user_input or "")
    if "先" in text and _contains_any(text, ["再", "如果", "则", "就"]):
        return True
    if _looks_like_contextual_delete(text):
        return True
    workflow_template = match_workflow_template(text)
    if workflow_template is not None and workflow_template.requires_confirmation:
        return True
    return bool(
        "端口" in text
        and _contains_any(text, ["对应", "进程"])
        and _contains_any(text, ["先", "再", "如果"])
    )


def _looks_like_contextual_delete(raw_user_input: str) -> bool:
    text = str(raw_user_input or "")
    return bool(
        _contains_any(text, ["删除", "删掉", "移除", "remove", "delete"])
        and _contains_any(text, ["刚才那个用户", "上一个用户", "刚刚创建的用户", "刚才创建的用户"])
    )


def _should_prefer_contextual_delete_plan(
    current_plan: ExecutionPlan,
    candidate_plan: ExecutionPlan,
) -> bool:
    if not candidate_plan.supported:
        return False
    candidate_intents = [step.intent for step in candidate_plan.steps]
    if DELETE_USER_INTENT not in candidate_intents or CREATE_USER_INTENT in candidate_intents:
        return False
    if not current_plan.supported:
        return True
    current_intents = [step.intent for step in current_plan.steps]
    return CREATE_USER_INTENT in current_intents or len(candidate_plan.steps) < len(current_plan.steps)


def _strip_delete_sensitivity_phrase(raw_user_input: str) -> str:
    text = str(raw_user_input or "")
    replacements = [
        ("为什么删除比创建更敏感", ""),
        ("删除比创建更敏感", "删除更敏感"),
        ("比创建更敏感", "更敏感"),
    ]
    for source, target in replacements:
        text = text.replace(source, target)
    return text


def _is_continuous_pending(pending_action: PendingAction) -> bool:
    return pending_action.context.get(CONTINUOUS_PENDING_KEY) is True


def _pending_action_from_continuous_step(
    *,
    parsed_intent: ParsedIntent,
    risk: PolicyDecision,
    step: PlanStep,
    plan: ExecutionPlan,
    step_index: int,
    timeline: list[dict[str, Any]],
    execution_steps: list[dict[str, Any]],
    execution_results: list[dict[str, Any]],
    environment: dict[str, Any],
    step_results: dict[str, dict[str, Any]],
    checkpoint: dict[str, Any] | None,
    host_id: str,
    policy_version: str,
) -> PendingAction:
    pending_action = _pending_action_from_intent(parsed_intent, risk)
    if pending_action is None:
        raise ValueError(f"continuous step cannot be confirmed: {step.intent}")

    context = dict(pending_action.context)
    context.update(
        {
            CONTINUOUS_PENDING_KEY: True,
            "plan": plan.to_dict(),
            "pending_step_id": step.step_id,
            "pending_step_index": step_index,
            "timeline": list(timeline),
            "execution_steps": list(execution_steps),
            "execution_results": list(execution_results),
            "environment": dict(environment),
            "step_results": dict(step_results),
        }
    )
    if checkpoint is not None:
        context[CHECKPOINT_KEY] = dict(checkpoint)
    pending_action = pending_action.model_copy(update={"context": context})
    return _bind_confirmation_token_to_pending_action(
        pending_action,
        host_id=host_id,
        policy_version=policy_version,
    )


def _build_step_checkpoint(
    *,
    step: PlanStep,
    step_index: int,
    environment: dict[str, Any],
    executor: Any,
) -> dict[str, Any]:
    facts = _baseline_contract_facts(step, environment=environment, executor=executor)
    tracked_keys = _tracked_contract_keys(step, facts)
    return {
        "step_id": step.step_id,
        "step_index": step_index,
        "write_step": step.write_step,
        "checkpointable": step.checkpointable,
        "resume_from_index": step_index,
        "saved_at": _utc_now(),
        "environment": dict(environment),
        "facts": facts,
        "tracked_keys": tracked_keys,
        "fingerprint": _contract_fingerprint(facts, step.fingerprint_keys),
        "contract": {
            "preconditions": list(step.preconditions),
            "expected_observation": step.expected_observation,
            "postconditions": list(step.postconditions),
            "freshness_keys": list(step.freshness_keys),
            "fingerprint_keys": list(step.fingerprint_keys),
            "checkpointable": step.checkpointable,
            "write_step": step.write_step,
        },
    }


def _baseline_contract_facts(
    step: PlanStep,
    *,
    environment: dict[str, Any],
    executor: Any,
) -> dict[str, Any]:
    tracked_keys = _contract_key_set(step)
    facts = _snapshot_contract_facts(
        _environment_snapshot_payload(environment),
        tracked_keys,
    )
    if "host.hostname" in tracked_keys and "host.hostname" not in facts:
        host_id = _probe_confirmation_host_id(executor)
        if host_id != "unknown":
            facts["host.hostname"] = host_id

    username = _str_or_none(step.target.get("username"))
    if username and _needs_target_user_facts(tracked_keys):
        lookup = _lookup_user(executor, username)
        if lookup.error is None:
            facts["target.user_exists"] = lookup.exists
            facts["target.user_uid"] = lookup.record.uid if lookup.record else None
    return facts


def _revalidate_step_checkpoint(
    *,
    step: PlanStep,
    checkpoint: dict[str, Any],
    environment: dict[str, Any],
    executor: Any,
    env_probe: EnvProbeCallable,
) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
    baseline_facts = dict(checkpoint.get("facts") or {})
    tracked_keys = [key for key in _tracked_contract_keys(step, baseline_facts) if key in baseline_facts]
    current_environment = dict(environment)
    current_facts = dict(_snapshot_contract_facts(_environment_snapshot_payload(environment), tracked_keys))

    env_keys = [key for key in tracked_keys if key.startswith("host.") or key.startswith("env.")]
    if env_keys:
        try:
            snapshot = env_probe(executor)
        except Exception as exc:
            reason = f"unable to revalidate environment before write step: {exc}"
            return current_environment, [_drift_timeline_entry(step, reason)], reason
        current_environment = {
            "status": "ok",
            "snapshot": snapshot.model_dump(mode="json"),
        }
        current_facts.update(_snapshot_contract_facts(current_environment["snapshot"], env_keys))

    username = _str_or_none(step.target.get("username"))
    target_keys = [key for key in tracked_keys if key.startswith("target.")]
    if username and target_keys:
        lookup = _lookup_user(executor, username)
        if lookup.error is not None:
            reason = f"unable to revalidate target state before write step: {lookup.error}"
            return current_environment, [_drift_timeline_entry(step, reason)], reason
        current_facts["target.user_exists"] = lookup.exists
        current_facts["target.user_uid"] = lookup.record.uid if lookup.record else None

    drift_messages = _contract_drift_messages(step, baseline_facts, current_facts)
    precondition_failures = _contract_precondition_failures(
        step,
        facts=current_facts,
        environment=current_environment,
    )
    if precondition_failures:
        drift_messages.extend(precondition_failures)

    if drift_messages:
        reason = "contract drift detected: " + "；".join(drift_messages)
        return current_environment, [_drift_timeline_entry(step, reason)], reason

    compared_keys = [key for key in tracked_keys if key in current_facts]
    summary = (
        f"write-step contract revalidated on {len(compared_keys)} tracked facts."
        if compared_keys
        else "write-step contract resumed from checkpoint without comparable drift facts."
    )
    return current_environment, [_revalidation_timeline_entry(step, summary)], None


def _contract_key_set(step: PlanStep) -> set[str]:
    return {
        str(key)
        for key in [*step.freshness_keys, *step.fingerprint_keys]
        if isinstance(key, str) and key
    }


def _tracked_contract_keys(step: PlanStep, facts: dict[str, Any]) -> list[str]:
    tracked = [
        key
        for key in [*step.freshness_keys, *step.fingerprint_keys]
        if isinstance(key, str) and key and key in facts
    ]
    return list(dict.fromkeys(tracked))


def _environment_snapshot_payload(environment: dict[str, Any]) -> dict[str, Any]:
    snapshot = environment.get("snapshot") if isinstance(environment, dict) else None
    return dict(snapshot) if isinstance(snapshot, dict) else {}


def _snapshot_contract_facts(
    snapshot: dict[str, Any],
    tracked_keys: list[str] | set[str],
) -> dict[str, Any]:
    tracked = set(tracked_keys)
    facts: dict[str, Any] = {}
    if "host.hostname" in tracked and "hostname" in snapshot:
        facts["host.hostname"] = snapshot.get("hostname")
    if "host.connection_mode" in tracked and "connection_mode" in snapshot:
        facts["host.connection_mode"] = snapshot.get("connection_mode")
    if "env.current_user" in tracked and "current_user" in snapshot:
        facts["env.current_user"] = snapshot.get("current_user")
    if "env.is_root" in tracked and "is_root" in snapshot:
        facts["env.is_root"] = bool(snapshot.get("is_root"))
    if "env.sudo_available" in tracked and "sudo_available" in snapshot:
        facts["env.sudo_available"] = bool(snapshot.get("sudo_available"))
    return facts


def _needs_target_user_facts(tracked_keys: list[str] | set[str]) -> bool:
    tracked = set(tracked_keys)
    return "target.user_exists" in tracked or "target.user_uid" in tracked


def _contract_fingerprint(facts: dict[str, Any], keys: list[str]) -> str | None:
    selected = {
        key: facts.get(key)
        for key in keys
        if isinstance(key, str) and key and key in facts
    }
    if not selected:
        return None
    return stable_hash(selected)


def _contract_drift_messages(
    step: PlanStep,
    baseline_facts: dict[str, Any],
    current_facts: dict[str, Any],
) -> list[str]:
    messages: list[str] = []
    for key in step.freshness_keys:
        if key not in baseline_facts or key not in current_facts:
            continue
        if baseline_facts.get(key) != current_facts.get(key):
            messages.append(
                f"{key} changed from {baseline_facts.get(key)!r} to {current_facts.get(key)!r}"
            )

    baseline_fingerprint = _contract_fingerprint(baseline_facts, step.fingerprint_keys)
    current_fingerprint = _contract_fingerprint(current_facts, step.fingerprint_keys)
    if (
        baseline_fingerprint is not None
        and current_fingerprint is not None
        and baseline_fingerprint != current_fingerprint
        and not messages
    ):
        messages.append("fingerprint_keys no longer match the saved checkpoint")
    return messages


def _contract_precondition_failures(
    step: PlanStep,
    *,
    facts: dict[str, Any],
    environment: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    snapshot_available = bool(_environment_snapshot_payload(environment))
    for precondition in step.preconditions:
        if precondition == "env.snapshot_available":
            if snapshot_available:
                continue
            if _precondition_keys_available({"env.current_user", "env.is_root", "env.sudo_available"}, facts):
                continue
            failures.append("environment snapshot is no longer available")
            continue

        if precondition == "env.sudo_available_or_root":
            if not _precondition_keys_available({"env.sudo_available", "env.is_root"}, facts):
                continue
            if bool(facts.get("env.sudo_available")) or bool(facts.get("env.is_root")):
                continue
            failures.append("write privilege precondition changed")
            continue

        if precondition == "target.user_absent":
            if not _precondition_keys_available({"target.user_exists"}, facts):
                continue
            if not bool(facts.get("target.user_exists")):
                continue
            failures.append("target object no longer satisfies user_absent")
            continue

        if precondition == "target.user_exists":
            if not _precondition_keys_available({"target.user_exists"}, facts):
                continue
            if bool(facts.get("target.user_exists")):
                continue
            failures.append("target object no longer satisfies user_exists")
            continue

        if precondition == "target.user_uid >= 1000":
            if not _precondition_keys_available({"target.user_uid"}, facts):
                continue
            uid = _int_or_none(facts.get("target.user_uid"))
            if uid is not None and uid >= MIN_NORMAL_UID:
                continue
            failures.append("target user UID is no longer a normal user")
    return failures


def _precondition_keys_available(required_keys: set[str], facts: dict[str, Any]) -> bool:
    return all(key in facts for key in required_keys)


def _parsed_intent_from_plan_step(
    step: PlanStep,
    *,
    raw_user_input: str,
    step_results: dict[str, dict[str, Any]],
) -> ParsedIntent:
    target = step.target
    if step.intent == ENV_PROBE_INTENT:
        return ParsedIntent(
            intent=ENV_PROBE_INTENT,
            raw_user_input=raw_user_input,
            confidence=1.0,
        )

    if step.intent == PORT_QUERY_INTENT:
        return ParsedIntent(
            intent=PORT_QUERY_INTENT,
            target=IntentTarget(port=_int_or_none(target.get("port"))),
            raw_user_input=raw_user_input,
            confidence=1.0,
        )

    if step.intent == PROCESS_QUERY_INTENT:
        pid = _pid_for_process_step(step, step_results)
        return ParsedIntent(
            intent=PROCESS_QUERY_INTENT,
            target=IntentTarget(
                port=_int_or_none(target.get("port")),
                pid=pid,
            ),
            constraints={
                "mode": "pid" if pid is not None else "cpu",
                "limit": 10,
                "from_step": target.get("from_step"),
            },
            raw_user_input=raw_user_input,
            confidence=1.0,
        )

    if step.intent == CREATE_USER_INTENT:
        return ParsedIntent(
            intent=CREATE_USER_INTENT,
            target=IntentTarget(username=_str_or_none(target.get("username"))),
            constraints={
                "groups": [],
                "create_home": bool(target.get("create_home", True)),
                "no_sudo": True,
            },
            requires_write=True,
            raw_user_input=raw_user_input,
            confidence=1.0,
        )

    if step.intent == DELETE_USER_INTENT:
        return ParsedIntent(
            intent=DELETE_USER_INTENT,
            target=IntentTarget(username=_str_or_none(target.get("username"))),
            constraints={
                "remove_home": bool(target.get("remove_home", False)),
                "resolved_from_memory": bool(target.get("resolved_from_memory", False)),
            },
            context_refs=["刚才那个用户"] if target.get("resolved_from_memory") else [],
            requires_write=True,
            raw_user_input=raw_user_input,
            confidence=1.0,
        )

    return ParsedIntent(
        intent=str(step.intent or "unknown"),
        constraints=dict(target),
        requires_write=False,
        raw_user_input=raw_user_input,
        confidence=0.2,
    )


def _tool_for_plan_step(
    step: PlanStep,
    step_results: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    target = step.target
    if step.intent == ENV_PROBE_INTENT:
        return ENV_PROBE_TOOL_NAME, {}
    if step.intent == PORT_QUERY_INTENT:
        return PORT_QUERY_TOOL_NAME, {"port": _int_or_none(target.get("port"))}
    if step.intent == PROCESS_QUERY_INTENT:
        pid = _pid_for_process_step(step, step_results)
        return PROCESS_QUERY_TOOL_NAME, {
            "mode": "pid" if pid is not None else "cpu",
            "limit": 10,
            "keyword": None,
            "pid": pid,
        }
    if step.intent == CREATE_USER_INTENT:
        return CREATE_USER_TOOL_NAME, {
            "username": target.get("username"),
            "create_home": bool(target.get("create_home", True)),
            "no_sudo": True,
        }
    if step.intent == DELETE_USER_INTENT:
        return DELETE_USER_TOOL_NAME, {
            "username": target.get("username"),
            "remove_home": bool(target.get("remove_home", False)),
        }
    return str(step.intent), dict(target)


def _dependency_abort_reason(
    step: PlanStep,
    timeline: list[dict[str, Any]],
) -> str | None:
    for dependency_id in step.depends_on:
        dependency = _timeline_for_step(timeline, dependency_id)
        if dependency is None:
            return f"依赖步骤 {dependency_id} 没有成功结果，已中止该步骤。"
        if dependency.get("status") != "success":
            return (
                f"依赖步骤 {dependency_id} 状态为 {dependency.get('status')}，"
                "后续依赖步骤不继续执行。"
            )
    return None


def _condition_skip_reason(
    step: PlanStep,
    environment: dict[str, Any],
    step_results: dict[str, dict[str, Any]],
) -> str | None:
    if step.condition == "env.sudo_available or env.is_root":
        snapshot = environment.get("snapshot") or {}
        if not isinstance(snapshot, dict):
            snapshot = {}
        if not bool(snapshot.get("sudo_available")) and not bool(snapshot.get("is_root")):
            username = step.target.get("username") or "目标用户"
            return (
                "环境权限不足：当前不是 root，且无免密 sudo。"
                f"未继续创建普通用户 {username}。"
            )
        return None

    if step.condition == "step_1.listener_found":
        source_step = _str_or_none(step.target.get("from_step")) or "step_1"
        listeners = _listeners_from_step_result(step_results.get(source_step))
        port = step.target.get("port")
        if not listeners:
            return f"端口 {port} 未发现监听，未继续查询对应进程。"
        if _first_listener_pid(listeners) is None:
            return f"端口 {port} 的监听记录没有 PID，未继续查询对应进程。"
        return None

    return None


def _stored_step_result(
    step: PlanStep,
    *,
    success: bool,
    status: str,
    data: Any = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "step_id": step.step_id,
        "intent": step.intent,
        "success": success,
        "status": status,
        "data": data,
        "error": error,
    }


def _timeline_entry(
    *,
    step_id: str,
    intent: str,
    risk: RiskLevel | str,
    status: str,
    result_summary: str,
) -> dict[str, Any]:
    risk_value = risk.value if isinstance(risk, RiskLevel) else str(risk)
    return {
        "step_id": step_id,
        "intent": intent,
        "risk": risk_value,
        "status": status,
        "result_summary": result_summary,
    }


def _checkpoint_timeline_entry(
    step: PlanStep,
    checkpoint: dict[str, Any],
) -> dict[str, Any]:
    tracked_count = len(list(checkpoint.get("tracked_keys") or []))
    return _timeline_entry(
        step_id=f"{step.step_id}_checkpoint",
        intent=CHECKPOINT_SAVED_INTENT,
        risk=_step_timeline_risk(step),
        status="success",
        result_summary=f"safe checkpoint saved with {tracked_count} tracked contract facts.",
    )


def _revalidation_timeline_entry(step: PlanStep, summary: str) -> dict[str, Any]:
    return _timeline_entry(
        step_id=f"{step.step_id}_revalidate",
        intent=CONTRACT_REVALIDATED_INTENT,
        risk=_step_timeline_risk(step),
        status="success",
        result_summary=summary,
    )


def _drift_timeline_entry(step: PlanStep, reason: str) -> dict[str, Any]:
    return _timeline_entry(
        step_id=f"{step.step_id}_drift",
        intent=CONTRACT_DRIFT_INTENT,
        risk=_step_timeline_risk(step),
        status="refused",
        result_summary=reason,
    )


def _step_timeline_risk(step: PlanStep) -> RiskLevel | str:
    raw_risk = step.target.get("risk_level")
    if isinstance(raw_risk, str):
        try:
            return RiskLevel(raw_risk)
        except ValueError:
            return raw_risk
    return RiskLevel.S1 if step.write_step else RiskLevel.S0


def _timeline_entry_from_tool_result(
    step: PlanStep,
    risk: PolicyDecision,
    tool_result: ToolResult,
) -> dict[str, Any]:
    status = "success" if tool_result.success else "failed"
    if tool_result.success:
        summary = _success_summary(step, tool_result)
    else:
        summary = f"{step.intent} 执行失败：{tool_result.error or '未知错误'}。"
    return _timeline_entry(
        step_id=step.step_id,
        intent=step.intent,
        risk=risk.risk_level,
        status=status,
        result_summary=summary,
    )


def _verification_timeline_entry(
    step: PlanStep,
    risk: PolicyDecision,
    tool_result: ToolResult,
) -> dict[str, Any] | None:
    username = step.target.get("username")
    if not tool_result.success or not username:
        return None
    if step.intent == CREATE_USER_INTENT:
        verified = bool((tool_result.data or {}).get("verified", True))
        return _timeline_entry(
            step_id=f"{step.step_id}_verify",
            intent=VERIFY_USER_EXISTS_INTENT,
            risk=RiskLevel.S0,
            status="success" if verified else "failed",
            result_summary=(
                f"创建后验证：普通用户 {username} 已存在。"
                if verified
                else f"创建后验证失败：普通用户 {username} 未确认存在。"
            ),
        )
    if step.intent == DELETE_USER_INTENT:
        verified_absent = bool((tool_result.data or {}).get("verified_absent", True))
        return _timeline_entry(
            step_id=f"{step.step_id}_verify",
            intent=VERIFY_USER_ABSENT_INTENT,
            risk=RiskLevel.S0,
            status="success" if verified_absent else "failed",
            result_summary=(
                f"删除后验证：普通用户 {username} 已不存在。"
                if verified_absent
                else f"删除后验证失败：普通用户 {username} 仍可能存在。"
            ),
        )
    return None


def _append_aborted_remaining_steps(
    remaining_steps: list[PlanStep],
    timeline: list[dict[str, Any]],
    *,
    failed_step_id: str,
    reason: str,
) -> None:
    for step in remaining_steps:
        timeline.append(
            _timeline_entry(
                step_id=step.step_id,
                intent=step.intent,
                risk=RiskLevel.S0,
                status="aborted",
                result_summary=(
                    f"前置步骤 {failed_step_id} 未成功：{reason}。"
                    "后续依赖步骤不继续执行，写操作不会盲目继续。"
                ),
            )
        )


def _pending_step_summary(
    step: PlanStep,
    risk: PolicyDecision,
    confirmation_text: str,
) -> str:
    username = step.target.get("username") or "目标用户"
    if step.intent == DELETE_USER_INTENT:
        return (
            f"删除比创建更敏感：会影响账号访问、文件归属和可恢复性。"
            f"删除普通用户 {username} 为 {risk.risk_level.value}，等待强确认：{confirmation_text}"
        )
    if step.intent == CREATE_USER_INTENT:
        return (
            f"创建普通用户 {username} 为 {risk.risk_level.value}，"
            f"等待确认：{confirmation_text}"
        )
    return f"{step.intent} 为 {risk.risk_level.value}，等待确认：{confirmation_text}"


def _success_summary(step: PlanStep, tool_result: ToolResult) -> str:
    data = tool_result.data or {}
    if step.intent == ENV_PROBE_INTENT:
        user = data.get("current_user", "unknown")
        is_root = data.get("is_root", False)
        sudo_available = data.get("sudo_available", False)
        return f"环境探测成功：当前用户 {user}，root={is_root}，sudo_available={sudo_available}。"
    if step.intent == PORT_QUERY_INTENT:
        port = data.get("port", step.target.get("port"))
        listeners = list(data.get("listeners") or [])
        if not listeners:
            return f"端口 {port} 当前没有监听。"
        pid = _first_listener_pid(listeners)
        return f"端口 {port} 当前存在监听，PID {pid or '未知'}。"
    if step.intent == PROCESS_QUERY_INTENT:
        processes = list(data.get("processes") or [])
        if not processes:
            return "已查询对应进程，没有返回匹配进程。"
        first = processes[0]
        return (
            f"已查询对应进程：PID {first.get('pid') or data.get('pid') or '未知'}，"
            f"进程 {first.get('command') or first.get('process_name') or '未知'}。"
        )
    if step.intent == CREATE_USER_INTENT:
        username = data.get("username") or step.target.get("username")
        return f"创建普通用户 {username} 成功。"
    if step.intent == DELETE_USER_INTENT:
        username = data.get("username") or step.target.get("username")
        return f"删除普通用户 {username} 成功。"
    return f"{step.intent} 执行成功。"


def _continuous_risk_from_timeline(timeline: list[dict[str, Any]]) -> PolicyDecision:
    return PolicyDecision(
        risk_level=_max_risk_from_timeline(timeline),
        allow=True,
        requires_confirmation=False,
        reasons=["continuous task"],
    )


def _continuous_final_status(timeline: list[dict[str, Any]]) -> str:
    statuses = [str(item.get("status")) for item in timeline]
    if "refused" in statuses:
        return "refused"
    if "failed" in statuses:
        return "failed"
    if "aborted" in statuses:
        return "aborted"
    if statuses and all(status == "skipped" for status in statuses):
        return "skipped"
    return "success"


def _continuous_error(timeline: list[dict[str, Any]]) -> str | None:
    for status in ("refused", "failed", "aborted"):
        for item in timeline:
            if item.get("status") == status:
                summary = item.get("result_summary")
                return summary if isinstance(summary, str) else status
    return None


def _max_risk_from_timeline(timeline: list[dict[str, Any]]) -> RiskLevel:
    order = {RiskLevel.S0: 0, RiskLevel.S1: 1, RiskLevel.S2: 2, RiskLevel.S3: 3}
    max_risk = RiskLevel.S0
    for item in timeline:
        try:
            risk = RiskLevel(str(item.get("risk") or RiskLevel.S0.value))
        except ValueError:
            risk = RiskLevel.S0
        if order[risk] > order[max_risk]:
            max_risk = risk
    return max_risk


def _timeline_for_step(
    timeline: list[dict[str, Any]],
    step_id: str,
) -> dict[str, Any] | None:
    for item in reversed(timeline):
        if item.get("step_id") == step_id:
            return item
    return None


def _pid_for_process_step(
    step: PlanStep,
    step_results: dict[str, dict[str, Any]],
) -> int | None:
    pid = _int_or_none(step.target.get("pid"))
    if pid is not None:
        return pid
    source_step = _str_or_none(step.target.get("from_step")) or "step_1"
    return _first_listener_pid(_listeners_from_step_result(step_results.get(source_step)))


def _listeners_from_step_result(step_result: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(step_result, dict):
        return []
    data = step_result.get("data")
    if not isinstance(data, dict):
        return []
    listeners = data.get("listeners") or []
    if not isinstance(listeners, list):
        return []
    return [item for item in listeners if isinstance(item, dict)]


def _first_listener_pid(listeners: list[dict[str, Any]]) -> int | None:
    for listener in listeners:
        pid = _int_or_none(listener.get("pid"))
        if pid is not None:
            return pid
    return None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _policy_refusal_reason(risk: PolicyDecision) -> str:
    if risk.reasons:
        return "；".join(risk.reasons)
    return "policy denied this request"


def _risk_decision(plan: ReadonlyPlan) -> PolicyDecision:
    if plan.ready:
        return PolicyDecision(
            risk_level=RiskLevel.S0,
            allow=True,
            requires_confirmation=False,
            reasons=["read-only request"],
        )
    return PolicyDecision(
        risk_level=RiskLevel.S0,
        allow=False,
        requires_confirmation=False,
        reasons=[plan.reason or "unsupported read-only request"],
    )


def _has_unresolved_context_ref(parsed_intent: ParsedIntent) -> bool:
    return "unresolved_context_ref" in parsed_intent.constraints


def _unresolved_context_reason(parsed_intent: ParsedIntent) -> str:
    reason = parsed_intent.constraints.get("unsupported_reason")
    if isinstance(reason, str) and reason:
        return reason
    ref_text = parsed_intent.constraints.get("context_ref_text")
    if isinstance(ref_text, str) and ref_text:
        return f"无法解析该引用：{ref_text}"
    return "无法解析该引用"


def _plan_payload(plan: ReadonlyPlan) -> dict[str, Any]:
    payload = plan.to_dict()
    if plan.ready:
        payload["steps"] = [{"tool_name": "env_probe_tool", "args": {}}] + payload["steps"]
    return payload


def _requires_pending_confirmation(risk: PolicyDecision) -> bool:
    return bool(
        risk.allow
        and risk.requires_confirmation
        and risk.risk_level in {RiskLevel.S1, RiskLevel.S2}
    )


def _policy_file_paths() -> list[Path]:
    return [path for path in sorted(POLICY_DIR.glob("*.py")) if path.is_file()]


def _current_policy_version() -> str:
    return stable_file_content_hash(_policy_file_paths())


def _probe_confirmation_host_id(executor: Any) -> str:
    snapshot = env_probe_tool(executor)
    hostname = getattr(snapshot, "hostname", None)
    if isinstance(hostname, str) and hostname.strip():
        return hostname.strip()
    return "unknown"


def _pending_confirmation_plan_payload(pending_action: PendingAction) -> dict[str, Any]:
    if _is_continuous_pending(pending_action):
        return {
            "mode": "continuous",
            "plan": dict(pending_action.context.get("plan") or {}),
            "pending_step_id": pending_action.context.get("pending_step_id"),
            "pending_step_index": pending_action.context.get("pending_step_index"),
            "tool_name": pending_action.tool_name,
            "tool_args": dict(pending_action.tool_args),
            "checkpoint": dict(pending_action.context.get(CHECKPOINT_KEY) or {}),
        }
    return {
        "mode": "single_step",
        "intent": pending_action.intent,
        "tool_name": pending_action.tool_name,
        "tool_args": dict(pending_action.tool_args),
        "constraints": dict(pending_action.context.get("constraints") or {}),
    }


def _bind_confirmation_token_to_pending_action(
    pending_action: PendingAction,
    *,
    host_id: str,
    policy_version: str,
) -> PendingAction:
    token = issue_confirmation_token(
        plan_payload=_pending_confirmation_plan_payload(pending_action),
        host_id=host_id,
        target=dict(pending_action.target),
        risk_level=pending_action.risk_level,
        policy_version=policy_version,
        issued_at=pending_action.created_at,
    )
    return pending_action.model_copy(update={"confirmation_token": token})


def _validate_pending_confirmation_token_binding(
    pending_action: PendingAction,
    *,
    risk: PolicyDecision,
    host_id: str,
    policy_version: str,
) -> str | None:
    return validate_confirmation_token(
        pending_action.confirmation_token,
        plan_payload=_pending_confirmation_plan_payload(pending_action),
        host_id=host_id,
        target=dict(pending_action.target),
        risk_level=risk.risk_level,
        policy_version=policy_version,
    )


def _confirmation_token_error_reason(error_code: str) -> str:
    if error_code == "missing_confirmation_token":
        return "confirmation token is missing"
    if error_code == "confirmation_token_expired":
        return "confirmation token expired"
    if error_code == "confirmation_token_host_mismatch":
        return "confirmation token host binding no longer matches the current host"
    if error_code == "confirmation_token_target_mismatch":
        return "confirmation token target binding no longer matches the current target set"
    if error_code == "confirmation_token_risk_mismatch":
        return "confirmation token risk binding no longer matches the current risk level"
    if error_code == "confirmation_token_policy_mismatch":
        return "confirmation token policy binding no longer matches the current policy version"
    if error_code == "confirmation_token_plan_mismatch":
        return "confirmation token plan binding no longer matches the current execution plan"
    return "confirmation token validation failed"


def _pending_action_from_intent(
    parsed_intent: ParsedIntent,
    risk: PolicyDecision,
) -> PendingAction | None:
    tool_name, tool_args = _tool_for_confirmable_intent(parsed_intent)
    if tool_name is None:
        return None

    target = parsed_intent.target.model_dump(mode="json", exclude_none=True)
    confirmation_text = confirmation_text_for(parsed_intent.intent, target)
    if confirmation_text is None:
        return None

    return PendingAction(
        intent=parsed_intent.intent,
        target=target,
        risk_level=risk.risk_level,
        confirmation_text=confirmation_text,
        tool_name=tool_name,
        tool_args=tool_args,
        raw_user_input=parsed_intent.raw_user_input,
        context={"constraints": dict(parsed_intent.constraints)},
    )


def _tool_for_confirmable_intent(
    parsed_intent: ParsedIntent,
) -> tuple[str | None, dict[str, Any]]:
    username = parsed_intent.target.username
    if parsed_intent.intent == CREATE_USER_INTENT:
        return CREATE_USER_TOOL_NAME, {
            "username": username,
            "create_home": bool(parsed_intent.constraints.get("create_home", True)),
            "no_sudo": True,
        }
    if parsed_intent.intent == DELETE_USER_INTENT:
        return DELETE_USER_TOOL_NAME, {
            "username": username,
            "remove_home": bool(parsed_intent.constraints.get("remove_home", False)),
        }
    return None, {}


def _parsed_intent_from_pending(
    pending_action: PendingAction,
    raw_user_input: str,
) -> ParsedIntent:
    target = pending_action.target
    username = target.get("username")
    constraints = pending_action.context.get("constraints") or {}
    if not isinstance(constraints, dict):
        constraints = {}
    return ParsedIntent(
        intent=pending_action.intent,
        target=IntentTarget(username=username if isinstance(username, str) else None),
        constraints=dict(constraints),
        requires_write=True,
        raw_user_input=raw_user_input,
        confidence=1.0,
    )


def _parse_confirmable_user_request(raw_user_input: str) -> ParsedIntent | None:
    text = str(raw_user_input or "").strip()
    if "普通用户" not in text:
        return None
    if _contains_any(text, ["刚才那个用户", "上一个用户", "刚刚创建的用户"]):
        return None

    username = _extract_username_after_normal_user(text)
    constraints: dict[str, Any] = {}
    if _requests_privilege_in_user_text(text):
        constraints["groups"] = ["sudo"]

    if _contains_any(text, ["创建", "新增", "添加"]):
        constraints.setdefault("groups", [])
        constraints["create_home"] = True
        return ParsedIntent(
            intent=CREATE_USER_INTENT,
            target=IntentTarget(username=username),
            constraints=constraints,
            requires_write=True,
            raw_user_input=raw_user_input,
            confidence=0.9,
        )

    if _contains_any(text, ["删除", "删掉", "移除", "remove", "delete"]):
        constraints["remove_home"] = False
        return ParsedIntent(
            intent=DELETE_USER_INTENT,
            target=IntentTarget(username=username),
            constraints=constraints,
            requires_write=True,
            raw_user_input=raw_user_input,
            confidence=0.9,
        )

    return None


def _extract_username_after_normal_user(text: str) -> str | None:
    match = re.search(r"普通用户\s*([^\s，,。、]+)", text, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip()


def _requests_privilege_in_user_text(text: str) -> bool:
    lower_text = text.lower()
    return bool(
        re.search(r"\b(?:admin|administrator|wheel)\b", lower_text)
        or _contains_any(text, ["管理员权限", "root 权限", "root权限", "加入 sudo", "加到 sudo", "sudo 权限"])
    )


def _contains_any(text: str, needles: list[str]) -> bool:
    lower_text = text.lower()
    return any(needle.lower() in lower_text for needle in needles)


def _write_execution_explanation(
    pending_action: PendingAction,
    tool_result: ToolResult,
) -> str:
    username = pending_action.target.get("username") or "目标用户"
    action_text = "创建普通用户" if pending_action.intent == CREATE_USER_INTENT else "删除普通用户"
    if tool_result.success:
        return f"确认语匹配，已执行{action_text} {username}。"
    return f"确认语匹配，但{action_text} {username} 执行失败：{tool_result.error or '未知错误'}。"


def _execution_step(
    tool_name: str,
    args: dict[str, Any],
    started_at: str,
    success: bool,
    error: str | None,
) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "args": dict(args),
        "started_at": started_at,
        "finished_at": _utc_now(),
        "success": success,
        "error": error,
    }


def _build_evidence_chain(
    *,
    parsed_intent: ParsedIntent,
    environment: dict[str, Any],
    risk: PolicyDecision,
    plan_payload: dict[str, Any],
    execution: dict[str, Any],
    result: dict[str, Any],
    timeline: list[dict[str, Any]] | None = None,
) -> EvidenceChain:
    del environment

    timeline = [item for item in (timeline or []) if isinstance(item, dict)]
    builder = EvidenceBuilder()

    parse_event = builder.add_event(
        stage=EvidenceStage.PARSE,
        title="intent_parsed",
        details={
            "raw_user_input": parsed_intent.raw_user_input,
            "intent": parsed_intent.intent,
            "target": parsed_intent.target.model_dump(mode="json"),
            "constraints": dict(parsed_intent.constraints),
            "context_refs": list(parsed_intent.context_refs),
            "requires_write": parsed_intent.requires_write,
            "confidence": parsed_intent.confidence,
        },
        refs=[f"intent:{parsed_intent.intent}"],
    )
    plan_event = builder.add_event(
        stage=EvidenceStage.PLAN,
        title="plan_evaluated",
        details={
            "status": plan_payload.get("status"),
            "reason": plan_payload.get("reason"),
            "steps": _evidence_step_preview(plan_payload),
        },
        severity=_plan_event_severity(plan_payload),
        refs=[parse_event.event_id],
    )
    policy_event = builder.add_event(
        stage=EvidenceStage.POLICY,
        title="policy_decision",
        details={
            "risk_level": risk.risk_level.value,
            "allow": risk.allow,
            "requires_confirmation": risk.requires_confirmation,
            "confirmation_text": risk.confirmation_text,
            "reasons": list(risk.reasons),
            "safe_alternative": risk.safe_alternative,
        },
        severity=_policy_event_severity(risk),
        refs=[parse_event.event_id, plan_event.event_id],
    )

    confirmation_status = _evidence_confirmation_status(
        risk=risk,
        plan_payload=plan_payload,
        execution=execution,
        result=result,
        timeline=timeline,
    )
    confirmation_event = builder.add_event(
        stage=EvidenceStage.CONFIRMATION,
        title=f"confirmation_{confirmation_status}",
        details={
            "status": confirmation_status,
            "requires_confirmation": risk.requires_confirmation,
            "confirmation_text": result.get("confirmation_text") or risk.confirmation_text,
            "plan_status": plan_payload.get("status"),
            "result_status": result.get("status"),
        },
        severity=_confirmation_event_severity(confirmation_status),
        refs=[policy_event.event_id, plan_event.event_id],
    )

    tool_events = _add_tool_call_events(builder, execution)
    post_check_events = _add_post_check_events(builder, execution, timeline)
    recovery_events = _add_recovery_events(
        builder,
        confirmation_status=confirmation_status,
        plan_payload=plan_payload,
        result=result,
        timeline=timeline,
        confirmation_event=confirmation_event,
    )
    result_event = builder.add_event(
        stage=EvidenceStage.RESULT,
        title="final_result",
        details={
            "status": result.get("status"),
            "error": result.get("error"),
            "tool_name": result.get("tool_name"),
        },
        severity=_result_event_severity(result),
        refs=[
            policy_event.event_id,
            *[event.event_id for event in tool_events[-1:]],
            *[event.event_id for event in post_check_events[-1:]],
            *[event.event_id for event in recovery_events[-1:]],
        ],
    )

    if risk.risk_level == RiskLevel.S3 or confirmation_status == "mismatch":
        passed = not tool_events
        builder.add_assertion(
            name="blocked_request_tool_suppression",
            passed=passed,
            evidence_refs=[
                policy_event.event_id,
                confirmation_event.event_id,
                *[event.event_id for event in tool_events],
            ],
            summary=(
                "S3/confirmation mismatch 下未执行任何工具。"
                if passed
                else "S3/confirmation mismatch 下仍发生了工具执行，需要回归检查。"
            ),
        )

    builder.add_assertion(
        name="confirmation_state",
        passed=confirmation_status in {"not_required", "confirmed"},
        evidence_refs=[confirmation_event.event_id],
        summary=_confirmation_assertion_summary(confirmation_status),
    )

    if post_check_events:
        post_check_passed = all(bool(event.details.get("passed")) for event in post_check_events)
        builder.add_assertion(
            name="post_check_state",
            passed=post_check_passed,
            evidence_refs=[event.event_id for event in post_check_events],
            summary="后置校验通过。" if post_check_passed else "后置校验失败。",
        )

    builder.add_assertion(
        name="final_outcome",
        passed=str(result.get("status") or "").lower() == "success",
        evidence_refs=[
            result_event.event_id,
            *[event.event_id for event in post_check_events],
        ],
        summary=_final_outcome_assertion_summary(result),
    )
    return builder.build()


def _evidence_step_preview(plan_payload: dict[str, Any]) -> list[dict[str, Any]]:
    preview: list[dict[str, Any]] = []
    for index, step in enumerate(plan_payload.get("steps") or [], start=1):
        if not isinstance(step, dict):
            continue
        preview.append(
            {
                "order": index,
                "label": step.get("tool_name") or step.get("intent") or step.get("step_id"),
                "args": dict(step.get("args") or {}),
                "target": dict(step.get("target") or {}),
            }
        )
    return preview


def _add_tool_call_events(
    builder: EvidenceBuilder,
    execution: dict[str, Any],
) -> list[Any]:
    events = []
    steps = [item for item in execution.get("steps") or [] if isinstance(item, dict)]
    results = [item for item in execution.get("results") or [] if isinstance(item, dict)]
    for index, result in enumerate(results, start=1):
        step = steps[index - 1] if index - 1 < len(steps) else {}
        tool_name = str(result.get("tool_name") or step.get("tool_name") or "unknown")
        refs = [f"tool:{tool_name}"]
        step_id = step.get("step_id")
        if isinstance(step_id, str) and step_id:
            refs.append(f"step:{step_id}")
        events.append(
            builder.add_event(
                stage=EvidenceStage.TOOL_CALL,
                title=tool_name,
                details={
                    "order": index,
                    "tool_name": tool_name,
                    "args": dict(step.get("args") or {}),
                    "success": result.get("success"),
                    "error": result.get("error"),
                    "started_at": step.get("started_at"),
                    "finished_at": step.get("finished_at"),
                },
                severity=EvidenceSeverity.INFO if result.get("success") else EvidenceSeverity.WARNING,
                refs=refs,
            )
        )
    return events


def _add_post_check_events(
    builder: EvidenceBuilder,
    execution: dict[str, Any],
    timeline: list[dict[str, Any]],
) -> list[Any]:
    events = []
    for item in timeline:
        if not _is_post_check_timeline_entry(item):
            continue
        passed = str(item.get("status") or "").lower() == "success"
        refs = []
        step_id = item.get("step_id")
        if isinstance(step_id, str) and step_id:
            refs.append(f"step:{step_id}")
        events.append(
            builder.add_event(
                stage=EvidenceStage.POST_CHECK,
                title=str(item.get("intent") or step_id or "post_check"),
                details={
                    "source": "timeline",
                    "step_id": step_id,
                    "intent": item.get("intent"),
                    "status": item.get("status"),
                    "result_summary": item.get("result_summary"),
                    "passed": passed,
                },
                severity=EvidenceSeverity.INFO if passed else EvidenceSeverity.CRITICAL,
                refs=refs,
            )
        )

    if events:
        return events

    results = [item for item in execution.get("results") or [] if isinstance(item, dict)]
    for index, result in enumerate(results, start=1):
        data = result.get("data")
        if not isinstance(data, dict):
            continue
        for key in ("verified", "verified_absent"):
            if key not in data:
                continue
            passed = bool(data[key])
            events.append(
                builder.add_event(
                    stage=EvidenceStage.POST_CHECK,
                    title=f"{result.get('tool_name') or 'tool'}_{key}",
                    details={
                        "source": "tool_result",
                        "order": index,
                        "tool_name": result.get("tool_name"),
                        "key": key,
                        "value": data[key],
                        "passed": passed,
                    },
                    severity=EvidenceSeverity.INFO if passed else EvidenceSeverity.CRITICAL,
                    refs=[f"tool:{result.get('tool_name') or 'unknown'}"],
                )
            )
    return events


def _add_recovery_events(
    builder: EvidenceBuilder,
    *,
    confirmation_status: str,
    plan_payload: dict[str, Any],
    result: dict[str, Any],
    timeline: list[dict[str, Any]],
    confirmation_event: Any,
) -> list[Any]:
    events = []
    if confirmation_status == "mismatch":
        events.append(
            builder.add_event(
                stage=EvidenceStage.RECOVERY,
                title="confirmation_mismatch",
                details={
                    "status": result.get("status"),
                    "error": result.get("error"),
                },
                severity=EvidenceSeverity.WARNING,
                refs=[confirmation_event.event_id],
            )
        )
    if str(result.get("status") or plan_payload.get("status") or "").lower() == "cancelled":
        events.append(
            builder.add_event(
                stage=EvidenceStage.RECOVERY,
                title="pending_action_cancelled",
                details={"status": result.get("status") or plan_payload.get("status")},
                severity=EvidenceSeverity.WARNING,
                refs=[confirmation_event.event_id],
            )
        )
    for item in timeline:
        intent = str(item.get("intent") or "")
        refs: list[str] = [confirmation_event.event_id]
        step_id = item.get("step_id")
        if isinstance(step_id, str) and step_id:
            refs.append(f"step:{step_id}")

        if intent == CHECKPOINT_SAVED_INTENT:
            events.append(
                builder.add_event(
                    stage=EvidenceStage.RECOVERY,
                    title="checkpoint_saved",
                    details={
                        "step_id": step_id,
                        "result_summary": item.get("result_summary"),
                    },
                    severity=EvidenceSeverity.INFO,
                    refs=refs,
                )
            )
            continue

        if intent == CONTRACT_REVALIDATED_INTENT:
            events.append(
                builder.add_event(
                    stage=EvidenceStage.RECOVERY,
                    title="contract_revalidated",
                    details={
                        "step_id": step_id,
                        "result_summary": item.get("result_summary"),
                    },
                    severity=EvidenceSeverity.INFO,
                    refs=refs,
                )
            )
            continue

        if intent == CONTRACT_DRIFT_INTENT:
            events.append(
                builder.add_event(
                    stage=EvidenceStage.RECOVERY,
                    title="contract_drift",
                    details={
                        "step_id": step_id,
                        "result_summary": item.get("result_summary"),
                    },
                    severity=EvidenceSeverity.WARNING,
                    refs=refs,
                )
            )
            continue

        if str(item.get("status") or "").lower() != "aborted":
            continue
        events.append(
            builder.add_event(
                stage=EvidenceStage.RECOVERY,
                title="dependency_abort",
                details={
                    "step_id": step_id,
                    "intent": item.get("intent"),
                    "result_summary": item.get("result_summary"),
                },
                severity=EvidenceSeverity.WARNING,
                refs=refs,
            )
        )
    return events


def _confirmation_assertion_summary(confirmation_status: str) -> str:
    if confirmation_status == "confirmed":
        return "确认已满足。"
    if confirmation_status == "pending":
        return "确认仍待满足。"
    if confirmation_status == "mismatch":
        return "确认语不匹配。"
    if confirmation_status == "cancelled":
        return "确认流程已取消。"
    return "当前请求无需确认。"


def _final_outcome_assertion_summary(result: dict[str, Any]) -> str:
    status = str(result.get("status") or "unknown")
    if status == "success":
        return "最终结果为 success。"
    if status == "pending_confirmation":
        return "最终结果为 pending_confirmation。"
    if status == "refused":
        return "最终结果为 refused。"
    if status == "failed":
        return "最终结果为 failed。"
    if status == "cancelled":
        return "最终结果为 cancelled。"
    if status == "aborted":
        return "最终结果为 aborted。"
    return f"最终结果为 {status}。"


def _evidence_confirmation_status(
    *,
    risk: PolicyDecision,
    plan_payload: dict[str, Any],
    execution: dict[str, Any],
    result: dict[str, Any],
    timeline: list[dict[str, Any]],
) -> str:
    plan_status = str(plan_payload.get("status") or "").lower()
    result_status = str(result.get("status") or "").lower()
    result_error = str(result.get("error") or "").lower()
    execution_results = execution.get("results") or []

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
    if risk.requires_confirmation and execution_results:
        return "confirmed"
    return "not_required"


def _plan_event_severity(plan_payload: dict[str, Any]) -> EvidenceSeverity:
    status = str(plan_payload.get("status") or "").lower()
    if status in {"refused", "unsupported"}:
        return EvidenceSeverity.WARNING
    if status in {"pending_confirmation", "cancelled"}:
        return EvidenceSeverity.WARNING
    return EvidenceSeverity.INFO


def _policy_event_severity(risk: PolicyDecision) -> EvidenceSeverity:
    if risk.risk_level == RiskLevel.S3:
        return EvidenceSeverity.CRITICAL
    if risk.risk_level in {RiskLevel.S1, RiskLevel.S2}:
        return EvidenceSeverity.WARNING
    return EvidenceSeverity.INFO


def _confirmation_event_severity(confirmation_status: str) -> EvidenceSeverity:
    if confirmation_status in {"pending", "mismatch", "cancelled"}:
        return EvidenceSeverity.WARNING
    return EvidenceSeverity.INFO


def _result_event_severity(result: dict[str, Any]) -> EvidenceSeverity:
    status = str(result.get("status") or "").lower()
    if status in {"failed", "refused"}:
        return EvidenceSeverity.CRITICAL
    if status in {"pending_confirmation", "cancelled", "aborted", "skipped"}:
        return EvidenceSeverity.WARNING
    return EvidenceSeverity.INFO


def _is_post_check_timeline_entry(item: dict[str, Any]) -> bool:
    intent = str(item.get("intent") or "")
    step_id = str(item.get("step_id") or "")
    return intent.startswith("verify_") or step_id.endswith("_verify")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
