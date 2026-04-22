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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="GuardedOps 本地只读调试入口。",
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
    parser = build_parser()
    args = parser.parse_args(argv)
    raw_user_input = " ".join(args.request).strip()

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
        return 1

    if args.json_output:
        print(json.dumps(response, ensure_ascii=False, indent=2, default=str))
    else:
        print(response.get("explanation") or "请求已处理，但没有返回摘要。")

    return 0 if _is_success(response) else 1


def _is_success(response: dict[str, Any]) -> bool:
    result = response.get("result") or {}
    return result.get("status") == "success"


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
