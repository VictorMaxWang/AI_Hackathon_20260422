from __future__ import annotations

import json
import re
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.models.evolution import WorkflowTemplate


DEFAULT_TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "workflows" / "templates"

BANNED_TOOL_NAMES = frozenset(
    {
        "run_shell_tool",
        "execute_command_tool",
        "bash_tool",
        "shell_tool",
        "raw_shell_tool",
        "command_tool",
    }
)
BANNED_RAW_COMMAND_FIELDS = frozenset(
    {
        "argv",
        "bash",
        "cmd",
        "command",
        "commands",
        "executable",
        "raw_command",
        "raw_shell",
        "script",
        "shell",
    }
)
RAW_COMMAND_TEXT_RE = re.compile(
    r"(?i)(?:^|\s)(?:"
    r"\$ |# |rm\s+-|chmod\s+[0-7]|chown\s+\S|useradd\s+\S|userdel\s+\S|"
    r"kill\s+(?:-\d+|\d+)|systemctl\s+(?:restart|start|stop)|"
    r"iptables\s+-|ufw\s+\S|netsh\s+\S|bash\s+-c|sh\s+-c|"
    r"powershell\s+-command|cmd\s+/c|&&|\|\|"
    r")"
)
GENERIC_MATCH_TAGS = frozenset(
    {
        "bounded",
        "confirmation",
        "evo-lite",
        "policy-gated",
        "process",
        "readonly",
        "safe-workflow",
    }
)
WORKFLOW_MATCH_TERMS = {
    "safe_disk_triage": (
        "disk",
        "disk triage",
        "disk usage",
        "storage",
        "mount",
    ),
    "safe_file_search": (
        "file search",
        "search files",
        "find files",
        "log",
        "logs",
    ),
    "diagnose_port_owner": (
        "port",
        "port owner",
        "port usage",
        "listener",
        "listening",
        "owning process",
    ),
    "safe_user_lifecycle": (
        "user lifecycle",
        "create user",
        "delete user",
        "add user",
        "remove user",
        "normal user",
    ),
}


class WorkflowTemplateLoadError(ValueError):
    """Raised when a declarative workflow template cannot be loaded safely."""


def load_workflow_template(
    workflow_id: str,
    templates_dir: str | Path | None = None,
) -> WorkflowTemplate:
    """Load and validate a single workflow template by id."""

    if not isinstance(workflow_id, str) or not workflow_id.strip():
        raise WorkflowTemplateLoadError("workflow_id must be a non-empty string")

    template_path = _resolve_template_dir(templates_dir) / f"{workflow_id}.json"
    template = _load_template_file(template_path)
    if template.workflow_id != workflow_id:
        raise WorkflowTemplateLoadError(
            f"workflow template {template_path} declares workflow_id "
            f"{template.workflow_id!r}, expected {workflow_id!r}"
        )
    return template


def load_workflow_templates(
    templates_dir: str | Path | None = None,
) -> dict[str, WorkflowTemplate]:
    """Load all JSON workflow templates from the template directory."""

    template_dir = _resolve_template_dir(templates_dir)
    if not template_dir.exists():
        raise WorkflowTemplateLoadError(f"workflow template directory not found: {template_dir}")
    if not template_dir.is_dir():
        raise WorkflowTemplateLoadError(f"workflow template path is not a directory: {template_dir}")

    templates: dict[str, WorkflowTemplate] = {}
    for template_path in sorted(template_dir.glob("*.json")):
        template = _load_template_file(template_path)
        if template.workflow_id in templates:
            raise WorkflowTemplateLoadError(
                f"duplicate workflow_id {template.workflow_id!r} in {template_path}"
            )
        templates[template.workflow_id] = template

    if not templates:
        raise WorkflowTemplateLoadError(f"no workflow templates found in {template_dir}")
    return templates


def match_workflow_template(
    raw_user_input: str,
    templates_dir: str | Path | None = None,
) -> WorkflowTemplate | None:
    """Return the best safe workflow template for a natural-language request.

    Matching is deterministic and rule-based only. It intentionally avoids
    embeddings or model calls so workflow templates can only suggest planner
    steps, not create a new execution path.
    """

    text = _normalize_match_text(raw_user_input)
    if not text:
        return None

    templates = load_workflow_templates(templates_dir)
    exact_match = _match_exact_workflow_id(text, templates)
    if exact_match is not None:
        return exact_match

    ranked_matches: list[tuple[int, str, WorkflowTemplate]] = []
    for template in templates.values():
        score = _workflow_match_score(template, text)
        if score > 0:
            ranked_matches.append((score, template.workflow_id, template))

    if not ranked_matches:
        return None

    ranked_matches.sort(key=lambda item: (-item[0], item[1]))
    return ranked_matches[0][2]


def _resolve_template_dir(templates_dir: str | Path | None) -> Path:
    return Path(templates_dir) if templates_dir is not None else DEFAULT_TEMPLATE_DIR


