from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.agent import ReadonlyOrchestrator
from app.executors import BaseExecutor, LocalExecutor


router = APIRouter()


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_user_input: str = Field(min_length=1)


def get_executor() -> BaseExecutor:
    return LocalExecutor()


def get_orchestrator(
    http_request: Request,
    executor: BaseExecutor = Depends(get_executor),
) -> ReadonlyOrchestrator:
    orchestrator = getattr(http_request.app.state, "chat_orchestrator", None)
    if orchestrator is None:
        orchestrator = ReadonlyOrchestrator(executor)
        http_request.app.state.chat_orchestrator = orchestrator
    return orchestrator


@router.post("/api/chat")
def chat(
    request: ChatRequest,
    orchestrator: ReadonlyOrchestrator = Depends(get_orchestrator),
) -> dict[str, Any]:
    return orchestrator.run(request.raw_user_input)
