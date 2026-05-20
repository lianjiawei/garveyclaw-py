from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


from weclaw.config import (
    AGENT_PROVIDER as _CONFIG_AGENT_PROVIDER,
    ANTHROPIC_API_KEY as _CONFIG_ANTHROPIC_API_KEY,
    CLAUDE_MEMORY_FILE,
    CONVERSATIONS_DIR,
    CONVERSATION_RETENTION_DAYS,
    LONG_TERM_MEMORY_DIR,
    MEMORY_ARCHIVE_DIR,
    MEMORY_ARCHIVE_AFTER_DAYS,
    MEMORY_CANDIDATES_DIR,
    MEMORY_CANDIDATE_AUTO_PROMOTE_SECONDS,
    MEMORY_CONFLICTS_FILE,
    MEMORY_DIR,
    MEMORY_REPORTS_DIR,
    PROJECT_ROOT,
    SESSION_SUMMARIES_DIR,
    WORKING_STATE_FILE,
    WORKSPACE_DIR,
)
from weclaw.memory.io import (
    append_text_locked,
    locked_path,
    read_json_locked,
    read_text_locked,
    read_text_unlocked,
    unlink_locked,
    update_json_locked,
    write_json_atomic,
    write_text_atomic,
    write_text_atomic_unlocked,
)
from weclaw.memory.frequency import (
    calculate_memory_importance,

    get_high_frequency_topics,

    load_frequency_state,

    load_importance_state,

    save_importance_state,

    update_memory_frequency,

)
from weclaw.memory.reflection import run_reflection_model

logger = logging.getLogger(__name__)

# Compatibility hooks for older tests/extensions that monkeypatch these module attributes.
AGENT_PROVIDER = _CONFIG_AGENT_PROVIDER
ANTHROPIC_API_KEY = _CONFIG_ANTHROPIC_API_KEY



PROMPTS_DIR = WORKSPACE_DIR / "prompts"





def _load_reflection_prompt() -> str | None:

    """从 workspace/prompts/ 加载 reflection prompt，文件不存在时返回 None。"""

    path = PROMPTS_DIR / "reflection.md"

    if path.exists():

        return read_text_locked(path)

    return None





LONG_TERM_FILES = {
    "profile": LONG_TERM_MEMORY_DIR / "profile.md",

    "preferences": LONG_TERM_MEMORY_DIR / "preferences.md",

    "rules": LONG_TERM_MEMORY_DIR / "rules.md",

}



DEFAULT_WORKING_STATE = {

    "active_goal": "",

    "active_intent_type": "",

    "active_tasks": [],

    "recent_decisions": [],

    "open_questions": [],

    "touched_files": [],

    "updated_at": "",

}

FILE_REFERENCE_PATTERN = re.compile(r"(?:src|workspace|data|assets|skills|scripts)[/\\][^\s'\"`]+")

TASK_INTENT_PATTERN = re.compile(r"(帮我|请你|实现|修改|优化|重构|添加|增加|修复|排查|检查|分析|设计|整理|更新|刷新|生成|创建)")

QUESTION_INTENT_PATTERN = re.compile(r"(吗|么|什么|为何|为什么|如何|咋|怎么|哪|多少|是否|可不可以|能不能|\?|？)")

FILE_WORK_INTENT_PATTERN = re.compile(r"(文件|代码|模块|函数|类|路径|README|SVG|架构图|session|记忆|上下文|prompt)")

SLOT_MARKER_PATTERN = re.compile(r"<!--\s*slot:(?P<slot>[a-zA-Z0-9_-]+)\s*-->")
MEMORY_METADATA_PATTERN = re.compile(r"<!--\s*memory-meta:(?P<meta>.+)\s*-->")
KEYWORD_EXTRACTOR = re.compile(r"[\u4e00-\u9fa5]{2,10}|[a-zA-Z]{3,20}")


@dataclass(frozen=True, slots=True)
class MemoryMetadata:
    source: str = "user_explicit"
    confidence: str = "medium"
    scope: str = "global"
    valid_from: str = ""
    valid_until: str = ""
    last_confirmed_at: str = ""
    supersedes: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source": self.source,
            "confidence": self.confidence,
            "scope": self.scope,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "last_confirmed_at": self.last_confirmed_at,
            "supersedes": list(self.supersedes),
            "tags": list(self.tags),
            "reason": self.reason,
        }
        return {key: value for key, value in payload.items() if value not in ("", [], ())}


@dataclass(frozen=True, slots=True)
class StructuredMemoryEntry:
    category: str
    title: str
    slot: str | None
    content: str
    metadata: MemoryMetadata | None
    section_lines: list[str]

    @property
    def identity(self) -> str:
        if self.slot:
            return f"{self.category}:{self.slot}"
        return f"{self.category}:{self.title}"


def _build_memory_metadata(
    category: str,
    slot: str | None = None,
    reason: str | None = None,
    source: str = "user_explicit",
    confidence: str = "medium",
    scope: str = "global",
    valid_until: str = "",
    supersedes: list[str] | None = None,
    tags: list[str] | None = None,
) -> MemoryMetadata:
    now = datetime.now().isoformat(timespec="seconds")
    merged_tags = [category]
    if slot:
        merged_tags.append(slot)
    if reason:
        merged_tags.append(reason)
    for tag in tags or []:
        if tag and tag not in merged_tags:
            merged_tags.append(tag)
    return MemoryMetadata(
        source=source,
        confidence=confidence,
        scope=scope,
        valid_from=now,
        last_confirmed_at=now,
        valid_until=valid_until,
        supersedes=tuple(item for item in (supersedes or []) if item),
        tags=tuple(merged_tags),
        reason=reason or "",
    )


def create_memory_metadata(
    category: str,
    slot: str | None = None,
    reason: str | None = None,
    source: str = "user_explicit",
    confidence: str = "medium",
    scope: str = "global",
    valid_until: str = "",
    supersedes: list[str] | None = None,
    tags: list[str] | None = None,
) -> MemoryMetadata:
    return _build_memory_metadata(
        category=category,
        slot=slot,
        reason=reason,
        source=source,
        confidence=confidence,
        scope=scope,
        valid_until=valid_until,
        supersedes=supersedes,
        tags=tags,
    )


def _serialize_memory_metadata(metadata: MemoryMetadata | None) -> str:
    if metadata is None:
        return ""
    return json.dumps(metadata.to_dict(), ensure_ascii=False, sort_keys=True)


def _parse_memory_metadata(section_lines: list[str]) -> MemoryMetadata | None:
    for line in section_lines:
        match = MEMORY_METADATA_PATTERN.search(line)
        if not match:
            continue
        try:
            payload = json.loads(match.group("meta"))
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        return MemoryMetadata(
            source=str(payload.get("source") or "user_explicit"),
            confidence=str(payload.get("confidence") or "medium"),
            scope=str(payload.get("scope") or "global"),
            valid_from=str(payload.get("valid_from") or ""),
            valid_until=str(payload.get("valid_until") or ""),
            last_confirmed_at=str(payload.get("last_confirmed_at") or ""),
            supersedes=tuple(str(item) for item in payload.get("supersedes") or [] if item),
            tags=tuple(str(item) for item in payload.get("tags") or [] if item),
            reason=str(payload.get("reason") or ""),
        )
    return None


