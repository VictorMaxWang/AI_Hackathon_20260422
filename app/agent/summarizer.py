from __future__ import annotations

from typing import Any

from app.models import ParsedIntent, PolicyDecision, RiskLevel, ToolResult

from app.agent.parser import DISK_INTENT, FILE_INTENT, PORT_INTENT, PROCESS_INTENT


class ReadonlySummarizer:
    """Chinese summaries for Phase 1 read-only tool results."""

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
        f"当前共检测到 {len(filesystems)} 个挂载点，"
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
    return (
        f"已在 {base_path} 中完成文件检索{keyword_text}，"
        f"返回 {count} 条结果，{truncated}。"
    )


def _summarize_process(data: Any) -> str:
    payload = data or {}
    processes = list(payload.get("processes") or [])
    mode = payload.get("mode", "cpu")
    mode_text = {
        "cpu": "CPU 占用",
        "memory": "内存占用",
        "keyword": "关键词匹配",
        "pid": "PID",
    }.get(str(mode), str(mode))
    if not processes:
        return f"已完成{mode_text}进程查询，没有返回匹配进程。"

    first = processes[0]
    return (
        f"已完成{mode_text}进程查询，返回 {len(processes)} 个进程；"
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


def _percent_value(value: Any) -> int:
    text = str(value or "").strip().rstrip("%")
    try:
        return int(text)
    except ValueError:
        return -1
