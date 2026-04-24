from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from app.agent.parser import DISK_INTENT, FILE_INTENT, PORT_INTENT, PROCESS_INTENT
from app.models import ParsedIntent, PolicyDecision, RiskLevel, ToolResult
from app.models.evidence import (
    EvidenceChain,
    EvidenceStage,
    ExplanationCard,
    ExplanationSection,
)


class ReadonlySummarizer:
    """Summaries and explanation-card rendering for GuardedOps orchestrator outputs."""

    def summarize(
        self,
        parsed_intent: ParsedIntent,
        *,
        status: str,
        tool_result: ToolResult | None = None,
        reason: str | None = None,
        risk: PolicyDecision | None = None,
    ) -> str:
        if status == "refused" and risk is not None and risk.risk_level == RiskLevel.S3:
            return _summarize_s3_refusal(parsed_intent, risk, reason)
        if _is_port_query_unsupported_environment(parsed_intent, tool_result):
            return (
                tool_result.error
                or "当前本地环境缺少端口查询所需的系统工具，因此无法完成该查询。"
                "建议在 Linux/SSH 目标环境中执行，或配置可用的端口查询工具。"
            )
        if status in {"unsupported", "refused", "skipped"}:
            return f"{reason or '当前只支持只读基础能力'}，未执行任何命令。"
        if status == "failed":
            return f"只读请求执行失败：{reason or '工具调用失败'}。"
        if tool_result is None:
            return "只读请求已处理，但没有可总结的工具结果。"
        if not tool_result.success:
            return f"只读工具执行失败：{tool_result.error or '未知错误'}。"

        if parsed_intent.intent == DISK_INTENT:
            return _summarize_disk(tool_result.data)
        if parsed_intent.intent == FILE_INTENT:
            return _summarize_file_search(tool_result.data)
        if parsed_intent.intent == PROCESS_INTENT:
            return _summarize_process(tool_result.data)
        if parsed_intent.intent == PORT_INTENT:
            return _summarize_port(tool_result.data)

        return "当前只支持只读基础能力，未识别到可总结的结果。"

    def summarize_continuous(
        self,
        *,
        status: str,
        timeline: list[dict[str, Any]],
        reason: str | None = None,
        confirmation_text: str | None = None,
        pending_intent: str | None = None,
    ) -> str:
        delete_reason = ""
        if pending_intent == "delete_user" or any(
            item.get("intent") == "delete_user" for item in timeline
        ):
            delete_reason = "删除比创建更敏感，因为它会影响账号访问、文件归属和可恢复性。"

        if status == "pending_confirmation":
            confirm_part = f"请输入精确确认语：{confirmation_text}。" if confirmation_text else ""
            return f"连续任务已暂停等待确认。{delete_reason}{confirm_part}"

        if status == "success":
            return f"连续任务已完成，共记录 {len(timeline)} 个 timeline 节点。{delete_reason}"

        if status == "skipped":
            return f"连续任务已按条件跳过部分步骤：{reason or '条件未满足'}。{delete_reason}"

        if status == "aborted":
            return f"连续任务已中止：{reason or '前置步骤未成功'}。{delete_reason}"

        if status == "refused":
            return f"连续任务被策略拒绝：{reason or 'policy denied this request'}。{delete_reason}"

        if status == "failed":
            return f"连续任务执行失败：{reason or '工具调用失败'}。{delete_reason}"

        return f"连续任务状态：{status}。{delete_reason}"

    def build_explanation_card(
        self,
        *,
        parsed_intent: ParsedIntent | Mapping[str, Any],
        environment: Mapping[str, Any] | BaseModel | None,
        risk: PolicyDecision | Mapping[str, Any],
        plan: Mapping[str, Any] | BaseModel,
        execution: Mapping[str, Any] | BaseModel,
        result: Mapping[str, Any] | BaseModel,
        evidence_chain: EvidenceChain | Mapping[str, Any],
        recovery: Mapping[str, Any] | None = None,
        legacy_explanation: str | None = None,
        timeline: list[dict[str, Any]] | None = None,
    ) -> ExplanationCard:
        parsed = _as_dict(parsed_intent)
        risk_data = _as_dict(risk)
        plan_data = _as_dict(plan)
        execution_data = _as_dict(execution)
        result_data = _as_dict(result)
        environment_data = _as_dict(environment)
        evidence = _as_evidence_chain(evidence_chain)
        recovery_data = _as_dict(recovery)
        timeline = [item for item in (timeline or []) if isinstance(item, dict)]

        parse_refs = _event_ids(evidence, EvidenceStage.PARSE)
        plan_refs = _event_ids(evidence, EvidenceStage.PLAN)
        policy_refs = _event_ids(evidence, EvidenceStage.POLICY)
        confirmation_refs = _event_ids(evidence, EvidenceStage.CONFIRMATION)
        tool_refs = _event_ids(evidence, EvidenceStage.TOOL_CALL)
        post_check_refs = _event_ids(evidence, EvidenceStage.POST_CHECK)
        recovery_refs = _event_ids(evidence, EvidenceStage.RECOVERY)
        result_refs = _event_ids(evidence, EvidenceStage.RESULT)

        confirmation_assertion = _assertion_by_name(evidence, "confirmation_state")
        blocked_assertion = _assertion_by_name(evidence, "blocked_request_tool_suppression")
        post_check_assertion = _assertion_by_name(evidence, "post_check_state")
        outcome_assertion = _assertion_by_name(evidence, "final_outcome")

        card = ExplanationCard(
            intent_normalized=ExplanationSection(
                summary=_intent_section_summary(parsed),
                evidence_refs=parse_refs,
            ),
            plan_summary=ExplanationSection(
                summary=_plan_section_summary(plan_data),
                evidence_refs=plan_refs,
            ),
            risk_hits=ExplanationSection(
                summary=_risk_section_summary(risk_data),
                evidence_refs=policy_refs,
            ),
            scope_preview=ExplanationSection(
                summary=_scope_section_summary(parsed, plan_data),
                evidence_refs=plan_refs,
            ),
            confirmation_basis=ExplanationSection(
                summary=_confirmation_section_summary(
                    risk_data=risk_data,
                    plan_data=plan_data,
                    execution_data=execution_data,
                    result_data=result_data,
                    timeline=timeline,
                    confirmation_assertion=confirmation_assertion,
                ),
                evidence_refs=_merge_refs(
                    confirmation_refs,
                    _assertion_refs(confirmation_assertion),
                ),
            ),
            execution_evidence=ExplanationSection(
                summary=_execution_section_summary(
                    legacy_explanation=legacy_explanation,
                    execution_data=execution_data,
                    blocked_assertion=blocked_assertion,
                ),
                evidence_refs=_merge_refs(
                    tool_refs,
                    post_check_refs,
                    _assertion_refs(blocked_assertion),
                    confirmation_refs,
                    plan_refs,
                ),
            ),
            result_assertion=ExplanationSection(
                summary=_result_section_summary(
                    result_data=result_data,
                    outcome_assertion=outcome_assertion,
                    post_check_assertion=post_check_assertion,
                ),
                evidence_refs=_merge_refs(
                    result_refs,
                    _assertion_refs(outcome_assertion),
                    _assertion_refs(post_check_assertion),
                ),
            ),
            residual_risks_or_next_step=ExplanationSection(
                summary=_residual_section_summary(
                    risk_data=risk_data,
                    result_data=result_data,
                    plan_data=plan_data,
                    recovery_data=recovery_data,
                ),
                evidence_refs=_merge_refs(policy_refs, result_refs, recovery_refs),
            ),
        )
        return card

    def render_explanation_card(
        self,
        card: ExplanationCard | Mapping[str, Any],
        *,
        fallback: str | None = None,
    ) -> str:
        payload = _as_dict(card)
        sections = [
            _as_dict(payload.get("intent_normalized")),
            _as_dict(payload.get("plan_summary")),
            _as_dict(payload.get("risk_hits")),
            _as_dict(payload.get("scope_preview")),
            _as_dict(payload.get("confirmation_basis")),
            _as_dict(payload.get("execution_evidence")),
            _as_dict(payload.get("result_assertion")),
            _as_dict(payload.get("residual_risks_or_next_step")),
        ]
        evidence_refs = _merge_refs(*[section.get("evidence_refs") for section in sections])
        if fallback:
            if not evidence_refs:
                return fallback
            return f"{fallback} [evidence: {', '.join(evidence_refs)}]"

        parts: list[str] = []
        for section in sections:
            summary = str(section.get("summary") or "").strip()
            refs = _merge_refs(section.get("evidence_refs"))
            if not summary:
                continue
            if refs:
                parts.append(f"{summary} [evidence: {', '.join(refs)}]")
            else:
                parts.append(summary)
        return "\n".join(parts)


