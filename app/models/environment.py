from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EnvironmentSnapshot(BaseModel):
    """Read-only system context captured before policy and execution steps."""

    model_config = ConfigDict(extra="forbid")

    hostname: str = "unknown"
    distro: str = "unknown"
    kernel: str = "unknown"
    current_user: str = "unknown"
    is_root: bool = False
    sudo_available: bool = False
    available_commands: list[str] = Field(default_factory=list)
    connection_mode: str = "unknown"
