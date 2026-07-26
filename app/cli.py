from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent import ReadonlyOrchestrator as ReadonlyOrchestratorType
    from app.executors import LocalExecutor as LocalExecutorType


ReadonlyOrchestrator: Any = None
LocalExecutor: Any = None


EXIT_SUCCESS = 0
EXIT_INTERNAL_FAILURE = 1
EXIT_USAGE = 2
EXIT_REFUSED_BY_POLICY = 3
EXIT_PENDING_CONFIRMATION = 4

STATUS_EXIT_CODES = {
    "success": EXIT_SUCCESS,
    "completed": EXIT_SUCCESS,
    "cancelled": EXIT_SUCCESS,
    "refused": EXIT_REFUSED_BY_POLICY,
    "unsupported": EXIT_REFUSED_BY_POLICY,
    "pending_confirmation": EXIT_PENDING_CONFIRMATION,
    "failed": EXIT_INTERNAL_FAILURE,
}

EXIT_CODE_EPILOG = (
    "退出码：0 成功（含已取消）；1 内部或工具失败；2 用法错误；"
    "3 被策略拒绝（期望结果）；4 等待精确确认。"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="GuardedOps 本地只读调试入口。",
        epilog=EXIT_CODE_EPILOG,
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="输出完整结构化 JSON 结果。",
    )
    parser.add_argument(
        "request",
        nargs="+",
        help="一条自然语言只读运维请求。",
    )
    return parser


def run_request(raw_user_input: str) -> dict[str, Any]:
    executor_cls, orchestrator_cls = _load_runtime()
    executor = executor_cls()
    return orchestrator_cls(executor).run(raw_user_input)


def main(argv: Sequence[str] | None = None) -> int:
    _force_utf8_streams()

    parser = build_parser()
    args = parser.parse_args(argv)
    raw_user_input = " ".join(args.request).strip()
    if not raw_user_input:
        parser.error("请求内容不能为空。")

    try:
        response = run_request(raw_user_input)
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        error_response = {
            "result": {"status": "failed", "data": None, "error": str(exc)},
            "explanation": f"CLI 调用失败：{exc}",
        }
        if args.json_output:
            print(json.dumps(error_response, ensure_ascii=False, indent=2, default=str))
        else:
            print(error_response["explanation"], file=sys.stderr)
        return EXIT_INTERNAL_FAILURE

    if args.json_output:
        print(json.dumps(response, ensure_ascii=False, indent=2, default=str))
    else:
        print(response.get("explanation") or "请求已处理，但没有返回摘要。")

    return exit_code_for(response)


def exit_code_for(response: dict[str, Any]) -> int:
    result = response.get("result") or {}
    status = str(result.get("status") or "").strip().lower()
    return STATUS_EXIT_CODES.get(status, EXIT_INTERNAL_FAILURE)


def _force_utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):  # pragma: no cover - stream refuses
            continue


def _load_runtime() -> tuple[type["LocalExecutorType"], type["ReadonlyOrchestratorType"]]:
    global LocalExecutor, ReadonlyOrchestrator

    if LocalExecutor is None:
        from app.executors import LocalExecutor as runtime_executor

        LocalExecutor = runtime_executor
    if ReadonlyOrchestrator is None:
        from app.agent import ReadonlyOrchestrator as runtime_orchestrator

        ReadonlyOrchestrator = runtime_orchestrator

    return LocalExecutor, ReadonlyOrchestrator


if __name__ == "__main__":
    raise SystemExit(main())
