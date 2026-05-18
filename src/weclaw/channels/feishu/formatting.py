from __future__ import annotations

import html
import re

IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
LINK_PATTERN = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
HEADING_PATTERN = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
ITALIC_PATTERN = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
EXCESS_BLANK_LINE = re.compile(r"\n{3,}")
MAX_LARK_MD_CHARS = 8000


def markdown_to_lark_md(text: str) -> str:
    """把标准 Markdown 转为飞书交互卡片可渲染的 lark_md 标签。

    飞书卡片的 ``markdown`` 元素不支持 ``#`` 标题语法和表格，本函数会把标题
    转为加粗文本，链接使用 ``<a>`` 保证渲染稳定。斜体用 ``*`` 包裹在飞书里容易
    误触发（列表项、乘号等），故直接移除斜体标记，保留文字本身。
    """

    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return ""

    normalized = HEADING_PATTERN.sub(lambda m: f"**{m.group(1).strip()}**", normalized)

    normalized = LINK_PATTERN.sub(lambda m: f'<a href="{m.group(2)}">{_escape_link_text(m.group(1))}</a>', normalized)

    normalized = IMAGE_PATTERN.sub(lambda m: f"[图片: {_escape_link_text(m.group(1) or '无描述')}]", normalized)

    normalized = ITALIC_PATTERN.sub(lambda m: m.group(1), normalized)

    normalized = EXCESS_BLANK_LINE.sub("\n\n", normalized)

    if len(normalized) > MAX_LARK_MD_CHARS:
        normalized = normalized[:MAX_LARK_MD_CHARS] + "\n\n…(内容过长已截断)"

    return normalized


def _escape_link_text(s: str) -> str:
    """对链接/图片的展示文本做 HTML 转义，避免模型输出中包含 ``<`` 字符时破坏卡片 JSON。"""
    return html.escape(s, quote=False)


# 保留旧名称作为兼容别名
def format_feishu_text(text: str) -> str:
    return markdown_to_lark_md(text)
