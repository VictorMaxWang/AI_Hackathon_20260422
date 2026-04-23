from __future__ import annotations

from dataclasses import dataclass

from app.agent.confirmation import PendingAction


@dataclass
class AgentMemory:
    """Minimal session memory for one pending confirmation action."""

    pending_action: PendingAction | None = None

    def set_pending_action(self, action: PendingAction) -> None:
        self.pending_action = action

    def clear_pending_action(self) -> PendingAction | None:
        action = self.pending_action
        self.pending_action = None
        return action