def _extract_section_title(section_lines: list[str]) -> str:
    if not section_lines:
        return ""
    return section_lines[0].removeprefix("## ").strip()


def _extract_memory_content(section: list[str]) -> str | None:
    for line in section:
        match = re.search(r"^-\s*(.+)$", line.strip())
        if match:
            return match.group(1).strip()
    return None


def _section_datetime(section_lines: list[str]) -> datetime | None:
    for line in section_lines:
        date_match = re.search(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})", line)
        if not date_match:
            continue
        text = date_match.group(1).replace(" ", "T")
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            continue
    return None


def _build_structured_entries(category: str, sections: list[list[str]]) -> list[StructuredMemoryEntry]:
    entries: list[StructuredMemoryEntry] = []
    for section in sections:
        content = _extract_memory_content(section)
        if not content:
            continue
        entries.append(
            StructuredMemoryEntry(
                category=category,
                title=_extract_section_title(section),
                slot=_section_slot(section),
                content=content,
                metadata=_parse_memory_metadata(section),
                section_lines=section,
            )
        )
    return entries


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    append_text_locked(path, json.dumps(payload, ensure_ascii=False) + "\n")


def _archive_sections(category: str, sections: list[list[str]], reason: str) -> Path | None:
    if not sections:
        return None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = MEMORY_ARCHIVE_DIR / f"{category}_{reason}_{timestamp}.md"
    lines = ["# Archived Memory Sections", "", f"- category: {category}", f"- reason: {reason}", f"- archived_at: {datetime.now().isoformat(timespec='seconds')}", ""]
    for section in sections:
        lines.extend(section)
        lines.append("")
    write_text_atomic(target, "\n".join(lines).rstrip() + "\n")
    return target


def _log_memory_conflict(category: str, slot: str, previous: StructuredMemoryEntry, incoming_note: str, metadata: MemoryMetadata | None) -> None:
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "category": category,
        "slot": slot,
        "previous_content": previous.content,
        "incoming_content": _normalize_memory_note(incoming_note),
        "previous_metadata": previous.metadata.to_dict() if previous.metadata else {},
        "incoming_metadata": metadata.to_dict() if metadata else {},
        "resolution": "superseded_by_newer_memory",
    }
    _append_jsonl(MEMORY_CONFLICTS_FILE, payload)




def _sanitize_scope(scope: str | None) -> str:

    if not scope:

        return "default"

    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", scope.strip()).strip("_")

    return normalized or "default"





def get_session_summary_file(scope: str | None = None) -> Path:

    return SESSION_SUMMARIES_DIR / f"{_sanitize_scope(scope)}.json"





def get_working_state_file(scope: str | None = None) -> Path:

    safe_scope = _sanitize_scope(scope)

    if safe_scope == "default":

        return WORKING_STATE_FILE

    return WORKING_STATE_FILE.with_name(f"{WORKING_STATE_FILE.stem}_{safe_scope}{WORKING_STATE_FILE.suffix}")





def ensure_memory_files() -> None:

    MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

    LONG_TERM_MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    MEMORY_CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)

    MEMORY_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    SESSION_SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)



    if not CLAUDE_MEMORY_FILE.exists():

        write_text_atomic(
            CLAUDE_MEMORY_FILE,

            "# 长期记忆\n\n"

            "## 目录说明\n"

            f"- 项目根目录：`{PROJECT_ROOT}`\n"

            f"- 工作区目录：`{WORKSPACE_DIR}`\n"

            f"- 长期记忆文件：`{CLAUDE_MEMORY_FILE}`\n"

            f"- 对话记录目录：`{CONVERSATIONS_DIR}`\n\n"

            "## 文件使用规则\n"

            "- 长期稳定信息写入 CLAUDE.md 或 long_term 目录。\n"

            "- 每轮对话原始记录追加写入 conversations 目录。\n"

            "- 工作区文件操作尽量限制在工作区目录内。\n\n"

            "## 默认背景\n"

            "- 当前项目是一个支持多入口和双 Provider 的个人 Agent。\n"

            "- 需要长期复用的信息优先结构化沉淀，而不是只追加原始日志。\n",

        )



    defaults = {

        "profile": "# 用户画像\n\n- 暂无结构化画像。\n",

        "preferences": "# 用户偏好\n\n- 暂无结构化偏好。\n",

        "rules": "# 长期规则\n\n- 暂无长期规则。\n",

    }

    for key, path in LONG_TERM_FILES.items():

        if not path.exists():

            write_text_atomic(path, defaults[key])



    if not WORKING_STATE_FILE.exists():

        write_json_atomic(WORKING_STATE_FILE, DEFAULT_WORKING_STATE)



    default_summary_file = get_session_summary_file()

    if not default_summary_file.exists():

        write_json_atomic(
            default_summary_file,
            {
                "session_scope": "default",
                "updated_at": "",
                "latest_user_message": "",
                "latest_assistant_reply_excerpt": "",
                "recent_topics": [],
            },
        )





