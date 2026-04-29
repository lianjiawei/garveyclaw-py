from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class AgentImage:
    """Agent 返回给 Telegram 的图片内容，默认只在内存里流转。"""

    data: bytes
    mime_type: str = "image/png"
    caption: str | None = None


@dataclass(slots=True)
class AgentReply:
    """统一的 Agent 返回结构，兼容纯文本和图片结果。"""

    text: str = ""
    images: list[AgentImage] = field(default_factory=list)

    @classmethod
    def from_text(cls, text: str) -> "AgentReply":
        return cls(text=text)
