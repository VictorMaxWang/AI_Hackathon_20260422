from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.chat import (
    SessionRegistry,
    Utf8JSONResponse,
    build_internal_error_envelope,
    build_validation_error_payload,
    router as chat_router,
)
from app.config import load_config


BASE_DIR = Path(__file__).resolve().parent
UI_DIR = BASE_DIR / "ui"
UI_INDEX_FILE = UI_DIR / "index.html"
UI_UNAVAILABLE_MESSAGE = (
    "Web 控制面静态资源缺失（安装包未包含 app/ui），API 仍然可用："
    "请改用 POST /api/chat 或 GET /health。"
)

APP_VERSION = "0.1.0"
APP_SUMMARY = "安全优先的自然语言 Linux 运维代理：安全边界在代码里，不在提示词里。"
APP_DESCRIPTION = (
    "GuardedOps 把自然语言请求转成结构化 intent，交由策略引擎判定风险等级"
    "（S0 只读放行 / S1-S2 需要精确确认 / S3 拒绝），"
    "只允许白名单工具执行，并为每一次请求返回可审计的证据链。\n\n"
    "**永远没有任意 shell。** 被拒绝是期望结果，不是故障。"
)
OPENAPI_TAGS = [
    {"name": "chat", "description": "自然语言运维请求入口，返回带证据链的统一信封。"},
    {"name": "ops", "description": "运行时自检：版本、策略指纹与 LLM 边界开关。"},
]

CORRELATION_ID_HEADER = "X-Correlation-Id"
GZIP_MINIMUM_SIZE = 1000

LOGGER = logging.getLogger("guardedops.api")


def create_app() -> FastAPI:
    application = FastAPI(
        title="GuardedOps",
        version=APP_VERSION,
        summary=APP_SUMMARY,
        description=APP_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
    )
    application.state.chat_sessions = SessionRegistry()
    application.add_middleware(GZipMiddleware, minimum_size=GZIP_MINIMUM_SIZE)
    application.include_router(chat_router)
    if UI_DIR.is_dir():
        application.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")
    else:
        LOGGER.warning(
            "UI directory missing, serving the API without the operator panel path=%s",
            UI_DIR,
        )

    @application.get("/", include_in_schema=False)
    def index() -> Response:
        if not UI_INDEX_FILE.is_file():
            LOGGER.warning(
                "UI index missing, degraded to an API-only response path=%s",
                UI_INDEX_FILE,
            )
            return Utf8JSONResponse(
                status_code=503,
                content={"detail": UI_UNAVAILABLE_MESSAGE},
            )
        return FileResponse(UI_INDEX_FILE)

    @application.get(
        "/health",
        response_class=Utf8JSONResponse,
        tags=["ops"],
        summary="运行时自检",
        description=(
            "返回服务版本、当前策略文件指纹与 LLM 边界开关。"
            "只报告 DASHSCOPE_API_KEY 是否存在，绝不返回密钥本身。"
        ),
        response_description="服务状态、版本、策略指纹与 LLM 边界开关",
    )
    def health() -> dict[str, Any]:
        config = load_config()
        return {
            "status": "ok",
            "version": APP_VERSION,
            "policy_version": _policy_version(),
            "llm_enable": config.llm_enable,
            "llm_allow_write_intents": config.llm_allow_write_intents,
            "dashscope_api_key_present": config.dashscope_api_key_present,
        }

    @application.exception_handler(RequestValidationError)
    async def invalid_request(
        request: Request,
        exc: RequestValidationError,
    ) -> Utf8JSONResponse:
        del request
        return Utf8JSONResponse(
            status_code=422,
            content=build_validation_error_payload(exc.errors()),
        )

    @application.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> Utf8JSONResponse:
        correlation_id = uuid.uuid4().hex
        LOGGER.exception(
            "unhandled request error correlation_id=%s method=%s path=%s",
            correlation_id,
            request.method,
            request.url.path,
            exc_info=exc,
        )
        envelope = build_internal_error_envelope(
            correlation_id=correlation_id,
            raw_user_input=_state_text(request, "raw_user_input"),
            path=request.url.path,
        )
        return Utf8JSONResponse(
            status_code=500,
            content=envelope,
            headers={CORRELATION_ID_HEADER: correlation_id},
        )

    return application


def _policy_version() -> str:
    try:
        from app.agent.orchestrator import _current_policy_version

        return _current_policy_version()
    except Exception:  # pragma: no cover - health must never crash the process
        return "unknown"


def _state_text(request: Request, name: str) -> str:
    value = getattr(request.state, name, "")
    return value if isinstance(value, str) else ""


app = create_app()