def summarize_readonly_result(
    parsed_intent: ParsedIntent,
    *,
    status: str,
    tool_result: ToolResult | None = None,
    reason: str | None = None,
    risk: PolicyDecision | None = None,
) -> str:
    return ReadonlySummarizer().summarize(
        parsed_intent,
        status=status,
        tool_result=tool_result,
        reason=reason,
        risk=risk,
    )


def _intent_section_summary(parsed: dict[str, Any]) -> str:
    intent_name = str(parsed.get("intent") or "unknown")
    target = _as_dict(parsed.get("target"))
    target_parts: list[str] = []
    for key in ("username", "path", "port", "pid", "keyword"):
        value = target.get(key)
        if value not in (None, "", []):
            target_parts.append(f"{key}={value}")
    base_paths = target.get("base_paths")
    if isinstance(base_paths, list) and base_paths:
        target_parts.append(f"base_paths={base_paths}")
    requires_write = bool(parsed.get("requires_write", False))
    target_text = "；目标：" + "，".join(target_parts) if target_parts else ""
    return f"归一化意图：{intent_name}{target_text}；requires_write={requires_write}。"


def _plan_section_summary(plan_data: dict[str, Any]) -> str:
    status = str(plan_data.get("status") or "unknown")
    steps = _step_labels(plan_data)
    if steps:
        return f"计划状态：{status}；步骤预览：{' -> '.join(steps)}。"
    reason = plan_data.get("reason")
    if reason:
        return f"计划状态：{status}；原因：{reason}。"
    return f"计划状态：{status}。"


