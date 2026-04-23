from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.models import ParsedIntent
from app.models.intent import ExecutionPlan, PlanStep

from app.agent.parser import DISK_INTENT, FILE_INTENT, PORT_INTENT, PROCESS_INTENT


ENV_PROBE_INTENT = "env_probe"
CREATE_USER_INTENT = "create_user"
DELETE_USER_INTENT = "delete_user"


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


class MultistepPlanner:
    """Rule-based planner for a small, controlled set of multi-step requests."""

    def plan(self, raw_user_input: str, memory: Any | None = None) -> ExecutionPlan:
        text = _clean_text(raw_user_input)
        if not text:
            return _unsupported(raw_user_input, "empty input")

        if _has_unsupported_action(text):
            return _unsupported(raw_user_input, "unsupported or unsafe multi-step request")
        if _has_multiple_write_actions(text):
            return _unsupported(raw_user_input, "multiple write steps are not supported")

        contextual_delete = self._plan_contextual_delete(raw_user_input, text, memory)
        if contextual_delete is not None:
            return contextual_delete

        env_create = self._plan_env_then_create(raw_user_input, text)
        if env_create is not None:
            return env_create

        port_process = self._plan_port_then_process(raw_user_input, text)
        if port_process is not None:
            return port_process

        if_then = self._plan_if_then(raw_user_input, text)
        if if_then is not None:
            return if_then

        simple_sequence = self._plan_simple_first_then(raw_user_input, text)
        if simple_sequence is not None:
            return simple_sequence

        return _unsupported(raw_user_input, "unsupported multi-step pattern")

    def _plan_contextual_delete(
        self,
        raw_user_input: str,
        text: str,
        memory: Any | None,
    ) -> ExecutionPlan | None:
        if not (_looks_like_delete_user(text) and _has_user_context_ref(text)):
            return None

        username = _resolve_memory(memory, "username")
        if not username:
            return _unsupported(raw_user_input, "unresolved user reference")

        return _supported(
            raw_user_input,
            [
                _step(
                    1,
                    DELETE_USER_INTENT,
                    {
                        "username": username,
                        "remove_home": False,
                        "resolved_from_memory": True,
                    },
                    condition="target_user_exists and target_user_uid >= 1000",
                    description=f"Plan deletion of the previously referenced normal user {username}.",
                    requires_confirmation=True,
                )
            ],
        )

    def _plan_env_then_create(self, raw_user_input: str, text: str) -> ExecutionPlan | None:
        if not (_looks_like_env_probe(text) and _looks_like_create_user(text)):
            return None

        username = _extract_username(text)
        if not username:
            return _unsupported(raw_user_input, "missing username for create_user step")

        return _supported(raw_user_input, _env_create_steps(username))

    def _plan_port_then_process(self, raw_user_input: str, text: str) -> ExecutionPlan | None:
        if not (_looks_like_port_query(text) and _looks_like_process_query(text)):
            return None

        port = _extract_port(text)
        if port is None:
            return _unsupported(raw_user_input, "missing port for query_port step")

        return _supported(raw_user_input, _port_process_steps(port))

    def _plan_if_then(self, raw_user_input: str, text: str) -> ExecutionPlan | None:
        condition_text, action_text = _split_if_then(text)
        if condition_text is None or action_text is None:
            return None

        if _looks_like_create_user(action_text) and _looks_like_permission_condition(condition_text):
            username = _extract_username(action_text) or _extract_username(text)
            if not username:
                return _unsupported(raw_user_input, "missing username for conditional create_user step")
            return _supported(raw_user_input, _env_create_steps(username))

        if _looks_like_process_query(action_text) and _looks_like_port_query(condition_text):
            port = _extract_port(condition_text) or _extract_port(text)
            if port is None:
                return _unsupported(raw_user_input, "missing port for conditional process query")
            return _supported(raw_user_input, _port_process_steps(port))

        return _unsupported(raw_user_input, "unsupported if-then condition")

    def _plan_simple_first_then(self, raw_user_input: str, text: str) -> ExecutionPlan | None:
        parts = _split_first_then(text)
        if parts is None:
            return None

        first, second = parts
        if _looks_like_env_probe(first) and _looks_like_create_user(second):
            username = _extract_username(second) or _extract_username(text)
            if not username:
                return _unsupported(raw_user_input, "missing username for create_user step")
            return _supported(raw_user_input, _env_create_steps(username))

        if _looks_like_port_query(first) and _looks_like_process_query(second):
            port = _extract_port(first) or _extract_port(text)
            if port is None:
                return _unsupported(raw_user_input, "missing port for query_port step")
            return _supported(raw_user_input, _port_process_steps(port))

        return _unsupported(raw_user_input, "unsupported first-then sequence")


def plan_multistep(raw_user_input: str, memory: Any | None = None) -> ExecutionPlan:
    return MultistepPlanner().plan(raw_user_input, memory=memory)


def _supported(raw_user_input: str, steps: list[PlanStep]) -> ExecutionPlan:
    return ExecutionPlan(
        raw_user_input=raw_user_input,
        status="supported",
        supported=True,
        steps=steps,
        reason=None,
    )


def _unsupported(raw_user_input: str, reason: str) -> ExecutionPlan:
    return ExecutionPlan(
        raw_user_input=raw_user_input,
        status="unsupported",
        supported=False,
        steps=[],
        reason=reason,
    )


