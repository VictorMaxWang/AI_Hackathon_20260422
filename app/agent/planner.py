from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models import ParsedIntent

from app.agent.parser import DISK_INTENT, FILE_INTENT, PORT_INTENT, PROCESS_INTENT


@dataclass(frozen=True)
class PlannedToolCall:
    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"tool_name": self.tool_name, "args": dict(self.args)}


@dataclass(frozen=True)
class ReadonlyPlan:
    status: str
    steps: list[PlannedToolCall] = field(default_factory=list)
    reason: str | None = None

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "steps": [step.to_dict() for step in self.steps],
        }


class ReadonlyPlanner:
    """Map parsed read-only intents to whitelisted tool calls."""

    def plan(self, parsed_intent: ParsedIntent) -> ReadonlyPlan:
        if parsed_intent.requires_write:
            return ReadonlyPlan(
                status="refused",
                reason="当前只支持只读基础能力，不执行写操作",
            )

        if parsed_intent.intent == DISK_INTENT:
            return ReadonlyPlan(
                status="ready",
                steps=[PlannedToolCall("disk_usage_tool", {})],
            )

        if parsed_intent.intent == FILE_INTENT:
            base_path = parsed_intent.target.path
            if not base_path:
                return ReadonlyPlan(
                    status="refused",
                    reason="文件检索需要明确 base_path，例如 /var/log 或 /home",
                )
            return ReadonlyPlan(
                status="ready",
                steps=[
                    PlannedToolCall(
                        "file_search_tool",
                        {
                            "base_path": base_path,
                            "name_contains": parsed_intent.target.keyword,
                            "modified_within_days": parsed_intent.constraints.get(
                                "modified_within_days"
                            ),
                            "max_results": parsed_intent.constraints.get("max_results", 20),
                            "max_depth": parsed_intent.constraints.get("max_depth", 4),
                        },
                    )
                ],
            )

        if parsed_intent.intent == PROCESS_INTENT:
            mode = parsed_intent.constraints.get("mode", "cpu")
            return ReadonlyPlan(
                status="ready",
                steps=[
                    PlannedToolCall(
                        "process_query_tool",
                        {
                            "mode": mode,
                            "limit": parsed_intent.constraints.get("limit", 10),
                            "keyword": parsed_intent.target.keyword,
                            "pid": parsed_intent.target.pid,
                        },
                    )
                ],
            )

        if parsed_intent.intent == PORT_INTENT:
            if parsed_intent.target.port is None:
                return ReadonlyPlan(
                    status="refused",
                    reason="端口查询需要明确端口号；当前不使用上下文猜测“这个端口”",
                )
            return ReadonlyPlan(
                status="ready",
                steps=[
                    PlannedToolCall(
                        "port_query_tool",
                        {"port": parsed_intent.target.port},
                    )
                ],
            )

        return ReadonlyPlan(
            status="unsupported",
            reason="当前只支持只读基础能力：磁盘、文件检索、进程和端口查询",
        )


def plan_readonly_tools(parsed_intent: ParsedIntent) -> ReadonlyPlan:
    return ReadonlyPlanner().plan(parsed_intent)