def _risk_section_summary(risk_data: dict[str, Any]) -> str:
    level = str(risk_data.get("risk_level") or "unknown")
    reasons = [str(item) for item in _as_list(risk_data.get("reasons")) if str(item).strip()]
    base = f"风险等级：{level}。"
    if reasons:
        return f"{base} 命中原因：{'；'.join(reasons)}。"
    if risk_data.get("allow") is True:
        return f"{base} 当前未命中额外阻断规则。"
    return f"{base} 当前请求未通过策略。"


def _scope_section_summary(parsed: dict[str, Any], plan_data: dict[str, Any]) -> str:
    steps = _step_labels(plan_data)
    if steps:
        scope = f"执行范围：{' -> '.join(steps)}。"
    else:
        scope = "执行范围：当前没有可执行步骤。"

    target = _as_dict(parsed.get("target"))
    path = target.get("path")
    port = target.get("port")
    username = target.get("username")
    extra: list[str] = []
    if isinstance(path, str) and path:
        extra.append(f"path={path}")
    if port is not None:
        extra.append(f"port={port}")
    if isinstance(username, str) and username:
        extra.append(f"username={username}")
    if extra:
        scope = f"{scope} 边界参数：{'，'.join(extra)}。"
    return scope


def _confirmation_section_summary(
    *,
    risk_data: dict[str, Any],
    plan_data: dict[str, Any],
    execution_data: dict[str, Any],
    result_data: dict[str, Any],
    timeline: list[dict[str, Any]],
    confirmation_assertion: dict[str, Any] | None,
) -> str:
    confirmation_status = _confirmation_status(
        risk_data=risk_data,
        plan_data=plan_data,
        execution_data=execution_data,
        result_data=result_data,
        timeline=timeline,
    )
    confirmation_text = result_data.get("confirmation_text") or risk_data.get("confirmation_text")
    risk_level = str(risk_data.get("risk_level") or "S0")

    if confirmation_status == "pending":
        return (
            f"确认依据：该请求为 {risk_level}，当前仍待确认。"
            f"{_confirmation_text_suffix(confirmation_text)}"
        )
    if confirmation_status == "confirmed":
        return f"确认依据：所需确认已满足，执行闭环可以继续。"
    if confirmation_status == "mismatch":
        return (
            "确认依据：确认语不匹配，系统继续保持待确认状态。"
            f"{_confirmation_text_suffix(confirmation_text)}"
        )
    if confirmation_status == "cancelled":
        return "确认依据：待确认操作已被用户取消。"

    if confirmation_assertion is not None:
        return str(confirmation_assertion.get("summary") or "确认依据：当前请求无需二次确认。")
    return "确认依据：当前请求无需二次确认。"


