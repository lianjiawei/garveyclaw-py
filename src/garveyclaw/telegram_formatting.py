import html
import re

from telegram.constants import ParseMode

CODE_BLOCK_PATTERN = re.compile(r"```(?:([\w#+.-]+)\n)?(.*?)```", re.DOTALL)
INLINE_CODE_PATTERN = re.compile(r"`([^`\n]+)`")
LINK_PATTERN = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
BLOCKQUOTE_PATTERN = re.compile(r"^>\s?(.*)$", re.MULTILINE)
BOLD_PATTERN = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
ITALIC_PATTERN = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", re.DOTALL)


def split_text_for_telegram(text: str, max_length: int = 3500) -> list[str]:
    # 尽量按段落切分，避免把一大段 Markdown 直接从中间截断。
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    current = ""

    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= max_length:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        while len(paragraph) > max_length:
            chunks.append(paragraph[:max_length])
            paragraph = paragraph[max_length:]

        current = paragraph

    if current:
        chunks.append(current)

    return chunks


def markdown_to_telegram_html(text: str) -> str:
    # Telegram 对标准 Markdown 支持并不完整，这里转成更稳定的 HTML 子集。
    placeholders: dict[str, str] = {}

    def store(value: str) -> str:
        key = f"__TG_PLACEHOLDER_{len(placeholders)}__"
        placeholders[key] = value
        return key

    def replace_code_block(match: re.Match[str]) -> str:
        code = html.escape(match.group(2).strip("\n"))
        return store(f"<pre>{code}</pre>")

    text = CODE_BLOCK_PATTERN.sub(replace_code_block, text)
    text = html.escape(text)

    text = HEADING_PATTERN.sub(lambda m: f"<b>{m.group(2).strip()}</b>", text)
    text = BLOCKQUOTE_PATTERN.sub(lambda m: f"&gt; {m.group(1)}", text)
    text = LINK_PATTERN.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', text)
    text = BOLD_PATTERN.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    text = ITALIC_PATTERN.sub(lambda m: f"<i>{m.group(1)}</i>", text)
    text = INLINE_CODE_PATTERN.sub(lambda m: store(f"<code>{html.escape(m.group(1))}</code>"), text)

    for key, value in placeholders.items():
        text = text.replace(key, value)

    return text


def format_telegram_text(text: str) -> list[dict[str, str]]:
    # 统一返回“消息文本 + parse_mode”，便于 handler 直接循环发送。
    return [
        {
            "text": markdown_to_telegram_html(chunk),
            "parse_mode": ParseMode.HTML,
        }
        for chunk in split_text_for_telegram(text)
    ]
