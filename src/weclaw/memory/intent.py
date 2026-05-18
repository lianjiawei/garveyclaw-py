from __future__ import annotations

import random
import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MemoryIntent:
    category: str
    slot: str | None
    content: str
    confidence: str
    reason: str
    auto_accept: bool


INTENT_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(?:你要记得|你记住|你给我记住)[：:，,\s]*(?P<content>.+)$"), "explicit_remember"),
    (re.compile(r"^(?:帮我记住|帮我记一下|把这个记住|把这条记住|把这个记下来|把这条记下来)[：:，,\s]*(?P<content>.+)$"), "explicit_remember"),
    (re.compile(r"^(?:记住这个|记住这点|记一下这个|记下来)[：:，,\s]*(?P<content>.+)$"), "explicit_remember"),
    (re.compile(r"^(?:以后你要|下次你要|后面你要|之后你要)[：:，,\s]*(?P<content>.+)$"), "future_rule"),
    (re.compile(r"^(?:以后不要|下次不要|以后别再|下次别再)[：:，,\s]*(?P<content>.+)$"), "future_rule"),
    (re.compile(r"^(?:以后回答我时要|下次回答我时要|之后回答我时要)[：:，,\s]*(?P<content>.+)$"), "response_rule"),
    (re.compile(r"^(?:以后回答|下次回答|之后回答)[：:，,\s]*(?P<content>.+)$"), "response_style"),
    (re.compile(r"^(?:你可以叫我|以后叫我|下次叫我)[：:，,\s]*(?P<content>.+)$"), "addressing_user"),
    (re.compile(r"^(?:你可以叫自己|你以后叫|以后你叫|下次你叫)[：:，,\s]*(?P<content>.+)$"), "assistant_name"),
    (re.compile(r"^(?:以后都用|以后用|下次用)[：:，,\s]*(?P<content>.+)$"), "language_preference"),
    (re.compile(r"^(?:我喜欢用|我希望用|我更喜欢用|我喜欢你用|我希望你用|我更喜欢你用)[：:，,\s]*(?P<content>.+)$"), "preference_statement"),
)

LANGUAGE_PATTERN = re.compile(r"(中文|英文|日文|日语|韩文|韩语|英文回答|中文回答)", re.IGNORECASE)
STYLE_PATTERN = re.compile(r"(简洁|详细|精简|展开|直接一点|详细一点|短一点|长一点)", re.IGNORECASE)
CHANNEL_EMPHASIS_PATTERN = re.compile(r"(Telegram|飞书|TUI|默认强调)", re.IGNORECASE)
PROFILE_NAME_PATTERN = re.compile(r"^(?:我叫|我的名字是|你可以叫我)[：:，,\s]*(?P<content>.+)$")
ASSISTANT_NAME_PATTERN = re.compile(r"^(?:你叫|你可以叫自己|以后你叫)[：:，,\s]*(?P<content>.+)$")


def _normalize_memory_content(content: str) -> str:
    normalized = content.strip().strip("，,。；;：:！!？?")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized[:500]


def _infer_memory_target(content: str, reason: str) -> tuple[str, str | None, bool]:
    if reason == "addressing_user":
        return ("profile", "addressing_user", True)
    if reason == "assistant_name":
        return ("profile", "assistant_name", True)
    if reason == "language_preference":
        return ("preferences", "language", True)
    if reason == "response_style":
        return ("preferences", "style", True)
    if reason == "preference_statement":
        if LANGUAGE_PATTERN.search(content):
            return ("preferences", "language", True)
        return ("preferences", "style", True)
    if reason in {"response_rule", "future_rule"}:
        if CHANNEL_EMPHASIS_PATTERN.search(content):
            return ("rules", "channel_emphasis", True)
        if LANGUAGE_PATTERN.search(content):
            return ("preferences", "language", True)
        if STYLE_PATTERN.search(content):
            return ("preferences", "style", True)
        return ("rules", "reply_rule", True)
    profile_name = PROFILE_NAME_PATTERN.match(content)
    if profile_name:
        return ("profile", "addressing_user", True)
    assistant_name = ASSISTANT_NAME_PATTERN.match(content)
    if assistant_name:
        return ("profile", "assistant_name", True)
    if LANGUAGE_PATTERN.search(content):
        return ("preferences", "language", True)
    if STYLE_PATTERN.search(content):
        return ("preferences", "style", True)
    if CHANNEL_EMPHASIS_PATTERN.search(content):
        return ("rules", "channel_emphasis", True)
    return ("general", None, False)


def detect_memory_intent(text: str) -> MemoryIntent | None:
    stripped = text.strip()
    if not stripped:
        return None

    for pattern, reason in INTENT_RULES:
        match = pattern.match(stripped)
        if not match:
            continue
        content = _normalize_memory_content(match.group("content"))
        if not content:
            return None
        category, slot, auto_accept = _infer_memory_target(content, reason)
        return MemoryIntent(
            category=category,
            slot=slot,
            content=content,
            confidence="high",
            reason=reason,
            auto_accept=auto_accept,
        )

    return None


def should_auto_accept_memory_intent(intent: MemoryIntent) -> bool:
    return intent.confidence == "high" and intent.auto_accept


def build_memory_intent_ack(intent: MemoryIntent, auto_accepted: bool, debug: bool = False, detail: str = "") -> str:
    if auto_accepted:
        responses = [
            "好嘞，记住了。",
            "明白啦，已经记下。",
            "好的，收到。",
            "记到小本本上了。",
            "OK，已收录。",
            "收到，已记牢。",
        ]
        text = random.choice(responses)
    else:
        text = "行，先记下。"
    if not debug:
        return text

    extras = [
        f"- 分类：{intent.category}",
        f"- 槽位：{intent.slot or 'none'}",
        f"- 原因：{intent.reason}",
    ]
    if detail:
        extras.append(f"- 记录：{detail}")
    return text + "\n" + "\n".join(extras)
