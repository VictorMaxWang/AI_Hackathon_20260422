from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.agent.parser import ReadonlyParser
from app.agent.planner import ReadonlyPlan, ReadonlyPlanner
from app.agent.summarizer import ReadonlySummarizer
from app.models import EnvironmentSnapshot, ParsedIntent, PolicyDecision, RiskLevel, ToolResult
from app.tools.disk import disk_usage_tool
from app.tools.env_probe import env_probe_tool
from app.tools.file_search import file_search_tool
from app.tools.port import port_query_tool
from app.tools.process import process_query_tool


ToolCallable = Callable[..., ToolResult]
EnvProbeCallable = Callable[[Any], EnvironmentSnapshot]


class ReadonlyOrchestrator:
    """Phase 1 read-only natural language closed loop."""

    def __init__(
        self,
        executor: Any,
        *,
        parser: ReadonlyParser | None = None,
        planner: ReadonlyPlanner | None = None,
        summarizer: ReadonlySummarizer | None = None,
        env_probe: EnvProbeCallable = env_probe_tool,
        disk_tool: ToolCallable = disk_usage_tool,
        file_search_tool_fn: ToolCallable = file_search_tool,
        process_query_tool_fn: ToolCallable = process_query_tool,
        port_query_tool_fn: ToolCallable = port_query_tool,
    ) -> None:
        self.executor = executor
        self.parser = parser or ReadonlyParser()
        self.planner = planner or ReadonlyPlanner()
        self.summarizer = summarizer or ReadonlySummarizer()
        self.env_probe = env_probe
        self.tools: dict[str, ToolCallable] = {
            "disk_usage_tool": disk_tool,
            "file_search_tool": file_search_tool_fn,
            "process_query_tool": process_query_tool_fn,
            "port_query_tool": port_query_tool_fn,
        }

    def run(self, raw_user_input: str) -> dict[str, Any]:
        parsed_intent = self.parser.parse(raw_user_input)
        plan = self.planner.plan(parsed_intent)
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


def run_readonly_request(executor: Any, raw_user_input: str) -> dict[str, Any]:
    return ReadonlyOrchestrator(executor).run(raw_user_input)


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