def _step(
    number: int,
    intent: str,
    target: dict[str, Any],
    *,
    depends_on: list[str] | None = None,
    condition: str | None = None,
    description: str,
    requires_policy: bool = True,
    requires_confirmation: bool = False,
) -> PlanStep:
    return PlanStep(
        step_id=f"step_{number}",
        intent=intent,
        target=dict(target),
        depends_on=list(depends_on or []),
        condition=condition,
        description=description,
        requires_policy=requires_policy,
        requires_confirmation=requires_confirmation,
    )


def _env_create_steps(username: str) -> list[PlanStep]:
    return [
        _step(
            1,
            ENV_PROBE_INTENT,
            {},
            description="Probe the environment before considering a user creation step.",
            requires_policy=True,
            requires_confirmation=False,
        ),
        _step(
            2,
            CREATE_USER_INTENT,
            {"username": username, "create_home": True, "no_sudo": True},
            depends_on=["step_1"],
            condition="env.sudo_available or env.is_root",
            description=f"Plan creation of normal user {username} only when sufficient privilege exists.",
            requires_policy=True,
            requires_confirmation=True,
        ),
    ]


def _port_process_steps(port: int) -> list[PlanStep]:
    return [
        _step(
            1,
            PORT_INTENT,
            {"port": port},
            description=f"Query listening state for port {port}.",
            requires_policy=True,
            requires_confirmation=False,
        ),
        _step(
            2,
            PROCESS_INTENT,
            {
                "port": port,
                "from_step": "step_1",
                "pid_from": "step_1.listeners[0].pid",
            },
            depends_on=["step_1"],
            condition="step_1.listener_found",
            description="Query the process only if the port query finds a listener.",
            requires_policy=True,
            requires_confirmation=False,
        ),
    ]


def _clean_text(value: str) -> str:
    return str(value or "").strip()


def _contains_any(text: str, needles: list[str]) -> bool:
    lower_text = text.lower()
    return any(needle.lower() in lower_text for needle in needles)


def _has_unsupported_action(text: str) -> bool:
    lower_text = text.lower()
    if _contains_any(
        text,
        [
            "sudoers",
            "sshd_config",
            "防火墙",
            "开放端口",
            "重启",
            "启动服务",
            "停止服务",
            "安装",
            "卸载",
            "升级系统",
            "修复系统",
            "chmod",
            "chown",
            "systemctl",
            "apt ",
            "yum ",
            "dnf ",
            "docker",
            "kubernetes",
        ],
    ):
        return True

    if re.search(r"\brm\s+-", lower_text):
        return True

    if _contains_any(text, ["删除", "删掉", "移除", "remove", "delete"]):
        if _looks_like_delete_user(text):
            return False
        if _contains_any(text, ["/", "目录", "文件", "日志", "配置"]):
            return True

    if _contains_any(text, ["创建", "新增", "添加"]):
        if _looks_like_create_user(text):
            return False
        return True

    return False


def _has_multiple_write_actions(text: str) -> bool:
    write_count = 0
    if _looks_like_create_user(text):
        write_count += 1
    if _looks_like_delete_user(text):
        write_count += 1
    return write_count > 1


def _looks_like_env_probe(text: str) -> bool:
    return _contains_any(text, ["环境", "权限", "sudo_available", "sudo", "root", "探测"])


def _looks_like_permission_condition(text: str) -> bool:
    return _contains_any(text, ["权限足够", "有权限", "sudo_available", "sudo", "root"])


def _looks_like_create_user(text: str) -> bool:
    return bool(
        _contains_any(text, ["创建", "新增", "添加"])
        and _contains_any(text, ["普通用户", "用户"])
    )


def _looks_like_delete_user(text: str) -> bool:
    return bool(
        _contains_any(text, ["删除", "删掉", "移除", "remove", "delete"])
        and (
            _contains_any(text, ["普通用户", "用户"])
            or _has_user_context_ref(text)
        )
    )


def _looks_like_port_query(text: str) -> bool:
    return bool("端口" in text or re.search(r"\bport\b", text, flags=re.IGNORECASE))


def _looks_like_process_query(text: str) -> bool:
    return _contains_any(text, ["进程", "process", "pid", "对应"])


def _has_user_context_ref(text: str) -> bool:
    return _contains_any(text, ["刚才那个用户", "上一个用户", "刚刚创建的用户", "刚才创建的用户"])


def _extract_username(text: str) -> str | None:
    patterns = [
        r"普通用户\s*([a-z_][a-z0-9_-]{2,31})",
        r"用户\s*([a-z_][a-z0-9_-]{2,31})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _extract_port(text: str) -> int | None:
    patterns = [
        r"(\d{1,5})\s*端口",
        r"端口\s*(\d{1,5})",
        r"\bport\s*(\d{1,5})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        port = int(match.group(1))
        if 0 <= port <= 65535:
            return port
    return None


def _resolve_memory(memory: Any | None, slot: str) -> Any:
    if memory is None:
        return None
    resolver = getattr(memory, "resolve", None)
    if callable(resolver):
        return resolver(slot)
    return getattr(memory, f"last_{slot}", None)


def _split_if_then(text: str) -> tuple[str | None, str | None]:
    match = re.search(r"如果(.+?)(?:则|就|，|,)\s*(.+)", text)
    if not match:
        return None, None
    return match.group(1).strip(), match.group(2).strip()


def _split_first_then(text: str) -> tuple[str, str] | None:
    first_index = text.find("先")
    if first_index < 0:
        return None

    then_index = text.find("再", first_index + 1)
    if then_index < 0:
        return None

    first = text[first_index + 1 : then_index].strip(" ，,。；;")
    second = text[then_index + 1 :].strip(" ，,。；;")
    if not first or not second:
        return None
    if "再" in second:
        return None
    return first, second