def _read_json_file(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    return read_json_locked(path, fallback)





def _compact_text(value: str, limit: int) -> str:

    return value.strip().replace("\n", " ")[:limit]





def _append_unique_tail(items: list[str], value: str, max_items: int) -> list[str]:

    normalized = value.strip()

    if not normalized:

        return items[-max_items:]

    result = [item for item in items if item != normalized]

    result.append(normalized)

    return result[-max_items:]





def _extract_touched_files(*texts: str) -> list[str]:

    matches: list[str] = []

    for text in texts:

        for match in FILE_REFERENCE_PATTERN.findall(text):

            normalized = match.replace("\\", "/")

            if normalized not in matches:

                matches.append(normalized)

    return matches





def _extract_open_question(text: str) -> str:

    stripped = text.strip()

    if not stripped:

        return ""

    if any(stripped.endswith(mark) for mark in ("?", "？")):

        return _compact_text(stripped, 200)

    return ""





def _classify_intent(user_message: str) -> str:

    stripped = user_message.strip()

    if not stripped:

        return "empty"

    if stripped.startswith("/"):

        return "command"

    if FILE_WORK_INTENT_PATTERN.search(stripped) and TASK_INTENT_PATTERN.search(stripped):

        return "file_task"

    if TASK_INTENT_PATTERN.search(stripped):

        return "task"

    if QUESTION_INTENT_PATTERN.search(stripped):

        return "question"

    return "note"





def _extract_goal_candidate(user_message: str, intent_type: str) -> str:

    compact = _compact_text(user_message, 200)

    if intent_type in {"task", "file_task"}:

        return compact

    if intent_type == "question":

        return compact

    return ""





def _extract_decision_candidate(assistant_reply: str, intent_type: str) -> str:

    compact = _compact_text(assistant_reply, 240)

    if intent_type in {"task", "file_task", "question"}:

        return compact

    return compact[:160]





def _normalize_memory_note(note: str) -> str:

    return note.strip().replace("\n", " ")





def _split_markdown_sections(content: str) -> tuple[list[str], list[list[str]]]:

    lines = content.splitlines()

    preamble: list[str] = []

    sections: list[list[str]] = []

    current: list[str] | None = None



    for line in lines:

        if line.startswith("## "):

            if current is not None:

                sections.append(current)

            current = [line]

            continue

        if current is None:

            preamble.append(line)

        else:

            current.append(line)



    if current is not None:

        sections.append(current)

    return preamble, sections





def _section_slot(section_lines: list[str]) -> str | None:

    for line in section_lines:

        match = SLOT_MARKER_PATTERN.search(line)

        if match:

            return match.group("slot")

    return None





def _merge_structured_memory(
    path: Path,
    category: str,
    note: str,
    timestamp: str,
    slot: str | None = None,
    metadata: MemoryMetadata | None = None,
) -> bool:
    with locked_path(path):
        existing = read_text_unlocked(path)
        normalized_note = _normalize_memory_note(note)
        if normalized_note in existing:
            return False

        preamble, sections = _split_markdown_sections(existing)
        existing_entries = _build_structured_entries(category, sections)

        filtered_sections: list[list[str]] = []
        supersedes = list(metadata.supersedes) if metadata is not None else []
        removed_sections: list[list[str]] = []
        for section in sections:
            section_slot = _section_slot(section)
            if slot and section_slot == slot:
                if f"slot:{slot}" not in supersedes:
                    supersedes.append(f"slot:{slot}")
                removed_sections.append(section)
                continue
            filtered_sections.append(section)

        if metadata is not None and tuple(supersedes) != metadata.supersedes:
            metadata = MemoryMetadata(
                source=metadata.source,
                confidence=metadata.confidence,
                scope=metadata.scope,
                valid_from=metadata.valid_from,
                valid_until=metadata.valid_until,
                last_confirmed_at=metadata.last_confirmed_at,
                supersedes=tuple(supersedes),
                tags=metadata.tags,
                reason=metadata.reason,
            )

        if slot and removed_sections:
            previous_entries = [entry for entry in existing_entries if entry.slot == slot]
            if previous_entries:
                _log_memory_conflict(category, slot, previous_entries[-1], normalized_note, metadata)
            _archive_sections(category, removed_sections, "superseded")

        new_section = [f"## 自动记忆 {timestamp}"]
        if slot:
            new_section.append(f"<!-- slot:{slot} -->")
        serialized_metadata = _serialize_memory_metadata(metadata)
        if serialized_metadata:
            new_section.append(f"<!-- memory-meta:{serialized_metadata} -->")
        new_section.append(f"- {normalized_note}")
        filtered_sections.append(new_section)

        rebuilt_lines = list(preamble)
        if rebuilt_lines and rebuilt_lines[-1].strip() != "":
            rebuilt_lines.append("")

        for section in filtered_sections:
            if rebuilt_lines and rebuilt_lines[-1].strip() != "":
                rebuilt_lines.append("")
            rebuilt_lines.extend(section)

        write_text_atomic_unlocked(path, "\n".join(rebuilt_lines).rstrip() + "\n")
        return True





def load_long_term_memory() -> str:

    ensure_memory_files()

    return read_text_locked(CLAUDE_MEMORY_FILE)





def append_long_term_memory(note: str) -> None:

    ensure_memory_files()

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    append_text_locked(CLAUDE_MEMORY_FILE, f"\n## 追加记忆 {timestamp}\n- {note.strip()}\n")





def append_structured_long_term_memory(
    note: str,
    category: str,
    slot: str | None = None,
    metadata: MemoryMetadata | None = None,
) -> Path:
    ensure_memory_files()
    safe_category = re.sub(r"[^a-zA-Z0-9_-]+", "_", category.strip()).strip("_") or "general"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if metadata is None:
        metadata = _build_memory_metadata(
            category=safe_category,
            slot=slot,
            source="user_explicit",
            confidence="high",
        )
    if safe_category in LONG_TERM_FILES:
        target = LONG_TERM_FILES[safe_category]
        _merge_structured_memory(target, safe_category, note, timestamp, slot, metadata)
        return target
    append_long_term_memory(note)
    return CLAUDE_MEMORY_FILE


def append_memory_candidate(
    note: str,
    category: str = "general",
    reason: str | None = None,
    slot: str | None = None,
    metadata: MemoryMetadata | None = None,
) -> Path:
    ensure_memory_files()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_category = re.sub(r"[^a-zA-Z0-9_-]+", "_", category.strip()).strip("_") or "general"
    target = MEMORY_CANDIDATES_DIR / f"{timestamp}_{safe_category}.md"
    if metadata is None:
        metadata = _build_memory_metadata(
            category=safe_category,
            slot=slot,
            reason=reason,
            source="user_candidate",
            confidence="medium",
        )
    metadata_lines = [
        f"- category: {safe_category}",
        f"- created_at: {datetime.now().isoformat(timespec='seconds')}",
    ]
    if reason:
        metadata_lines.append(f"- reason: {reason}")
    if slot:
        metadata_lines.append(f"- slot: {slot}")
    if metadata is not None:
        for key, value in metadata.to_dict().items():
            if isinstance(value, list):
                metadata_lines.append(f"- {key}: {json.dumps(value, ensure_ascii=False)}")
            else:
                metadata_lines.append(f"- {key}: {value}")
    metadata_block = "\n".join(metadata_lines)
    write_text_atomic(target, f"# Memory Candidate\n\n{metadata_block}\n\n{note.strip()}\n")
    return target




def list_memory_candidates(limit: int = 20) -> list[Path]:

    ensure_memory_files()

    return sorted(MEMORY_CANDIDATES_DIR.glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)[:limit]





def get_memory_candidate(name: str) -> Path | None:

    ensure_memory_files()

    target = MEMORY_CANDIDATES_DIR / name.strip()

    return target if target.exists() and target.is_file() else None





def accept_memory_candidate(name: str, category: str = "general", slot: str | None = None) -> Path:
    candidate = get_memory_candidate(name)

    if candidate is None:

        raise FileNotFoundError(name)



    content = read_text_locked(candidate).strip()
    body = content.split("\n\n", maxsplit=2)[-1].strip() if content else ""
    safe_category = re.sub(r"[^a-zA-Z0-9_-]+", "_", category.strip()).strip("_") or "general"
    parsed_metadata = _parse_candidate_metadata(content)
    candidate_metadata = _build_memory_metadata(
        category=safe_category,
        slot=slot or str(parsed_metadata.get("slot") or "") or None,
        reason=str(parsed_metadata.get("reason") or ""),
        source=str(parsed_metadata.get("source") or "candidate_promoted"),
        confidence=str(parsed_metadata.get("confidence") or "medium"),
        scope=str(parsed_metadata.get("scope") or "global"),
        valid_until=str(parsed_metadata.get("valid_until") or ""),
        supersedes=[str(item) for item in parsed_metadata.get("supersedes") or []] if isinstance(parsed_metadata.get("supersedes"), list) else [],
        tags=[str(item) for item in parsed_metadata.get("tags") or []] if isinstance(parsed_metadata.get("tags"), list) else [],
    )

    target = append_structured_long_term_memory(body, safe_category, slot, candidate_metadata)


    unlink_locked(candidate)

    return target





def reject_memory_candidate(name: str) -> None:

    candidate = get_memory_candidate(name)

    if candidate is None:

        raise FileNotFoundError(name)

    unlink_locked(candidate)





def load_working_state(scope: str | None = None) -> dict[str, Any]:

    ensure_memory_files()

    data = _read_json_file(get_working_state_file(scope), DEFAULT_WORKING_STATE)

    merged = dict(DEFAULT_WORKING_STATE)

    merged.update(data)

    return merged





def save_working_state(state: dict[str, Any], scope: str | None = None) -> None:

    ensure_memory_files()

    payload = dict(DEFAULT_WORKING_STATE)

    payload.update(state)

    payload["session_scope"] = _sanitize_scope(scope)

    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")

    write_json_atomic(get_working_state_file(scope), payload)





def _compact_text(text: str, max_length: int) -> str:

    """截断文本到指定长度，避免工作记忆膨胀。"""

    if not text:

        return ""

    cleaned = text.strip().replace("\n", " ")

    if len(cleaned) > max_length:

        return cleaned[:max_length - 3] + "..."

    return cleaned





def update_working_state_from_turn(user_message: str, assistant_reply: str, scope: str | None = None) -> dict[str, Any]:

    ensure_memory_files()

    state = load_working_state(scope)



    intent_type = _classify_intent(user_message)

    active_goal = _extract_goal_candidate(user_message, intent_type)

    recent_decision = _extract_decision_candidate(assistant_reply, intent_type)

    open_question = _extract_open_question(assistant_reply)

    touched_files = _extract_touched_files(user_message, assistant_reply)



    state["active_intent_type"] = intent_type

    if active_goal:

        state["active_goal"] = _compact_text(active_goal, 200)

    state["active_tasks"] = [_compact_text(t, 200) for t in _append_unique_tail(list(state.get("active_tasks") or []), active_goal, 5)]

    state["recent_decisions"] = [_compact_text(d, 300) for d in _append_unique_tail(list(state.get("recent_decisions") or []), recent_decision, 8)]



    existing_questions = list(state.get("open_questions") or [])

    if open_question:

        state["open_questions"] = [_compact_text(q, 200) for q in _append_unique_tail(existing_questions, open_question, 5)]

    else:

        state["open_questions"] = [_compact_text(q, 200) for q in existing_questions[-5:]]



    merged_files = list(state.get("touched_files") or [])

    for touched in touched_files:

        merged_files = _append_unique_tail(merged_files, touched, 12)

    state["touched_files"] = merged_files[-12:]



    save_working_state(state, scope)

    return state





def load_session_summary(scope: str | None = None) -> dict[str, Any]:

    ensure_memory_files()

    return _read_json_file(

        get_session_summary_file(scope),

        {

            "session_scope": _sanitize_scope(scope),

            "updated_at": "",

            "latest_user_message": "",

            "latest_assistant_reply_excerpt": "",

            "recent_topics": [],

        },

    )





def save_session_summary(scope: str | None, user_message: str, assistant_reply: str) -> None:
    ensure_memory_files()
    fallback = {
        "session_scope": _sanitize_scope(scope),
        "updated_at": "",
        "latest_user_message": "",
        "latest_assistant_reply_excerpt": "",
        "recent_topics": [],
    }

    def updater(summary: dict[str, Any]) -> tuple[dict[str, Any], None]:
        recent_topics = list(summary.get("recent_topics") or [])
        compact_user_message = user_message.strip().replace("\n", " ")
        if compact_user_message:
            recent_topics.append(compact_user_message[:80])
        payload = {
            "session_scope": _sanitize_scope(scope),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "latest_user_message": compact_user_message[:500],
            "latest_assistant_reply_excerpt": assistant_reply.strip().replace("\n", " ")[:800],
            "recent_topics": recent_topics[-5:],
        }
        return payload, None

    update_json_locked(get_session_summary_file(scope), fallback, updater)


def clear_session_context(scope: str | None = None) -> None:
    """清空当前会话级上下文，但保留长期记忆。"""

    ensure_memory_files()

    working_state_file = get_working_state_file(scope)
    if working_state_file.exists():
        write_json_atomic(working_state_file, DEFAULT_WORKING_STATE)

    summary_file = get_session_summary_file(scope)
    summary_payload = {
        "session_scope": _sanitize_scope(scope),
        "updated_at": "",
        "latest_user_message": "",
        "latest_assistant_reply_excerpt": "",
        "recent_topics": [],
    }
    write_json_atomic(summary_file, summary_payload)


def _extract_query_keywords(text: str) -> set[str]:
    return {item.lower() for item in KEYWORD_EXTRACTOR.findall(text or "") if len(item) >= 2}


def _memory_importance_keys(entry: StructuredMemoryEntry) -> list[str]:
    keys = [entry.identity]
    if entry.slot:
        keys.append(entry.slot)
        keys.append(f"{entry.category}:{entry.slot}")
    keys.append(entry.content[:80])
    return keys


def _memory_importance_key(category: str, section: list[str], content: str) -> str:
    slot = _section_slot(section)
    if slot:
        return f"{category}:{slot}"
    title = _extract_section_title(section)
    return f"{category}:{title}" if title else f"{category}:{content[:80]}"


def _score_memory_entry(entry: StructuredMemoryEntry, query_keywords: set[str], importance_scores: dict[str, float] | None = None) -> float:

    metadata_bonus = _metadata_importance_bonus(entry.metadata)

    if entry.metadata is not None and entry.metadata.valid_until:

        try:

            if datetime.fromisoformat(entry.metadata.valid_until) < datetime.now():

                return -999.0

        except ValueError:

            pass



    entry_keywords = _extract_query_keywords(entry.content)

    overlap = len(entry_keywords & query_keywords) if query_keywords else 0

    union = len(entry_keywords | query_keywords) if query_keywords else 0

    jaccard = (overlap / union) if union else 0.0



    recency_bonus = 0.0

    section_time = _section_datetime(entry.section_lines)

    if section_time is not None:

        age_days = max((datetime.now() - section_time).days, 0)

        recency_bonus = max(0.0, 1.5 - min(age_days / 30, 1.5))



    decay_penalty = 0.0

    if entry.metadata is not None:

        if entry.metadata.scope == "temporary":

            decay_penalty += 1.5

            if section_time is not None and (datetime.now() - section_time).days > 3:

                decay_penalty += 2.0

        elif entry.metadata.scope == "session" and section_time is not None and (datetime.now() - section_time).days > 7:

            decay_penalty += 1.5



        if entry.metadata.last_confirmed_at:

            try:

                confirmed_at = datetime.fromisoformat(entry.metadata.last_confirmed_at)

                stale_days = max((datetime.now() - confirmed_at).days, 0)

                if stale_days > 30:

                    decay_penalty += min(stale_days / 60, 2.5)

            except ValueError:

                pass



    frequency_bonus = 0.0

    if importance_scores:
        memory_score = max((importance_scores.get(key, 1.0) for key in _memory_importance_keys(entry)), default=1.0)
        topic_score = max((importance_scores.get(keyword, 1.0) for keyword in entry_keywords), default=1.0)
        frequency_bonus = max(memory_score - 1.0, 0.0) * 0.3 + max(topic_score - 1.0, 0.0) * 0.08



    slot_bonus = 0.8 if entry.slot and any(part in query_keywords for part in _extract_query_keywords(entry.slot)) else 0.0

    return round(overlap * 1.5 + jaccard * 2.0 + metadata_bonus + recency_bonus + frequency_bonus + slot_bonus - decay_penalty, 3)


def _render_ranked_memory_sections(category: str, title: str, path: Path, query_keywords: set[str], limit: int, importance_scores: dict[str, float] | None = None) -> str:

    if not path.exists():

        return f"## {title}\n- 暂无记录。"

    existing = read_text_locked(path)

    preamble, sections = _split_markdown_sections(existing)

    entries = _build_structured_entries(category, sections)

    if not entries:

        content = existing.strip() or "- 暂无记录。"

        return f"## {title}\n{content}"



    scored = [(entry, _score_memory_entry(entry, query_keywords, importance_scores)) for entry in entries]

    ranked = sorted(scored, key=lambda x: x[1], reverse=True)

    selected = [entry for entry, score in ranked if score > -900][:limit]

    if not selected:

        selected = [entry for entry, _ in ranked[:limit]]



    lines = [f"## {title}"]

    for entry in selected:

        lines.append(f"- {entry.content}")

    return "\n".join(lines)


def _render_general_memory(query_keywords: set[str], limit: int = 4, importance_scores: dict[str, float] | None = None) -> str:

    content = load_long_term_memory().strip()

    if not content:

        return "## 兼容长期记忆\n- 暂无记录。"

    preamble, sections = _split_markdown_sections(content)

    entries = _build_structured_entries("general", sections)

    if not entries:

        compact_lines = [line for line in content.splitlines() if line.strip()][:limit]

        return "## 兼容长期记忆\n" + "\n".join(compact_lines)

    scored = [(entry, _score_memory_entry(entry, query_keywords, importance_scores)) for entry in entries]

    ranked = sorted(scored, key=lambda x: x[1], reverse=True)

    selected = [entry for entry, score in ranked if score > -900][:limit]

    if not selected:

        selected = [entry for entry, _ in ranked[:limit]]

    lines = ["## 兼容长期记忆"]

    for entry in selected:

        lines.append(f"- {entry.content}")

    return "\n".join(lines)


def _render_working_state_nl(working_state: dict) -> str:

    parts = ["## 工作记忆"]

    if working_state.get("active_goal"):

        parts.append(f"- 当前目标：{working_state['active_goal']}")

    if working_state.get("active_intent_type"):

        parts.append(f"- 当前意图类型：{working_state['active_intent_type']}")

    tasks = working_state.get("active_tasks", [])

    if tasks:

        parts.append(f"- 进行中的任务（{len(tasks)}个）：")

        for t in tasks:

            parts.append(f"  - {t}")

    decisions = working_state.get("recent_decisions", [])

    if decisions:

        parts.append(f"- 最近决策（{len(decisions)}条）：")

        for d in decisions:

            parts.append(f"  - {d}")

    questions = working_state.get("open_questions", [])

    if questions:

        parts.append(f"- 待解决问题（{len(questions)}个）：")

        for q in questions:

            parts.append(f"  - {q}")

    files = working_state.get("touched_files", [])

    if files:

        parts.append(f"- 最近访问文件（{len(files)}个）：")

        for f in files:

            parts.append(f"  - {f}")

    if len(parts) == 1:

        parts.append("- 暂无记录。")

    return "\n".join(parts)





def _render_session_summary_nl(summary: dict) -> str:

    parts = ["## 会话摘要"]

    if summary.get("latest_user_message"):

        parts.append(f"- 用户最近消息：{summary['latest_user_message']}")

    if summary.get("latest_assistant_reply_excerpt"):

        parts.append(f"- 助手最近回复：{summary['latest_assistant_reply_excerpt']}")

    topics = summary.get("recent_topics", [])

    if topics:

        parts.append(f"- 近期话题：{'、'.join(topics)}")

    if len(parts) == 1:

        parts.append("- 暂无记录。")

    return "\n".join(parts)





def _compute_importance_scores(frequency_state: dict[str, Any]) -> dict[str, float]:

    scores: dict[str, float] = {}

    topic_counts = frequency_state.get("topic_counts", {})

    for topic, count in topic_counts.items():

        if count >= 3:

            scores[topic] = round(1.0 + count * 0.2, 2)

    return scores





def build_context_snapshot(scope: str | None = None, query_text: str | None = None) -> str:

    ensure_memory_files()

    working_state = load_working_state(scope)

    session_summary = load_session_summary(scope)

    sections: list[str] = []

    query_keywords = _extract_query_keywords(query_text or "")



    freq_state = load_frequency_state()

    importance_scores = _compute_importance_scores(freq_state)
    saved_importance_scores = load_importance_state().get("memory_scores", {})
    if isinstance(saved_importance_scores, dict):
        for key, value in saved_importance_scores.items():
            try:
                importance_scores[str(key)] = float(value)
            except (TypeError, ValueError):
                continue



    for key, title in (

        ("profile", "用户画像"),

        ("preferences", "用户偏好"),

        ("rules", "长期规则"),

    ):

        sections.append(_render_ranked_memory_sections(key, title, LONG_TERM_FILES[key], query_keywords, limit=4, importance_scores=importance_scores))



    sections.append(_render_working_state_nl(working_state))

    sections.append(_render_session_summary_nl(session_summary))

    sections.append(_render_general_memory(query_keywords, limit=4, importance_scores=importance_scores))

    return "\n\n".join(sections).strip()




def append_conversation_record(user_message: str, assistant_reply: str, session_id: str | None, session_scope: str | None = None) -> None:

    ensure_memory_files()

    date_key = datetime.now().strftime("%Y-%m-%d")

    file_path = CONVERSATIONS_DIR / f"{date_key}.jsonl"

    payload = {

        "created_at": datetime.now().isoformat(timespec="seconds"),

        "session_id": session_id,

        "session_scope": _sanitize_scope(session_scope),

        "user_message": user_message,

        "assistant_reply": assistant_reply,

    }

    _append_jsonl(file_path, payload)

    save_session_summary(session_scope, user_message, assistant_reply)

    update_working_state_from_turn(user_message, assistant_reply, session_scope)

    update_memory_frequency(user_message, assistant_reply)





def load_recent_conversation_turns(
    session_scope: str | None,
    *,
    limit: int = 6,
    max_chars_per_message: int = 2400,
    days: int = 7,
) -> list[dict[str, str]]:
    """Load recent user/assistant text turns for providers without native resume."""

    ensure_memory_files()
    if not session_scope or limit <= 0:
        return []

    target_scope = _sanitize_scope(session_scope)
    cutoff = datetime.now() - timedelta(days=max(days, 1))
    turns: list[dict[str, str]] = []

    for log_file in sorted(CONVERSATIONS_DIR.glob("*.jsonl"), reverse=True):
        try:
            date_match = re.match(r"^(\d{4}-\d{2}-\d{2})\.jsonl$", log_file.name)
            if not date_match:
                continue
            file_date = datetime.strptime(date_match.group(1), "%Y-%m-%d")
            if file_date < cutoff:
                continue
            lines = read_text_locked(log_file).splitlines()
        except (OSError, ValueError):
            continue

        for line in reversed(lines):
            if len(turns) >= limit:
                return list(reversed(turns))
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if str(payload.get("session_scope") or "") != target_scope:
                continue

            user_message = _compact_text(str(payload.get("user_message") or ""), max_chars_per_message)
            assistant_reply = _compact_text(str(payload.get("assistant_reply") or ""), max_chars_per_message)
            if not user_message or not assistant_reply:
                continue
            turns.append({"user": user_message, "assistant": assistant_reply})

    return list(reversed(turns))


def _parse_candidate_timestamp(filename: str) -> datetime | None:

    match = re.match(r"^(?P<ts>\d{8}_\d{6})_", filename)

    if not match:

        return None

    try:

        return datetime.strptime(match.group("ts"), "%Y%m%d_%H%M%S")

    except ValueError:

        return None





def _parse_candidate_metadata(content: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- ") or ":" not in stripped:
            continue
        key, raw_value = stripped[2:].split(":", maxsplit=1)
        key = key.strip()
        value = raw_value.strip()
        if not key:
            continue
        if value.startswith("[") and value.endswith("]"):
            try:
                metadata[key] = json.loads(value)
                continue
            except json.JSONDecodeError:
                pass
        metadata[key] = value
    return metadata




def _get_promote_delay_seconds(reason: str | None) -> int:

    if reason in {"explicit_remember", "addressing_user", "assistant_name"}:

        return 0

    if reason in {"language_preference", "response_style", "preference_statement"}:

        return 3600

    if reason in {"future_rule", "response_rule"}:

        return 21600

    return MEMORY_CANDIDATE_AUTO_PROMOTE_SECONDS





def _is_age_archivable(metadata: MemoryMetadata | None) -> bool:
    if metadata is None:
        return True
    confidence = metadata.confidence.strip().lower()
    scope = metadata.scope.strip().lower()
    source = metadata.source.strip().lower()
    reason = metadata.reason.strip().lower()
    durable_sources = {"user_explicit", "manual_remember", "candidate_promoted", "candidate_auto_promoted", "nightly_reflection"}
    durable_reasons = {"explicit_remember", "addressing_user", "assistant_name", "language_preference", "response_rule", "future_rule"}
    if scope == "global" and confidence in {"high", "medium"} and (source in durable_sources or reason in durable_reasons):
        return False
    if scope == "global" and confidence == "high":
        return False
    return True


def auto_promote_candidates() -> list[Path]:

    promoted: list[Path] = []

    now = datetime.now()



    for candidate_path in list_memory_candidates(limit=100):

        content = read_text_locked(candidate_path).strip()

        if not content:

            unlink_locked(candidate_path)

            continue



        body = content.split("\n\n", maxsplit=2)[-1].strip() if content else ""

        if not body:

            unlink_locked(candidate_path)

            continue



        parsed_metadata = _parse_candidate_metadata(content)
        category = str(parsed_metadata.get("category") or "general")
        slot = str(parsed_metadata.get("slot") or "") or None
        reason = str(parsed_metadata.get("reason") or "") or None
        created_at = _parse_candidate_timestamp(candidate_path.name)
        if created_at is None:

            continue



        delay_seconds = _get_promote_delay_seconds(reason)

        age_seconds = (now - created_at).total_seconds()

        if age_seconds < delay_seconds:

            continue



        safe_category = re.sub(r"[^a-zA-Z0-9_-]+", "_", category.strip()).strip("_") or "general"

        candidate_metadata = _build_memory_metadata(
            category=safe_category,
            slot=slot,
            reason=reason,
            source=str(parsed_metadata.get("source") or "candidate_auto_promoted"),
            confidence=str(parsed_metadata.get("confidence") or "medium"),
            scope=str(parsed_metadata.get("scope") or "global"),
            valid_until=str(parsed_metadata.get("valid_until") or ""),
            supersedes=[str(item) for item in parsed_metadata.get("supersedes") or []] if isinstance(parsed_metadata.get("supersedes"), list) else [],
            tags=[str(item) for item in parsed_metadata.get("tags") or []] if isinstance(parsed_metadata.get("tags"), list) else [],
        )
        target = append_structured_long_term_memory(body, safe_category, slot, candidate_metadata)
        unlink_locked(candidate_path)

        promoted.append(target)



    return promoted





def archive_old_memories() -> list[Path]:

    archived: list[Path] = []

    now = datetime.now()

    cutoff_days = MEMORY_ARCHIVE_AFTER_DAYS



    for category, path in LONG_TERM_FILES.items():

        if not path.exists():

            continue



        with locked_path(path):
            existing = read_text_unlocked(path)

        preamble, sections = _split_markdown_sections(existing)



        kept_sections: list[list[str]] = []

        archived_sections: list[list[str]] = []



        for section in sections:
            section_date = None
            metadata = _parse_memory_metadata(section)
            for line in section:
                date_match = re.search(r"自动记忆\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", line)
                if date_match:
                    try:

                        section_date = datetime.strptime(date_match.group(1), "%Y-%m-%d %H:%M:%S")

                    except ValueError:

                        pass

                    break

            is_expired = False
            if metadata is not None and metadata.valid_until:
                try:
                    is_expired = datetime.fromisoformat(metadata.valid_until) < now
                except ValueError:
                    is_expired = False

            is_decayed_temporary = False
            if metadata is not None and metadata.scope == "temporary" and section_date is not None:
                is_decayed_temporary = (now - section_date).days > 3

            is_stale_session = False
            if metadata is not None and metadata.scope == "session" and section_date is not None:
                is_stale_session = (now - section_date).days > 7

            is_age_expired = bool(section_date and (now - section_date).days > cutoff_days and _is_age_archivable(metadata))

            if is_expired or is_decayed_temporary or is_stale_session or is_age_expired:
                archived_sections.append(section)
            else:
                kept_sections.append(section)


        if archived_sections:

            archive_file = MEMORY_ARCHIVE_DIR / f"{category}_{now.strftime('%Y%m%d')}.md"

            archive_lines = list(preamble)

            for section in archived_sections:

                if archive_lines and archive_lines[-1].strip() != "":

                    archive_lines.append("")

                archive_lines.extend(section)

            write_text_atomic(archive_file, "\n".join(archive_lines).rstrip() + "\n")

            archived.append(archive_file)



            rebuilt_lines = list(preamble)

            for section in kept_sections:

                if rebuilt_lines and rebuilt_lines[-1].strip() != "":

                    rebuilt_lines.append("")

                rebuilt_lines.extend(section)

            with locked_path(path):
                write_text_atomic_unlocked(path, "\n".join(rebuilt_lines).rstrip() + "\n")



    return archived

def _calculate_section_similarity(section_a: list[str], section_b: list[str]) -> float:
    """计算两个记忆片段的相似度，使用改进的关键词+字符n-gram混合相似度。"""

    content_a = _extract_memory_content(section_a)

    content_b = _extract_memory_content(section_b)

    if not content_a or not content_b:

        return 0.0



    # 提取关键词

    keywords_a = set(KEYWORD_EXTRACTOR.findall(content_a.lower()))

    keywords_b = set(KEYWORD_EXTRACTOR.findall(content_b.lower()))



    if not keywords_a or not keywords_b:

        return 0.0



    # 关键词 Jaccard 相似度

    keyword_intersection = keywords_a & keywords_b

    keyword_union = keywords_a | keywords_b

    keyword_similarity = len(keyword_intersection) / len(keyword_union) if keyword_union else 0.0



    # 字符 2-gram 相似度（对短文本更有效）

    def char_bigrams(text: str) -> set[str]:

        return {text[i:i+2] for i in range(len(text) - 1)}



    bigrams_a = char_bigrams(content_a.lower())

    bigrams_b = char_bigrams(content_b.lower())

    if bigrams_a and bigrams_b:

        bigram_intersection = bigrams_a & bigrams_b

        bigram_union = bigrams_a | bigrams_b

        bigram_similarity = len(bigram_intersection) / len(bigram_union) if bigram_union else 0.0

    else:

        bigram_similarity = 0.0



    # 混合相似度：关键词权重 60%，n-gram 权重 40%
    return 0.6 * keyword_similarity + 0.4 * bigram_similarity


def _metadata_importance_bonus(metadata: MemoryMetadata | None) -> float:
    if metadata is None:
        return 0.0
    bonus = 0.0
    if metadata.confidence == "high":
        bonus += 1.5
    elif metadata.confidence == "medium":
        bonus += 0.5
    else:
        bonus -= 0.5

    if metadata.source in {"user_explicit", "candidate_promoted", "candidate_auto_promoted"}:
        bonus += 0.8
    elif metadata.source == "inferred":
        bonus += 0.2

    if metadata.scope == "global":
        bonus += 0.4
    elif metadata.scope == "temporary":
        bonus -= 0.5

    if metadata.valid_until:
        try:
            if datetime.fromisoformat(metadata.valid_until) < datetime.now():
                bonus -= 3.0
        except ValueError:
            pass
    return bonus


def _read_recent_conversation_records(limit: int = 40, days: int = 3) -> list[dict[str, Any]]:
    cutoff = datetime.now() - timedelta(days=days)
    records: list[dict[str, Any]] = []
    for log_file in sorted(CONVERSATIONS_DIR.glob("*.jsonl"), reverse=True):
        try:
            date_match = re.match(r"^(\d{4}-\d{2}-\d{2})\.jsonl$", log_file.name)
            if not date_match:
                continue
            file_date = datetime.strptime(date_match.group(1), "%Y-%m-%d")
            if file_date < cutoff:
                continue
            for line in reversed(read_text_locked(log_file).splitlines()):
                if len(records) >= limit:
                    return records
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    records.append(payload)
        except OSError:
            continue
    return records


def _load_candidate_briefs(limit: int = 20) -> list[dict[str, Any]]:
    briefs: list[dict[str, Any]] = []
    for path in list_memory_candidates(limit=limit):
        content = read_text_locked(path, errors="replace").strip()
        body = content.split("\n\n", maxsplit=2)[-1].strip() if content else ""
        metadata = _parse_candidate_metadata(content)
        briefs.append(
            {
                "name": path.name,
                "category": metadata.get("category", "general"),
                "slot": metadata.get("slot"),
                "reason": metadata.get("reason"),
                "content": body[:240],
            }
        )
    return briefs


def _load_structured_memory_briefs() -> list[dict[str, Any]]:
    briefs: list[dict[str, Any]] = []
    for category, path in LONG_TERM_FILES.items():
        if not path.exists():
            continue
        _, sections = _split_markdown_sections(read_text_locked(path))
        for entry in _build_structured_entries(category, sections):
            briefs.append(
                {
                    "category": category,
                    "slot": entry.slot,
                    "content": entry.content[:240],
                    "metadata": entry.metadata.to_dict() if entry.metadata else {},
                }
            )
    return briefs


def _strip_json_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _archive_slots(category: str, slots: list[str], reason: str) -> list[str]:
    path = LONG_TERM_FILES.get(category)
    if path is None or not path.exists() or not slots:
        return []
    existing = read_text_locked(path)
    preamble, sections = _split_markdown_sections(existing)
    kept_sections: list[list[str]] = []
    archived_sections: list[list[str]] = []
    archived_slots: list[str] = []
    for section in sections:
        section_slot = _section_slot(section)
        if section_slot and section_slot in slots:
            archived_sections.append(section)
            archived_slots.append(section_slot)
            continue
        kept_sections.append(section)
    if not archived_sections:
        return []
    _archive_sections(category, archived_sections, reason)
    rebuilt_lines = list(preamble)
    for section in kept_sections:
        if rebuilt_lines and rebuilt_lines[-1].strip() != "":
            rebuilt_lines.append("")
        rebuilt_lines.extend(section)
    write_text_atomic(path, "\n".join(rebuilt_lines).rstrip() + "\n")
    return archived_slots


async def reflect_and_rewrite_memories() -> dict[str, Any]:
    report_path = MEMORY_REPORTS_DIR / f"memory_reflection_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    report: dict[str, Any] = {
        "used_model": False,
        "provider": "",
        "model": "",
        "applied_rewrites": [],
        "promoted_candidates": [],
        "archived_slots": [],
        "raw_response": "",
        "model_errors": [],
        "report_file": report_path.name,
    }

    memories = _load_structured_memory_briefs()

    candidates = _load_candidate_briefs()

    recent_records = _read_recent_conversation_records()

    input_data = json.dumps(

        {

            "memories": memories[:40],

            "candidates": candidates[:20],

            "recent_conversations": recent_records[:40],

        },

        ensure_ascii=False,

        indent=2,

    )



    reflection_template = _load_reflection_prompt()

    if reflection_template:

        combined_prompt = reflection_template.replace("{INPUT_DATA}", input_data)

    else:

        combined_prompt = (

            "你是 WeClaw 的夜间记忆反思器。"

            "你的任务是识别可以重写沉淀的长期规则、应该晋升的候选记忆，以及应该归档的过期 slot。"

            "只返回 JSON，不要解释。\n\n"

            "请基于下面的输入，输出 JSON，格式为：\n"

            "{\n"

            '  "rewrite_memories": [{"category": "preferences|rules|profile", "slot": "...", "content": "...", "confidence": "high|medium|low", "reason": "...", "scope": "global|session|temporary", "valid_until": ""}],\n'

            '  "promote_candidates": [{"name": "candidate.md", "category": "preferences|rules|profile|general", "slot": "..."}],\n'

            '  "archive_slots": [{"category": "preferences|rules|profile", "slot": "...", "reason": "..."}]\n'

            "}\n"

            "只在你有足够把握时输出 action；没有就返回空数组。\n\n"

            f"输入数据：\n{input_data}"

        )


    provider_override = AGENT_PROVIDER if AGENT_PROVIDER != _CONFIG_AGENT_PROVIDER else None
    api_key_override = ANTHROPIC_API_KEY if ANTHROPIC_API_KEY != _CONFIG_ANTHROPIC_API_KEY else None
    model_result = await run_reflection_model(combined_prompt, provider_override=provider_override, api_key_override=api_key_override)
    report["provider"] = model_result.provider
    report["model"] = model_result.model
    report["used_model"] = model_result.used_model
    report["raw_response"] = model_result.text
    report["model_errors"] = model_result.errors
    if model_result.errors:
        write_json_atomic(report_path, report)
        return report

    actions: dict[str, Any]
    try:
        actions = json.loads(_strip_json_fence(model_result.text)) if model_result.text else {}
    except json.JSONDecodeError:
        actions = {}
        report["parse_failed"] = True

    for item in actions.get("promote_candidates", []) or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not name:
            continue
        try:
            target = accept_memory_candidate(name, category=str(item.get("category") or "general"), slot=str(item.get("slot") or "") or None)
            report["promoted_candidates"].append({"name": name, "target": target.name})
        except Exception as exc:
            report.setdefault("promotion_errors", []).append({"name": name, "error": str(exc)})

    for item in actions.get("rewrite_memories", []) or []:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "").strip()
        content = str(item.get("content") or "").strip()
        if not category or not content:
            continue
        slot = str(item.get("slot") or "").strip() or None
        metadata = create_memory_metadata(
            category=category,
            slot=slot,
            reason=str(item.get("reason") or "nightly_reflection"),
            source="nightly_reflection",
            confidence=str(item.get("confidence") or "medium"),
            scope=str(item.get("scope") or "global"),
            valid_until=str(item.get("valid_until") or ""),
        )
        target = append_structured_long_term_memory(content, category, slot, metadata)
        report["applied_rewrites"].append({"category": category, "slot": slot, "target": target.name, "content": content[:120]})

    archive_groups: dict[str, list[str]] = {}
    for item in actions.get("archive_slots", []) or []:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "").strip()
        slot = str(item.get("slot") or "").strip()
        if not category or not slot:
            continue
        archive_groups.setdefault(category, []).append(slot)
    for category, slots in archive_groups.items():
        archived = _archive_slots(category, slots, "nightly_reflection")
        if archived:
            report["archived_slots"].append({"category": category, "slots": archived})

    write_json_atomic(report_path, report)
    return report




