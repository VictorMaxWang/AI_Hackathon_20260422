from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.agent.confirmation import (
    PendingAction,
    confirmation_text_for,
    is_cancel_pending_text,
)
from app.agent.memory import AgentMemory
from app.agent.parser import ReadonlyParser
from app.agent.planner import PlannedToolCall, ReadonlyPlan, ReadonlyPlanner
from app.agent.summarizer import ReadonlySummarizer
from app.models import (
    EnvironmentSnapshot,
    IntentTarget,
    ParsedIntent,
    PolicyDecision,
    RiskLevel,
    ToolResult,
)
from app.policy import evaluate as evaluate_policy
from app.tools.disk import disk_usage_tool
from app.tools.env_probe import env_probe_tool
from app.tools.file_search import file_search_tool
from app.tools.port import port_query_tool
from app.tools.process import process_query_tool
from app.tools.user import create_user_tool, delete_user_tool


ToolCallable = Callable[..., ToolResult]
EnvProbeCallable = Callable[[Any], EnvironmentSnapshot]

CREATE_USER_INTENT = "create_user"
DELETE_USER_INTENT = "delete_user"
CREATE_USER_TOOL_NAME = "create_user_tool"
DELETE_USER_TOOL_NAME = "delete_user_tool"


class ReadonlyOrchestrator:
    """Phase 1 read-only natural language closed loop."""

    def __init__(
        self,
        executor: Any,
        *,
        parser: ReadonlyParser | None = None,
        planner: ReadonlyPlanner | None = None,
        summarizer: ReadonlySummarizer | None = None,
        memory: AgentMemory | None = None,
        env_probe: EnvProbeCallable = env_probe_tool,
        disk_tool: ToolCallable = disk_usage_tool,
        file_search_tool_fn: ToolCallable = file_search_tool,
        process_query_tool_fn: ToolCallable = process_query_tool,
        port_query_tool_fn: ToolCallable = port_query_tool,
        create_user_tool_fn: ToolCallable = create_user_tool,
        delete_user_tool_fn: ToolCallable = delete_user_tool,
    ) -> None:
        self.executor = executor
        self.parser = parser or ReadonlyParser()
        self.planner = planner or ReadonlyPlanner()
        self.summarizer = summarizer or ReadonlySummarizer()
        self.memory = memory or AgentMemory()
        self.env_probe = env_probe
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
                return self._execute_pending_action(raw_user_input, pending_action)
            return self._pending_confirmation_mismatch(raw_user_input, pending_action)

        parsed_intent = _parse_confirmable_user_request(raw_user_input) or self.parser.parse(
            raw_user_input
        )
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
            pending_action = _pending_action_from_intent(parsed_intent, risk)
            if pending_action is not None:
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
        return {
            "intent": parsed_intent.model_dump(mode="json"),
            "environment": environment,
            "risk": risk.model_dump(mode="json"),
            "plan": _plan_payload(plan),
            "execution": execution,
            "result": result,
            "explanation": explanation,
        }

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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
