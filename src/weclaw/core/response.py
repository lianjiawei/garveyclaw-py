from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class AgentImage:
    """Agent 返回给通道的图片内容。"""

    data: bytes
    mime_type: str = "image/png"
    caption: str | None = None


@dataclass(slots=True)
class AgentFile:
    """Agent 返回给通道的文件内容。"""

    data: bytes
    file_name: str
    mime_type: str = "application/octet-stream"


@dataclass(slots=True)
class AgentReply:
    """统一的 Agent 返回结构，兼容纯文本、图片和文件。"""

    text: str = ""
    images: list[AgentImage] = field(default_factory=list)
    files: list[AgentFile] = field(default_factory=list)
    provider: str = ""

    @classmethod
    def from_text(cls, text: str, provider: str = "") -> "AgentReply":
        return cls(text=text, provider=provider)
