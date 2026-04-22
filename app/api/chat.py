from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.agent import run_readonly_request
from app.executors import BaseExecutor, LocalExecutor


router = APIRouter()


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_user_input: str = Field(min_length=1)


def get_executor() -> BaseExecutor:
    return LocalExecutor()


@router.post("/api/chat")
def chat(
    request: ChatRequest,
    executor: BaseExecutor = Depends(get_executor),
) -> dict[str, Any]:
    return run_readonly_request(executor, request.raw_user_input)
