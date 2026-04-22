from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.chat import router as chat_router


BASE_DIR = Path(__file__).resolve().parent
UI_DIR = BASE_DIR / "ui"


def create_app() -> FastAPI:
    application = FastAPI(title="GuardedOps", version="0.1.0")
    application.include_router(chat_router)
    application.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(UI_DIR / "index.html")

    return application


app = create_app()