def _normalize_match_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _match_exact_workflow_id(
    text: str,
    templates: dict[str, WorkflowTemplate],
) -> WorkflowTemplate | None:
    for workflow_id in sorted(templates):
        variants = {
            workflow_id.lower(),
            workflow_id.lower().replace("_", "-"),
            workflow_id.lower().replace("_", " "),
        }
        if any(_contains_term(text, variant) for variant in variants):
            return templates[workflow_id]
    return None


def _workflow_match_score(template: WorkflowTemplate, text: str) -> int:
    score = 0
    if _matches_chinese_intent(template.workflow_id, text):
        score += 50

    for term in _workflow_terms(template):
        if _contains_term(text, term):
            score += 10

    return score if score >= 10 else 0


def _workflow_terms(template: WorkflowTemplate) -> tuple[str, ...]:
    terms: list[str] = list(WORKFLOW_MATCH_TERMS.get(template.workflow_id, ()))
    terms.extend(
        tag
        for tag in template.tags
        if tag.strip().lower() not in GENERIC_MATCH_TAGS and tag.strip().lower() != "user"
    )
    return tuple(dict.fromkeys(term.strip().lower() for term in terms if term.strip()))


def _matches_chinese_intent(workflow_id: str, text: str) -> bool:
    if workflow_id == "safe_disk_triage":
        return "磁盘" in text and _contains_any(
            text,
            ["安全", "检查", "查看", "查询", "排查", "体检", "空间", "使用"],
        )

    if workflow_id == "safe_file_search":
        return _contains_any(text, ["文件", "目录", "日志", "log"]) and _contains_any(
            text,
            ["找", "查找", "搜索", "检索", "查"],
        )

    if workflow_id == "diagnose_port_owner":
        return "端口" in text and _contains_any(text, ["占用", "谁", "监听"])

    if workflow_id == "safe_user_lifecycle":
        has_create = _contains_any(text, ["创建", "新增", "添加"])
        has_delete = _contains_any(text, ["删除", "删掉", "移除"])
        return "用户" in text and has_create and has_delete

    return False


def _contains_any(text: str, needles: list[str]) -> bool:
    return any(needle.lower() in text for needle in needles)


def _contains_term(text: str, term: str) -> bool:
    normalized_term = term.strip().lower()
    if not normalized_term:
        return False

    if not normalized_term.isascii():
        return normalized_term in text

    variants = {
        normalized_term,
        normalized_term.replace("_", "-"),
        normalized_term.replace("_", " "),
        normalized_term.replace("-", "_"),
        normalized_term.replace("-", " "),
    }
    for variant in variants:
        if " " in variant or "_" in variant or "-" in variant:
            if variant in text:
                return True
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(variant)}(?![a-z0-9])", text):
            return True
    return False


def _load_template_file(template_path: Path) -> WorkflowTemplate:
    if not template_path.exists():
        raise WorkflowTemplateLoadError(f"workflow template not found: {template_path}")
    if not template_path.is_file():
        raise WorkflowTemplateLoadError(f"workflow template path is not a file: {template_path}")

    try:
        raw_text = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkflowTemplateLoadError(
            f"failed to read workflow template {template_path}: {exc}"
        ) from exc

    try:
        payload = json.loads(raw_text)
    except JSONDecodeError as exc:
        raise WorkflowTemplateLoadError(
            f"invalid JSON in workflow template {template_path}: "
            f"line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc

    if not isinstance(payload, dict):
        raise WorkflowTemplateLoadError(
            f"workflow template {template_path} must contain a JSON object"
        )

    _reject_raw_command_content(payload, source=str(template_path))

    try:
        template = WorkflowTemplate.model_validate(payload)
    except ValidationError as exc:
        raise WorkflowTemplateLoadError(
            f"invalid workflow template {template_path}: {exc}"
        ) from exc

    _reject_banned_tools(template, source=str(template_path))
    return template


def _reject_banned_tools(template: WorkflowTemplate, *, source: str) -> None:
    used_tools = set(template.allowed_tools)
    used_tools.update(step.tool_name for step in template.steps)
    banned = sorted(used_tools.intersection(BANNED_TOOL_NAMES))
    if banned:
        raise WorkflowTemplateLoadError(
            f"workflow template {source} declares forbidden tool names: {banned}"
        )


def _reject_raw_command_content(value: Any, *, source: str, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            normalized_key = str(key).strip().lower()
            if normalized_key in BANNED_RAW_COMMAND_FIELDS:
                raise WorkflowTemplateLoadError(
                    f"workflow template {source} contains raw command field at {path}.{key}"
                )
            _reject_raw_command_content(nested_value, source=source, path=f"{path}.{key}")
        return

    if isinstance(value, list):
        for index, nested_value in enumerate(value):
            _reject_raw_command_content(nested_value, source=source, path=f"{path}[{index}]")
        return

    if isinstance(value, str) and RAW_COMMAND_TEXT_RE.search(value):
        raise WorkflowTemplateLoadError(
            f"workflow template {source} contains command-like text at {path}"
        )
