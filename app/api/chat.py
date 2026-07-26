from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agent import ReadonlyOrchestrator
from app.executors import BaseExecutor, LocalExecutor


class Utf8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"


router = APIRouter()


EXPLANATION_SECTION_DEFINITIONS = (
    ("intent_normalized", "请求归一化"),
    ("plan_summary", "计划摘要"),
    ("risk_hits", "风险命中"),
    ("scope_preview", "范围预览"),
    ("confirmation_basis", "确认依据"),
    ("execution_evidence", "执行证据"),
    ("result_assertion", "结果断言"),
    ("residual_risks_or_next_step", "残余风险 / 下一步"),
)

MAX_RAW_USER_INPUT_LENGTH = 2000
SESSION_COOKIE_NAME = "guardedops_session"
SESSION_HEADER_NAME = "x-guardedops-session"
SESSION_ID_MAX_LENGTH = 128
SESSION_TTL_SECONDS = 1800.0
MAX_ACTIVE_SESSIONS = 128
TOOL_CALL_OUTPUT_MAX_CHARS = 400
TOOL_CALL_MAX_WARNINGS = 5
TOOL_CALL_WARNING_MAX_CHARS = 200
MAX_VALIDATION_ERRORS = 10
VALIDATION_MESSAGE_MAX_CHARS = 200

SESSION_CAPACITY_MESSAGE = (
    "服务端待确认会话已达上限。为了不丢弃正在等待精确确认的写操作，本次请求不再新建会话。"
    "请先完成或取消已有的确认，然后重试。"
)
SESSION_CAPACITY_RETRY_AFTER_SECONDS = 30

_SESSION_ID_PATTERN = re.compile(r"\A[A-Za-z0-9._:-]{1,%d}\Z" % SESSION_ID_MAX_LENGTH)
_REGISTRY_LOCK = threading.Lock()

LOGGER = logging.getLogger("guardedops.api.sessions")