def _execution_section_summary(
    *,
    legacy_explanation: str | None,
    execution_data: dict[str, Any],
    blocked_assertion: dict[str, Any] | None,
) -> str:
    results = [item for item in _as_list(execution_data.get("results")) if isinstance(item, dict)]
    if results:
        successes = sum(1 for item in results if item.get("success") is True)
        tool_names = [str(item.get("tool_name") or "unknown") for item in results]
        prefix = (
            f"执行证据：共记录 {len(results)} 次白名单工具调用，成功 {successes} 次；"
            f"工具链路：{' -> '.join(tool_names)}。"
        )
        if legacy_explanation:
            return f"{prefix} 摘要：{legacy_explanation}"
        return prefix

    if blocked_assertion is not None:
        return f"执行证据：{blocked_assertion.get('summary')}"

    if legacy_explanation:
        return f"执行证据：{legacy_explanation}"
    return "执行证据：当前没有工具调用记录。"


def _result_section_summary(
    *,
    result_data: dict[str, Any],
    outcome_assertion: dict[str, Any] | None,
    post_check_assertion: dict[str, Any] | None,
) -> str:
    parts: list[str] = []
    if outcome_assertion is not None:
        parts.append(str(outcome_assertion.get("summary") or "").strip())
    else:
        parts.append(f"最终状态：{result_data.get('status') or 'unknown'}。")

    if post_check_assertion is not None:
        parts.append(str(post_check_assertion.get("summary") or "").strip())
    return " ".join(part for part in parts if part)


def _residual_section_summary(
    *,
    risk_data: dict[str, Any],
    result_data: dict[str, Any],
    plan_data: dict[str, Any],
    recovery_data: dict[str, Any],
) -> str:
    if recovery_data:
        failure_type = str(recovery_data.get("failure_type") or "unknown")
        why = str(recovery_data.get("why_it_failed") or "").strip()
        safe_next_steps = [
            str(item).strip()
            for item in _as_list(recovery_data.get("safe_next_steps"))
            if isinstance(item, str) and item.strip()
        ]
        readonly_diagnostics = [
            str(item).strip()
            for item in _as_list(recovery_data.get("suggested_readonly_diagnostics"))
            if isinstance(item, str) and item.strip()
        ]
        parts = [f"Recovery: {failure_type}."]
        if why:
            parts.append(f"Why: {why}")
        if safe_next_steps:
            parts.append(f"Next: {' '.join(safe_next_steps[:2])}")
        if readonly_diagnostics:
            parts.append(f"Read-only diagnostics: {' '.join(readonly_diagnostics[:2])}")
        return " ".join(parts)

    status = str(result_data.get("status") or plan_data.get("status") or "unknown")
    confirmation_text = result_data.get("confirmation_text") or risk_data.get("confirmation_text")
    safe_alternative = risk_data.get("safe_alternative")
    error = result_data.get("error")

    if status == "pending_confirmation":
        return f"下一步：输入精确确认语继续。{_confirmation_text_suffix(confirmation_text)}"
    if status == "refused":
        if safe_alternative:
            return f"残余风险/下一步：建议改为安全替代方案：{_translate_safe_alternative(str(safe_alternative))}"
        return "残余风险/下一步：当前请求已被拒绝，建议收敛到只读或更小范围。"
    if status in {"failed", "aborted"}:
        if error:
            return f"残余风险/下一步：需排查失败原因：{error}"
        return "残余风险/下一步：需检查失败步骤并重新验证。"
    if status == "cancelled":
        return "下一步：待确认操作已取消，如需继续请重新发起请求。"
    return "残余风险/下一步：当前请求已完成，可直接使用 evidence_chain 做审计、回放或回归比对。"


