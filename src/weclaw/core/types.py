from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConversationRef:
    channel: str
    target_id: str
    session_scope: str
    user_id: str | None = None

    @property
    def conversation_key(self) -> str:
        return build_conversation_key(self.channel, self.target_id)


def build_conversation_key(channel: str, target_id: str) -> str:
    return f"{channel}:{target_id}"