class SessionCapacityExceeded(RuntimeError):
    """No session could be freed without discarding guarded state awaiting confirmation."""


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_user_input: str = Field(min_length=1, max_length=MAX_RAW_USER_INPUT_LENGTH)
    session_id: str | None = Field(
        default=None,
        max_length=SESSION_ID_MAX_LENGTH,
        description=(
            "未经认证的持有者标识（bearer identifier）：任何出示该值的客户端都拥有这份会话的"
            "待确认动作。本 Demo 是单操作员模型，不提供多租户隔离保证。"
            "浏览器请改用服务端签发的 httpOnly Cookie；该字段留给 CLI 与脚本。"
        ),
    )

    @field_validator("raw_user_input")
    @classmethod
    def _reject_blank_request(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("raw_user_input 不能只包含空白字符")
        return value


@dataclass
class ChatSession:
    """One conversation: its orchestrator, its memory, its own lock."""

    session_id: str
    orchestrator: Any
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_seen: float = 0.0

    def has_pending_action(self) -> bool:
        """Any pending action at all — the audit view, used when something is discarded."""

        try:
            return self._pending_action() is not None
        except Exception:  # pragma: no cover - an unreadable orchestrator stays pinned
            return True

    def pins_eviction(self) -> bool:
        """Only a still-confirmable pending action pins the session in the cache.

        The confirmation token carries its own TTL: once it has passed, the phrase
        would be rejected anyway, so the cache entry no longer protects anything the
        operator can still act on. That keeps pending-action lifetime from turning
        into an unbounded hold on the session cache.
        """

        try:
            pending = self._pending_action()
            if pending is None:
                return False
            token = getattr(pending, "confirmation_token", None)
            is_expired = getattr(token, "is_expired", None)
            return not (callable(is_expired) and is_expired())
        except Exception:  # pragma: no cover - an unreadable orchestrator stays pinned
            return True

    def _pending_action(self) -> Any:
        memory = getattr(self.orchestrator, "memory", None)
        return getattr(memory, "pending_action", None)


class SessionRegistry:
    """Bounded per-session orchestrator cache with TTL expiry and pending-aware eviction.

    A session holding a pending action is pinned: eviction walks further down the LRU
    order rather than dropping it, and when every cached session is pinned the *new*
    session is rejected instead. Refusing to start new work is safer than silently
    voiding a write the operator was already told is waiting for exact confirmation.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = SESSION_TTL_SECONDS,
        max_sessions: int = MAX_ACTIVE_SESSIONS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = float(ttl_seconds)
        self._max_sessions = max(1, int(max_sessions))
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: OrderedDict[str, ChatSession] = OrderedDict()

    def acquire(self, session_id: str, factory: Callable[[], Any]) -> ChatSession:
        with self._lock:
            now = self._clock()
            self._drop_expired(now)
            session = self._sessions.get(session_id)
            if session is None:
                self._make_room_for_new_session()
                session = ChatSession(session_id=session_id, orchestrator=factory())
                self._sessions[session_id] = session
            session.last_seen = now
            self._sessions.move_to_end(session_id)
            return session

    def active_session_ids(self) -> list[str]:
        with self._lock:
            return list(self._sessions)

    def pending_session_ids(self) -> list[str]:
        with self._lock:
            return [key for key, session in self._sessions.items() if session.has_pending_action()]

    def _drop_expired(self, now: float) -> None:
        expired = [
            key
            for key, session in self._sessions.items()
            if now - session.last_seen > self._ttl_seconds
        ]
        for key in expired:
            self._discard(key, reason="ttl_expired")

    def _make_room_for_new_session(self) -> None:
        while len(self._sessions) >= self._max_sessions:
            victim = self._first_evictable_session_id()
            if victim is None:
                raise SessionCapacityExceeded(
                    f"all {len(self._sessions)} cached sessions hold a confirmable pending action"
                )
            self._discard(victim, reason="lru_evicted")

    def _first_evictable_session_id(self) -> str | None:
        for key, session in self._sessions.items():
            if not session.pins_eviction():
                return key
        return None

    def _discard(self, session_id: str, *, reason: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is not None and session.has_pending_action():
            LOGGER.warning(
                "discarded a session holding a pending action session_id=%s reason=%s",
                session_id,
                reason,
            )


def get_executor() -> BaseExecutor:
    return LocalExecutor()


def session_registry(app: FastAPI) -> SessionRegistry:
    registry = getattr(app.state, "chat_sessions", None)
    if isinstance(registry, SessionRegistry):
        return registry
    with _REGISTRY_LOCK:
        registry = getattr(app.state, "chat_sessions", None)
        if not isinstance(registry, SessionRegistry):
            registry = SessionRegistry()
            app.state.chat_sessions = registry
        return registry


@router.post(
    "/api/chat",
    response_class=Utf8JSONResponse,
    tags=["chat"],
    summary="提交一条自然语言运维请求",
    description=(
        "把自然语言解析为结构化 intent，交给策略引擎判定风险等级"
        "（S0 只读放行 / S1-S2 需要精确确认 / S3 拒绝），"
        "只允许白名单工具执行，并返回带证据链的统一信封。\n\n"
        "会话隔离：`session_id`（或 `X-GuardedOps-Session` 头、`guardedops_session` Cookie）"
        "决定使用哪一份多轮上下文；未携带时服务端签发一个新会话，"
        "任何一个会话的待确认动作都不会出现在另一个会话的响应里。\n\n"
        "**信任模型（请照实理解，不要当成多租户隔离）**："
        "`session_id` 是未经认证的持有者标识，任何出示该值的客户端都拥有这份会话的待确认动作。"
        "本 Demo 面向单操作员：浏览器走服务端签发的 httpOnly Cookie（页面 JavaScript 读不到它），"
        "请求体字段与请求头形式保留给 CLI 与脚本。"
    ),
    response_description="统一响应信封：intent / risk / plan / execution / result / evidence_chain / operator_panel",
    responses={
        200: {"description": "请求已受控处理（成功、等待确认或被策略拒绝都返回 200）"},
        422: {"description": "请求体不合法，例如 raw_user_input 为空或超过长度上限"},
        500: {"description": "服务端内部错误，返回带 correlation_id 的失败信封，不会执行任何命令"},
        503: {
            "description": (
                "所有在册会话都持有待确认动作，服务端拒绝新建会话，"
                "而不是丢弃已经受控的待确认写操作"
            )
        },
    },
)
def chat(
    request: ChatRequest,
    http_request: Request,
    response: Response,
    executor: BaseExecutor = Depends(get_executor),
) -> dict[str, Any]:
    session_id, session_source = _resolve_session_id(http_request, request.session_id)
    http_request.state.raw_user_input = request.raw_user_input
    http_request.state.session_id = session_id

    try:
        session = _acquire_session(http_request.app, session_id=session_id, executor=executor)
    except SessionCapacityExceeded as exc:
        LOGGER.warning(
            "refused a new session because every cached session is pending confirmation: %s",
            exc,
        )
        raise HTTPException(
            status_code=503,
            detail=SESSION_CAPACITY_MESSAGE,
            headers={"Retry-After": str(SESSION_CAPACITY_RETRY_AFTER_SECONDS)},
        ) from exc

    with session.lock:
        envelope = dict(session.orchestrator.run(request.raw_user_input))

    envelope["session_id"] = session_id
    envelope["operator_panel"] = _build_operator_panel_view(
        envelope,
        raw_user_input=request.raw_user_input,
    )
    if session_source in {"issued", "cookie"}:
        response.set_cookie(
            SESSION_COOKIE_NAME,
            session_id,
            max_age=int(SESSION_TTL_SECONDS),
            path="/",
            httponly=True,
            samesite="lax",
        )
    return envelope


def build_internal_error_envelope(
    *,
    correlation_id: str,
    raw_user_input: str = "",
    path: str = "",
) -> dict[str, Any]:
    """Auditable failure envelope for the one path that used to emit no evidence."""

    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    event_id = f"err-{correlation_id[:12]}"
    explanation = (
        f"服务端内部错误，本次请求没有执行任何命令。请携带关联 ID {correlation_id} 排查服务端日志。"
    )
    event = {
        "event_id": event_id,
        "stage": "result",
        "title": "internal_error",
        "details": {
            "status": "failed",
            "correlation_id": correlation_id,
            "path": path,
            "why_it_failed": "服务端在处理该请求时抛出未预期异常。",
        },
        "severity": "critical",
        "refs": [f"correlation:{correlation_id}"],
        "timestamp": timestamp,
    }
    envelope: dict[str, Any] = {
        "correlation_id": correlation_id,
        "intent": {
            "intent": "unknown",
            "raw_user_input": raw_user_input,
            "confidence": None,
            "confidence_source": "server_error",
        },
        "environment": {"status": "not_collected", "snapshot": {}},
        "risk": {
            "risk_level": "unknown",
            "allow": False,
            "requires_confirmation": False,
            "reasons": ["服务端内部错误，未做任何策略放行。"],
            "safe_alternative": "先提交一条受限只读请求确认服务可用。",
        },
        "plan": {"status": "failed", "reason": "internal_error", "steps": []},
        "execution": {"status": "skipped", "steps": [], "results": []},
        "result": {
            "status": "failed",
            "data": None,
            "error": f"internal_error:{correlation_id}",
        },
        "explanation": explanation,
        "evidence_chain": {
            "events": [event],
            "state_assertions": [
                {
                    "assertion_id": f"as-{correlation_id[:12]}",
                    "name": "no_command_executed",
                    "passed": True,
                    "evidence_refs": [event_id],
                    "summary": "内部错误发生在工具边界之前，没有任何命令被执行。",
                }
            ],
        },
        "explanation_card": {
            "intent_normalized": {"summary": "请求未完成解析。", "evidence_refs": []},
            "plan_summary": {"summary": "未生成任何计划。", "evidence_refs": []},
            "risk_hits": {"summary": "未做策略判定，按失败处理。", "evidence_refs": []},
            "scope_preview": {"summary": "没有任何工具被执行，影响范围为空。", "evidence_refs": []},
            "confirmation_basis": {"summary": "当前请求无确认依据。", "evidence_refs": []},
            "execution_evidence": {
                "summary": f"内部错误证据已记录，关联 ID {correlation_id}。",
                "evidence_refs": [event_id],
            },
            "result_assertion": {"summary": "最终结果为 failed。", "evidence_refs": [event_id]},
            "residual_risks_or_next_step": {
                "summary": "请携带关联 ID 检查服务端日志后再重试。",
                "evidence_refs": [event_id],
            },
        },
        "recovery": {
            "failure_type": "internal_error",
            "why_it_failed": "服务端在处理该请求时抛出未预期异常，没有任何工具被执行。",
            "safe_next_steps": [
                f"记录关联 ID {correlation_id}。",
                "改用一条受限只读请求确认服务可用后再继续。",
            ],
            "suggested_readonly_diagnostics": ["先查看服务端日志中同一关联 ID 的堆栈。"],
            "requires_confirmation_for_recovery": False,
            "can_retry_safely": False,
        },
    }
    envelope["operator_panel"] = _build_operator_panel_view(
        envelope,
        raw_user_input=raw_user_input,
    )
    return envelope


def build_validation_error_payload(errors: Any) -> dict[str, Any]:
    """Compact 422 body that never echoes the rejected input back to the caller."""

    detail: list[dict[str, Any]] = []
    for item in _as_list(errors)[:MAX_VALIDATION_ERRORS]:
        entry = _as_dict(item)
        location = entry.get("loc")
        parts = list(location) if isinstance(location, (list, tuple)) else _as_list(location)
        detail.append(
            {
                "type": _first_text(entry.get("type"), "value_error"),
                "loc": [str(part) for part in parts],
                "msg": _compact_json(
                    _first_text(entry.get("msg"), "请求内容不合法"),
                    limit=VALIDATION_MESSAGE_MAX_CHARS,
                ),
            }
        )
    return {"detail": detail}


def _acquire_session(
    app: FastAPI,
    *,
    session_id: str,
    executor: BaseExecutor,
) -> ChatSession:
    registry = session_registry(app)
    override = getattr(app.state, "chat_orchestrator", None)
    if override is not None:
        return registry.acquire("__injected__", lambda: override)
    return registry.acquire(session_id, lambda: ReadonlyOrchestrator(executor))


def _resolve_session_id(http_request: Request, requested: str | None) -> tuple[str, str]:
    """Resolve the session id and where it came from.

    The returned id is an unauthenticated bearer identifier, not proof of identity:
    whichever client presents it owns that session's pending action. The demo is a
    single-operator tool, so the browser path uses a server-issued httpOnly cookie
    that page JavaScript cannot read, and the body/header forms stay for CLI use.
    """

    candidates = (
        ("body", requested),
        ("header", http_request.headers.get(SESSION_HEADER_NAME)),
        ("cookie", http_request.cookies.get(SESSION_COOKIE_NAME)),
    )
    for source, candidate in candidates:
        normalized = _normalize_session_id(candidate)
        if normalized:
            return normalized, source
    return uuid.uuid4().hex, "issued"


def _normalize_session_id(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if not candidate or not _SESSION_ID_PATTERN.match(candidate):
        return ""
    return candidate


def _build_operator_panel_view(
    payload: dict[str, Any],
    *,
    raw_user_input: str,
) -> dict[str, Any]:
    intent = _as_dict(payload.get("intent"))
    risk = _as_dict(payload.get("risk"))
    plan = _as_dict(payload.get("plan"))
    execution = _as_dict(payload.get("execution"))
    result = _as_dict(payload.get("result"))
    recovery = _as_dict(payload.get("recovery"))
    explanation_card = _as_dict(payload.get("explanation_card"))
    evidence_chain = _as_dict(payload.get("evidence_chain"))
    blast_radius_preview = _as_dict(payload.get("blast_radius_preview"))
    policy_simulator = _as_dict(payload.get("policy_simulator"))
    timeline = _as_list(payload.get("timeline"))
    environment = _as_dict(payload.get("environment"))
    status = _first_text(
        result.get("status"),
        plan.get("status"),
        execution.get("status"),
        "unknown",
    )
    risk_level = _first_text(risk.get("risk_level"), "unknown")

    return {
        "user_input": _first_text(raw_user_input, intent.get("raw_user_input"), "-"),
        "status": status,
        "risk_level": risk_level,
        "risk_reasons": _string_list(risk.get("reasons")),
        "confidence": _normalize_confidence(intent.get("confidence")),
        "confidence_source": _first_text(
            intent.get("confidence_source"),
            risk.get("confidence_source"),
            result.get("confidence_source"),
        ),
        "blast_radius_preview": _build_blast_radius_preview(blast_radius_preview),
        "policy_simulator": _build_policy_simulator(policy_simulator),
        "explanation_sections": _build_explanation_sections(explanation_card),
        "timeline_entries": _build_timeline_entries(
            timeline=timeline,
            evidence_chain=evidence_chain,
        ),
        "tool_calls": _build_tool_calls(
            execution=execution,
            evidence_chain=evidence_chain,
        ),
        "preflight_items": _build_preflight_items(
            intent=intent,
            risk=risk,
            plan=plan,
            execution=execution,
            result=result,
            environment=environment,
            evidence_chain=evidence_chain,
        ),
        "confirmation": _build_confirmation_block(
            risk=risk,
            plan=plan,
            execution=execution,
            result=result,
            explanation_card=explanation_card,
            evidence_chain=evidence_chain,
        ),
        "refusal": _build_refusal_block(
            risk=risk,
            plan=plan,
            result=result,
            explanation_card=explanation_card,
        ),
        "recovery": _build_recovery_block(recovery),
        "residual_next_step": _build_residual_block(explanation_card),
    }


def _build_explanation_sections(explanation_card: dict[str, Any]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for key, label in EXPLANATION_SECTION_DEFINITIONS:
        section = _as_dict(explanation_card.get(key))
        sections.append(
            {
                "key": key,
                "label": label,
                "summary": _first_text(section.get("summary"), "-"),
                "evidence_refs": _string_list(section.get("evidence_refs")),
            }
        )
    return sections


def _build_timeline_entries(
    *,
    timeline: list[Any],
    evidence_chain: dict[str, Any],
) -> list[dict[str, Any]]:
    entries = [item for item in timeline if isinstance(item, dict)]
    if entries:
        return [_timeline_entry_from_narrative(index, item) for index, item in enumerate(entries, start=1)]

    evidence_events = [
        item
        for item in _as_list(evidence_chain.get("events"))
        if isinstance(item, dict)
    ]
    return [
        _timeline_entry_from_evidence(index, item)
        for index, item in enumerate(evidence_events, start=1)
    ]


def _timeline_entry_from_narrative(index: int, item: dict[str, Any]) -> dict[str, Any]:
    intent = _first_text(item.get("intent"), item.get("step_id"), f"step_{index}")
    status = _first_text(item.get("status"), "unknown")
    risk_level = _first_text(item.get("risk"))
    summary = _first_text(
        item.get("result_summary"),
        f"{intent} -> {status}",
    )
    return {
        "source": "timeline",
        "index": index,
        "title": intent,
        "summary": summary,
        "status": status,
        "severity": _severity_for_timeline_status(status),
        "stage": _first_text(item.get("intent"), "timeline"),
        "risk_level": risk_level,
        "timestamp": _first_text(item.get("timestamp")),
        "evidence_refs": _string_list(item.get("refs")),
        "step_id": _first_text(item.get("step_id")),
    }


def _timeline_entry_from_evidence(index: int, item: dict[str, Any]) -> dict[str, Any]:
    details = _as_dict(item.get("details"))
    title = _first_text(item.get("title"), item.get("stage"), f"event_{index}")
    return {
        "source": "evidence",
        "index": index,
        "title": title,
        "summary": _evidence_event_summary(item),
        "status": _first_text(details.get("status"), details.get("result_status")),
        "severity": _first_text(item.get("severity"), "info"),
        "stage": _first_text(item.get("stage"), "evidence"),
        "risk_level": _first_text(
            details.get("risk_level"),
            details.get("risk"),
        ),
        "timestamp": _first_text(item.get("timestamp")),
        "evidence_refs": _string_list(item.get("refs")),
        "step_id": _first_text(details.get("step_id")),
    }


def _build_tool_calls(
    *,
    execution: dict[str, Any],
    evidence_chain: dict[str, Any],
) -> list[dict[str, Any]]:
    steps = [item for item in _as_list(execution.get("steps")) if isinstance(item, dict)]
    results = [item for item in _as_list(execution.get("results")) if isinstance(item, dict)]
    events = _tool_call_events_by_order(evidence_chain)

    calls: list[dict[str, Any]] = []
    for index, result in enumerate(results, start=1):
        step = steps[index - 1] if index - 1 <= len(steps) - 1 else {}
        data = _as_dict(result.get("data"))
        success = bool(result.get("success"))
        calls.append(
            {
                "order": index,
                "tool_name": _first_text(result.get("tool_name"), step.get("tool_name"), "unknown"),
                "status": "success" if success else "failed",
                "success": success,
                "args": _normalize_labeled_args(step.get("args")),
                "command": _first_text(data.get("source")),
                "error": _first_text(result.get("error")),
                "partial": bool(data.get("partial")),
                "warnings": _tool_warnings(data.get("warnings")),
                "output_excerpt": _tool_output_excerpt(result.get("data")),
                "started_at": _first_text(step.get("started_at")),
                "finished_at": _first_text(step.get("finished_at")),
                "evidence_refs": _event_refs(events.get(index)),
            }
        )
    return calls


def _tool_call_events_by_order(evidence_chain: dict[str, Any]) -> dict[int, dict[str, Any]]:
    events: dict[int, dict[str, Any]] = {}
    order = 0
    for item in _as_list(evidence_chain.get("events")):
        if not isinstance(item, dict):
            continue
        if _first_text(item.get("stage")).lower() != "tool_call":
            continue
        order += 1
        details = _as_dict(item.get("details"))
        try:
            declared = int(details.get("order"))
        except (TypeError, ValueError):
            declared = order
        events[declared] = item
    return events


def _normalize_labeled_args(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, dict):
        return []
    normalized: list[dict[str, str]] = []
    for key, item in value.items():
        normalized.append(
            {
                "label": _first_text(key, "arg"),
                "value": _compact_json(item, limit=TOOL_CALL_OUTPUT_MAX_CHARS),
            }
        )
    return normalized


def _tool_output_excerpt(value: Any) -> str:
    if value is None:
        return ""
    return _compact_json(value, limit=TOOL_CALL_OUTPUT_MAX_CHARS)


def _tool_warnings(value: Any) -> list[str]:
    warnings: list[str] = []
    for item in _string_list(value)[:TOOL_CALL_MAX_WARNINGS]:
        warnings.append(_compact_json(item, limit=TOOL_CALL_WARNING_MAX_CHARS))
    return warnings


def _compact_json(value: Any, *, limit: int) -> str:
    if isinstance(value, str):
        text = value.strip()
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _build_preflight_items(
    *,
    intent: dict[str, Any],
    risk: dict[str, Any],
    plan: dict[str, Any],
    execution: dict[str, Any],
    result: dict[str, Any],
    environment: dict[str, Any],
    evidence_chain: dict[str, Any],
) -> list[dict[str, Any]]:
    events = [
        item
        for item in _as_list(evidence_chain.get("events"))
        if isinstance(item, dict)
    ]
    parse_event = _first_event_by_stage(events, "parse")
    policy_event = _first_event_by_stage(events, "policy")
    confirmation_event = _first_event_by_stage(events, "confirmation")

    plan_status = _first_text(plan.get("status"), "unknown")
    result_status = _first_text(result.get("status"), execution.get("status"), "unknown")
    requires_confirmation = bool(risk.get("requires_confirmation"))
    environment_status = _first_text(environment.get("status"), "not_collected")
    risk_level = _first_text(risk.get("risk_level"), "unknown")

    return [
        {
            "key": "intent_parsed",
            "label": "Intent parsed",
            "status": "ready" if parse_event else "not_available",
            "summary": (
                f"识别为 {intent_name}。"
                if (intent_name := _first_text(intent.get("intent")))
                else "未发现 parse evidence。"
            ),
            "evidence_refs": _event_refs(parse_event),
        },
        {
            "key": "policy_bound",
            "label": "Policy bound",
            "status": "ready" if policy_event else "not_available",
            "summary": (
                f"绑定到 {risk_level} 风险决策。"
                if policy_event
                else "未发现 policy evidence。"
            ),
            "evidence_refs": _event_refs(policy_event),
        },
        {
            "key": "plan_ready",
            "label": "Plan ready",
            "status": _plan_preflight_status(plan_status),
            "summary": f"计划状态：{plan_status}。",
            "evidence_refs": _event_refs(_first_event_by_stage(events, "plan")),
        },
        {
            "key": "confirmation_gate",
            "label": "Confirmation gate",
            "status": _confirmation_preflight_status(
                requires_confirmation=requires_confirmation,
                plan_status=plan_status,
                result_status=result_status,
                result_error=_first_text(result.get("error")),
            ),
            "summary": _confirmation_preflight_summary(
                requires_confirmation=requires_confirmation,
                plan_status=plan_status,
                result_status=result_status,
                result_error=_first_text(result.get("error")),
                confirmation_text=_first_text(
                    result.get("confirmation_text"),
                    risk.get("confirmation_text"),
                ),
            ),
            "evidence_refs": _event_refs(confirmation_event),
        },
        {
            "key": "environment_ready",
            "label": "Environment ready",
            "status": _environment_preflight_status(environment_status),
            "summary": f"环境状态：{environment_status}。",
            "evidence_refs": [],
        },
    ]


def _build_confirmation_block(
    *,
    risk: dict[str, Any],
    plan: dict[str, Any],
    execution: dict[str, Any],
    result: dict[str, Any],
    explanation_card: dict[str, Any],
    evidence_chain: dict[str, Any],
) -> dict[str, Any]:
    requires_confirmation = bool(risk.get("requires_confirmation"))
    plan_status = _first_text(plan.get("status"), "unknown")
    result_status = _first_text(result.get("status"), execution.get("status"), "unknown")
    result_error = _first_text(result.get("error"))
    confirmation_text = _first_text(
        result.get("confirmation_text"),
        risk.get("confirmation_text"),
    )
    section = _as_dict(explanation_card.get("confirmation_basis"))
    events = [
        item
        for item in _as_list(_as_dict(evidence_chain).get("events"))
        if isinstance(item, dict)
    ]
    return {
        "required": requires_confirmation,
        "status": _confirmation_panel_status(
            requires_confirmation=requires_confirmation,
            plan_status=plan_status,
            result_status=result_status,
            result_error=result_error,
        ),
        "text": confirmation_text,
        "summary": _first_text(section.get("summary"), "当前请求无确认依据。"),
        "evidence_refs": _string_list(section.get("evidence_refs"))
        or _event_refs(_first_event_by_stage(events, "confirmation")),
    }


def _build_refusal_block(
    *,
    risk: dict[str, Any],
    plan: dict[str, Any],
    result: dict[str, Any],
    explanation_card: dict[str, Any],
) -> dict[str, Any]:
    status = _first_text(result.get("status"), plan.get("status"), "unknown")
    section = _as_dict(explanation_card.get("risk_hits"))
    return {
        "is_refused": status == "refused" or _first_text(plan.get("status")) == "refused",
        "reason": _first_text(result.get("error"), plan.get("reason"), section.get("summary")),
        "safe_alternative": _first_text(risk.get("safe_alternative")),
        "evidence_refs": _string_list(section.get("evidence_refs")),
    }


def _build_blast_radius_preview(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {
            "scenario": "",
            "summary": "",
            "facts": [],
            "impacts": [],
            "protected_paths": [],
            "notes": [],
        }

    return {
        "scenario": _first_text(payload.get("scenario"), "general"),
        "summary": _first_text(payload.get("summary")),
        "facts": _normalize_labeled_items(payload.get("facts")),
        "impacts": _normalize_impacts(payload.get("impacts")),
        "protected_paths": _string_list(payload.get("protected_paths")),
        "notes": _string_list(payload.get("notes")),
    }


def _build_policy_simulator(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {
            "risk_level": "",
            "allow": False,
            "requires_confirmation": False,
            "policy_version": "",
            "matched_rules": [],
            "denied_because": [],
            "requires_confirmation_because": [],
            "scope_summary": "",
            "target_fingerprint": "",
            "safe_alternative": "",
        }

    return {
        "risk_level": _first_text(payload.get("risk_level"), "unknown"),
        "allow": bool(payload.get("allow")),
        "requires_confirmation": bool(payload.get("requires_confirmation")),
        "policy_version": _first_text(payload.get("policy_version")),
        "matched_rules": _normalize_matched_rules(payload.get("matched_rules")),
        "denied_because": _string_list(payload.get("denied_because")),
        "requires_confirmation_because": _string_list(
            payload.get("requires_confirmation_because")
        ),
        "scope_summary": _first_text(payload.get("scope_summary")),
        "target_fingerprint": _first_text(payload.get("target_fingerprint")),
        "safe_alternative": _first_text(payload.get("safe_alternative")),
    }


def _build_recovery_block(recovery: dict[str, Any]) -> dict[str, Any]:
    if not recovery:
        return {
            "available": False,
            "failure_type": None,
            "why_it_failed": "",
            "safe_next_steps": [],
            "suggested_readonly_diagnostics": [],
            "requires_confirmation_for_recovery": False,
            "can_retry_safely": False,
        }

    return {
        "available": True,
        "failure_type": _first_text(recovery.get("failure_type")),
        "why_it_failed": _first_text(recovery.get("why_it_failed"), "-"),
        "safe_next_steps": _string_list(recovery.get("safe_next_steps")),
        "suggested_readonly_diagnostics": _string_list(
            recovery.get("suggested_readonly_diagnostics")
        ),
        "requires_confirmation_for_recovery": bool(
            recovery.get("requires_confirmation_for_recovery")
        ),
        "can_retry_safely": bool(recovery.get("can_retry_safely")),
    }


def _build_residual_block(explanation_card: dict[str, Any]) -> dict[str, Any]:
    section = _as_dict(explanation_card.get("residual_risks_or_next_step"))
    return {
        "summary": _first_text(section.get("summary"), "-"),
        "evidence_refs": _string_list(section.get("evidence_refs")),
    }


def _normalize_labeled_items(value: Any) -> list[dict[str, str]]:
    items = [item for item in _as_list(value) if isinstance(item, dict)]
    normalized: list[dict[str, str]] = []
    for item in items:
        normalized.append(
            {
                "label": _first_text(item.get("label"), "item"),
                "value": _first_text(item.get("value"), "-"),
            }
        )
    return normalized


def _normalize_impacts(value: Any) -> list[dict[str, str]]:
    items = [item for item in _as_list(value) if isinstance(item, dict)]
    normalized: list[dict[str, str]] = []
    for item in items:
        normalized.append(
            {
                "label": _first_text(item.get("label"), "impact"),
                "value": _first_text(item.get("value"), "-"),
                "precision": _first_text(item.get("precision"), "conservative"),
            }
        )
    return normalized


def _normalize_matched_rules(value: Any) -> list[dict[str, str]]:
    items = [item for item in _as_list(value) if isinstance(item, dict)]
    normalized: list[dict[str, str]] = []
    for item in items:
        normalized.append(
            {
                "rule_id": _first_text(item.get("rule_id"), "unknown.rule"),
                "outcome": _first_text(item.get("outcome"), "deny"),
                "summary": _first_text(item.get("summary"), "-"),
            }
        )
    return normalized


def _plan_preflight_status(plan_status: str) -> str:
    lowered = plan_status.lower()
    if lowered in {"refused", "unsupported", "failed"}:
        return "blocked"
    if lowered in {"pending_confirmation", "cancelled", "incomplete", "skipped"}:
        return "pending"
    if lowered in {"unknown", ""}:
        return "not_available"
    return "ready"


def _confirmation_preflight_status(
    *,
    requires_confirmation: bool,
    plan_status: str,
    result_status: str,
    result_error: str,
) -> str:
    if not requires_confirmation:
        return "not_required"
    lowered_error = result_error.lower()
    if lowered_error == "confirmation_text_mismatch" or lowered_error.startswith(
        "confirmation_token_"
    ):
        return "blocked"
    lowered_plan = plan_status.lower()
    lowered_result = result_status.lower()
    if lowered_result == "pending_confirmation" or lowered_plan == "pending_confirmation":
        return "pending"
    if lowered_plan == "confirmed" or lowered_result in {"success", "completed"}:
        return "ready"
    if lowered_result in {"refused", "failed", "cancelled"}:
        return "blocked"
    return "pending"


def _confirmation_preflight_summary(
    *,
    requires_confirmation: bool,
    plan_status: str,
    result_status: str,
    result_error: str,
    confirmation_text: str,
) -> str:
    if not requires_confirmation:
        return "该请求不需要额外确认。"
    if result_error == "confirmation_text_mismatch" or result_error.startswith(
        "confirmation_token_"
    ):
        return "确认绑定失配，当前请求未继续执行。"
    if result_status == "pending_confirmation" or plan_status == "pending_confirmation":
        if confirmation_text:
            return f"等待精确确认：{confirmation_text}"
        return "等待精确确认后继续。"
    if plan_status == "confirmed" or result_status in {"success", "completed"}:
        return "确认门已满足。"
    return "确认状态待定。"


def _confirmation_panel_status(
    *,
    requires_confirmation: bool,
    plan_status: str,
    result_status: str,
    result_error: str,
) -> str:
    if not requires_confirmation:
        return "not_required"
    lowered_error = result_error.lower()
    if lowered_error == "confirmation_text_mismatch" or lowered_error.startswith(
        "confirmation_token_"
    ):
        return "mismatch"
    lowered_result = result_status.lower()
    lowered_plan = plan_status.lower()
    if lowered_result == "pending_confirmation" or lowered_plan == "pending_confirmation":
        return "pending_confirmation"
    if lowered_result == "cancelled" or lowered_plan == "cancelled":
        return "cancelled"
    if lowered_plan == "confirmed" or lowered_result in {"success", "completed"}:
        return "confirmed"
    return "required"


def _environment_preflight_status(environment_status: str) -> str:
    lowered = environment_status.lower()
    if lowered == "ok":
        return "ready"
    if lowered == "error":
        return "blocked"
    return "not_available"


def _evidence_event_summary(event: dict[str, Any]) -> str:
    stage = _first_text(event.get("stage"), "evidence")
    title = _first_text(event.get("title"), stage)
    details = _as_dict(event.get("details"))
    for key in (
        "result_summary",
        "summary",
        "why_it_failed",
        "error",
        "status",
        "result_status",
        "tool_name",
        "intent",
    ):
        value = _first_text(details.get(key))
        if value:
            return value
    return f"{stage}: {title}"


def _severity_for_timeline_status(status: str) -> str:
    lowered = status.lower()
    if lowered in {"failed", "refused", "aborted"}:
        return "critical"
    if lowered in {"pending_confirmation", "cancelled", "skipped", "incomplete"}:
        return "warning"
    return "info"


def _first_event_by_stage(events: list[dict[str, Any]], stage: str) -> dict[str, Any] | None:
    for item in events:
        if _first_text(item.get("stage")).lower() == stage:
            return item
    return None


def _event_refs(event: dict[str, Any] | None) -> list[str]:
    if event is None:
        return []
    refs = _string_list(event.get("refs"))
    event_id = _first_text(event.get("event_id"))
    if event_id and event_id not in refs:
        refs.insert(0, event_id)
    return refs


def _normalize_confidence(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if confidence < 0 or confidence > 1:
        return None
    return confidence


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _string_list(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    cleaned: list[str] = []
    for item in items:
        if item is None:
            continue
        text = str(item).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""