def meditate_and_organize_memories() -> dict[str, Any]:

    frequency_state = load_frequency_state()

    high_freq_topics = get_high_frequency_topics(threshold=3)



    meditation_report = {

        "promoted_by_frequency": [],

        "merged_memories": [],

        "cleaned_memories": [],

        "importance_scores": {},

    }



    for topic, count in high_freq_topics:

        meditation_report["promoted_by_frequency"].append({

            "topic": topic,

            "count": count,

        })



    for category, path in LONG_TERM_FILES.items():

        if not path.exists():

            continue



        existing = read_text_locked(path)

        preamble, sections = _split_markdown_sections(existing)



        if len(sections) <= 1:

            continue



        # 预计算每个section的关键词集合，用于快速过滤

        section_keywords: list[set[str]] = []

        for section in sections:

            content = _extract_memory_content(section)

            if content:

                section_keywords.append(set(KEYWORD_EXTRACTOR.findall(content.lower())))

            else:

                section_keywords.append(set())



        merged_sections: list[list[str]] = []

        used_indices: set[int] = set()



        for i, section_a in enumerate(sections):

            if i in used_indices:

                continue



            similar_sections = [section_a]

            used_indices.add(i)

            keywords_a = section_keywords[i]

            if not keywords_a:

                continue



            for j, section_b in enumerate(sections):

                if j in used_indices:

                    continue

                keywords_b = section_keywords[j]

                if not keywords_b:

                    continue



                # 快速预过滤：关键词交集至少要有 30% 重叠才进行精确计算

                quick_overlap = len(keywords_a & keywords_b) / max(len(keywords_a | keywords_b), 1)

                if quick_overlap < 0.3:

                    continue



                similarity = _calculate_section_similarity(section_a, section_b)

                if similarity > 0.6:

                    similar_sections.append(section_b)

                    used_indices.add(j)



            if len(similar_sections) > 1:

                merged_content = _extract_memory_content(section_a)

                if merged_content:

                    merged_section = [

                        f"## 冥想合并 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",

                        f"- {merged_content} [合并 {len(similar_sections)} 条相似记忆]",

                    ]

                    merged_sections.append(merged_section)

                    meditation_report["merged_memories"].append({

                        "category": category,

                        "merged_count": len(similar_sections),

                        "content_preview": merged_content[:50],

                    })

            else:

                merged_sections.append(section_a)



        kept_sections = []
        cleaned_count = 0
        for section in merged_sections:
            content = _extract_memory_content(section)
            if content:
                metadata = _parse_memory_metadata(section)
                importance = calculate_memory_importance(content, frequency_state) + _metadata_importance_bonus(metadata)
                importance_key = _memory_importance_key(category, section, content)
                meditation_report["importance_scores"][importance_key] = round(importance, 2)
                if importance < 1.0 and len(merged_sections) > 3:
                    cleaned_count += 1
                    continue
                kept_sections.append(section)


        if cleaned_count > 0:

            meditation_report["cleaned_memories"].append({

                "category": category,

                "cleaned_count": cleaned_count,

            })



        if merged_sections != sections:

            rebuilt_lines = list(preamble)

            for section in kept_sections:

                if rebuilt_lines and rebuilt_lines[-1].strip() != "":

                    rebuilt_lines.append("")

                rebuilt_lines.extend(section)

            write_text_atomic(path, "\n".join(rebuilt_lines).rstrip() + "\n")



    save_importance_state({"memory_scores": meditation_report["importance_scores"]})



    return meditation_report





def clean_old_conversations() -> list[Path]:

    """清理超过保留天数的对话日志文件。"""

    cleaned: list[Path] = []

    now = datetime.now()

    cutoff = now - timedelta(days=CONVERSATION_RETENTION_DAYS)



    for log_file in sorted(CONVERSATIONS_DIR.glob("*.jsonl")):

        try:

            date_match = re.match(r"^(\d{4}-\d{2}-\d{2})\.jsonl$", log_file.name)

            if not date_match:

                continue

            file_date = datetime.strptime(date_match.group(1), "%Y-%m-%d")

            if file_date < cutoff:

                unlink_locked(log_file)

                cleaned.append(log_file)

        except (ValueError, OSError):

            continue



    return cleaned
