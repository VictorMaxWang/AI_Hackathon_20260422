from __future__ import annotations

from typing import Any

from app.models import ParsedIntent, ToolResult

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
    ) -> str:
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
) -> str:
    return ReadonlySummarizer().summarize(
        parsed_intent,
        status=status,
        tool_result=tool_result,
        reason=reason,
    )


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