def _confirmation_text_suffix(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return f" 精确确认语：{value.strip()}。"
    return ""


def _step_labels(plan_data: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for step in _as_list(plan_data.get("steps")):
        item = _as_dict(step)
        label = item.get("tool_name") or item.get("intent") or item.get("step_id")
        if label:
            labels.append(str(label))
    return labels


def _confirmation_status(
    *,
    risk_data: dict[str, Any],
    plan_data: dict[str, Any],
    execution_data: dict[str, Any],
    result_data: dict[str, Any],
    timeline: list[dict[str, Any]],
) -> str:
    plan_status = str(plan_data.get("status") or "").lower()
    result_status = str(result_data.get("status") or "").lower()
    result_error = str(result_data.get("error") or "").lower()
    execution_results = _as_list(execution_data.get("results"))

    if result_error == "confirmation_text_mismatch":
        return "mismatch"
    if result_status == "cancelled" or plan_status == "cancelled":
        return "cancelled"
    if result_status == "pending_confirmation" or plan_status == "pending_confirmation":
        return "pending"
    if plan_status == "confirmed":
        return "confirmed"
    if any(str(item.get("status") or "").lower() == "pending_confirmation" for item in timeline):
        return "pending"
    if bool(risk_data.get("requires_confirmation")) and execution_results:
        return "confirmed"
    return "not_required"


def _event_ids(evidence: EvidenceChain, stage: EvidenceStage) -> list[str]:
    return [event.event_id for event in evidence.events if event.stage == stage]


def _assertion_by_name(evidence: EvidenceChain, name: str) -> dict[str, Any] | None:
    for assertion in evidence.state_assertions:
        if assertion.name == name:
            return assertion.model_dump(mode="json")
    return None


def _assertion_refs(assertion: dict[str, Any] | None) -> list[str]:
    if assertion is None:
        return []
    assertion_id = assertion.get("assertion_id")
    return [str(assertion_id)] if isinstance(assertion_id, str) and assertion_id else []


def _as_evidence_chain(value: EvidenceChain | Mapping[str, Any]) -> EvidenceChain:
    if isinstance(value, EvidenceChain):
        return value
    if isinstance(value, Mapping):
        return EvidenceChain.model_validate(value)
    return EvidenceChain()


def _summarize_s3_refusal(
    parsed_intent: ParsedIntent,
    risk: PolicyDecision,
    reason: str | None,
) -> str:
    del parsed_intent

    reasons = risk.reasons or ([reason] if reason else ["策略引擎拒绝该请求"])
    reason_text = "；".join(_translate_policy_reason(item) for item in reasons if item)
    alternative = _translate_safe_alternative(risk.safe_alternative)
    return (
        "拒绝执行：该请求被策略引擎判定为禁止执行的高风险操作。"
        f"风险等级：{risk.risk_level.value}。"
        f"具体原因：{reason_text}。"
        f"安全替代方案：{alternative}"
        "未执行任何工具。"
    )


def _translate_policy_reason(reason: str) -> str:
    lower_reason = reason.lower()
    if "sudoers" in lower_reason:
        return "请求涉及修改 sudoers，可能授予不受控的管理员权限"
    if "sshd_config" in lower_reason or "ssh daemon" in lower_reason:
        return "请求涉及修改 sshd_config，可能削弱或中断远程登录安全"
    if "sudo, wheel, admin, or root" in lower_reason or "non-privileged" in lower_reason:
        return "请求会授予 sudo、wheel、admin 或 root 等特权，超出普通用户操作范围"
    if "bulk chmod/chown" in lower_reason or "permission changes" in lower_reason:
        return "请求涉及批量或递归 chmod/chown，可能影响大量文件权限"
    if "/etc" in lower_reason:
        return "请求会删除或破坏 /etc 下的核心系统配置"
    if "protected system path" in lower_reason or "core directories" in lower_reason:
        return "请求目标是受保护的系统核心目录"
    if "unknown or unsupported write operation" in lower_reason or "unknown writes" in lower_reason:
        return "当前只支持只读基础能力，未知写操作默认拒绝"
    return reason


def _translate_safe_alternative(safe_alternative: str | None) -> str:
    if not safe_alternative:
        return "改为只读盘点或收窄范围后查看候选对象，不执行修改。"

    lower_alternative = safe_alternative.lower()
    if "sudo-related" in lower_alternative:
        return "只读查看 sudo 相关配置或用户组现状，整理变更清单交由管理员人工审核。"
    if "ssh configuration" in lower_alternative:
        return "只读查看 SSH 配置和当前登录策略，形成手工审查计划，不自动修改配置。"
    if "non-privileged user" in lower_alternative:
        return "只读查看用户和组成员关系；如需新增用户，仅创建普通非特权用户，不加入 sudo/wheel/admin。"
    if "permission" in lower_alternative:
        return "先在小范围目录内只读列出权限现状和候选文件，不执行 chmod/chown。"
    if "non-core application path" in lower_alternative:
        return "收窄到非核心业务目录，先只读列出候选文件并盘点影响范围，不执行删除或修改。"
    if "base_path" in lower_alternative or "specific non-virtual directory" in lower_alternative:
        return "提供更窄的只读搜索路径，并限制 max_depth 和 max_results。"
    if "read-only" in lower_alternative or "whitelisted" in lower_alternative:
        return "改为受支持的只读查询，或明确收窄为只读盘点。"
    return "改为只读盘点或收窄范围后查看候选对象，不执行修改。"


def _summarize_disk(data: Any) -> str:
    filesystems = list((data or {}).get("filesystems") or [])
    if not filesystems:
        return "已查询磁盘使用情况，但没有返回挂载点信息。"

    tightest = max(filesystems, key=lambda item: _percent_value(item.get("use_percent")))
    return (
        f"当前共检测到 {len(filesystems)} 个挂载点；"
        f"最紧张的是 {tightest.get('mounted_on', '未知挂载点')}，"
        f"使用率 {tightest.get('use_percent', '未知')}，"
        f"可用空间 {tightest.get('available', '未知')}。"
    )


def _summarize_file_search(data: Any) -> str:
    payload = data or {}
    count = payload.get("count", 0)
    base_path = payload.get("base_path", "指定目录")
    truncated = "结果已截断" if payload.get("truncated") else "结果未截断"
    keyword = payload.get("name_contains")
    keyword_text = f"，文件名包含 {keyword}" if keyword else ""
    return f"已在 {base_path} 中完成文件检索{keyword_text}，返回 {count} 条结果，{truncated}。"


def _summarize_process(data: Any) -> str:
    payload = data or {}
    processes = list(payload.get("processes") or [])
    mode = payload.get("mode", "cpu")
    mode_text = {
        "cpu": "CPU 占用",
        "memory": "内存占用",
        "keyword": "关键字匹配",
        "pid": "PID",
    }.get(str(mode), str(mode))
    if not processes:
        return f"已完成 {mode_text} 进程查询，没有返回匹配进程。"

    first = processes[0]
    return (
        f"已完成 {mode_text} 进程查询，返回 {len(processes)} 个进程；"
        f"首条为 PID {first.get('pid')}，进程 {first.get('command')}，"
        f"用户 {first.get('user')}。"
    )


def _summarize_port(data: Any) -> str:
    payload = data or {}
    port = payload.get("port")
    listeners = list(payload.get("listeners") or [])
    if not listeners:
        return f"端口 {port} 当前没有监听记录。"

    first = listeners[0]
    return (
        f"端口 {port} 当前正在监听；"
        f"进程 {first.get('process_name') or '未知'}，"
        f"PID {first.get('pid') or '未知'}，"
        f"用户 {first.get('user') or '未知'}。"
    )


def _is_port_query_unsupported_environment(
    parsed_intent: ParsedIntent,
    tool_result: ToolResult | None,
) -> bool:
    if parsed_intent.intent != PORT_INTENT or tool_result is None:
        return False
    payload = tool_result.data if isinstance(tool_result.data, Mapping) else {}
    return payload.get("status") == "unsupported_on_current_environment"


def _percent_value(value: Any) -> int:
    text = str(value or "").strip().rstrip("%")
    try:
        return int(text)
    except ValueError:
        return -1


def _as_dict(value: Any) -> dict[str, Any]:
    plain = _to_plain(value)
    return plain if isinstance(plain, dict) else {}


def _as_list(value: Any) -> list[Any]:
    plain = _to_plain(value)
    if plain is None:
        return []
    if isinstance(plain, list):
        return plain
    if isinstance(plain, tuple):
        return list(plain)
    return [plain]


def _to_plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _to_plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    if isinstance(value, tuple):
        return [_to_plain(item) for item in value]
    return value


def _merge_refs(*groups: Any) -> list[str]:
    refs: list[str] = []
    for group in groups:
        for item in _as_list(group):
            if isinstance(item, str) and item.strip() and item not in refs:
                refs.append(item)
    return refs
