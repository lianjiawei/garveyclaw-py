from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from weclaw.config import (
    MEMORY_DIR,
)

MEMORY_FREQUENCY_FILE = MEMORY_DIR / "frequency.json"
MEMORY_IMPORTANCE_FILE = MEMORY_DIR / "importance.json"

DEFAULT_FREQUENCY_STATE = {
    "topic_counts": {},
    "recent_topics": [],
    "last_updated": "",
}

DEFAULT_IMPORTANCE_STATE = {
    "memory_scores": {},
    "last_meditation": "",
}

KEYWORD_EXTRACTOR = re.compile(r"[\u4e00-\u9fa5]{2,10}|[a-zA-Z]{3,20}")


def _extract_keywords(text: str) -> list[str]:
    return [kw.lower() for kw in KEYWORD_EXTRACTOR.findall(text) if len(kw) >= 2]


def load_frequency_state() -> dict[str, Any]:
    if not MEMORY_FREQUENCY_FILE.exists():
        return dict(DEFAULT_FREQUENCY_STATE)
    try:
        data = json.loads(MEMORY_FREQUENCY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else dict(DEFAULT_FREQUENCY_STATE)
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_FREQUENCY_STATE)


def save_frequency_state(state: dict[str, Any]) -> None:
    state["last_updated"] = datetime.now().isoformat(timespec="seconds")
    MEMORY_FREQUENCY_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def update_memory_frequency(user_message: str, assistant_reply: str) -> dict[str, Any]:
    state = load_frequency_state()
    topic_counts: dict[str, int] = state.get("topic_counts", {})
    recent_topics: list[str] = state.get("recent_topics", [])

    keywords = _extract_keywords(user_message)
    for kw in keywords:
        topic_counts[kw] = topic_counts.get(kw, 0) + 1
        if kw not in recent_topics:
            recent_topics.append(kw)

    recent_topics = recent_topics[-50:]
    state["topic_counts"] = topic_counts
    state["recent_topics"] = recent_topics
    save_frequency_state(state)
    return state


def get_high_frequency_topics(threshold: int = 3, window: int = 50) -> list[tuple[str, int]]:
    state = load_frequency_state()
    topic_counts = state.get("topic_counts", {})
    return [(topic, count) for topic, count in topic_counts.items() if count >= threshold]


def load_importance_state() -> dict[str, Any]:
    if not MEMORY_IMPORTANCE_FILE.exists():
        return dict(DEFAULT_IMPORTANCE_STATE)
    try:
        data = json.loads(MEMORY_IMPORTANCE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else dict(DEFAULT_IMPORTANCE_STATE)
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_IMPORTANCE_STATE)


def save_importance_state(state: dict[str, Any]) -> None:
    state["last_meditation"] = datetime.now().isoformat(timespec="seconds")
    MEMORY_IMPORTANCE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def calculate_memory_importance(content: str, frequency_state: dict[str, Any] | None = None) -> float:
    score = 1.0
    keywords = _extract_keywords(content)
    if frequency_state is None:
        frequency_state = load_frequency_state()
    topic_counts = frequency_state.get("topic_counts", {})

    for kw in keywords:
        if kw in topic_counts:
            score += topic_counts[kw] * 0.2

    if any(word in content for word in ("必须", "一定", "记住", "重要", "永远")):
        score += 2.0
    if any(word in content for word in ("可能", "也许", "暂时", "暂时性")):
        score -= 0.5

    return round(score, 2)
