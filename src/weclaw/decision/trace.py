from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from typing import Any


@dataclass(slots=True)
class TraceStep:
    name: str
    category: str
    risk_level: str
    summary: str
    started_at: str
    finished_at: str = ""
    success: bool = False
    result_excerpt: str = ""
    touched_files: tuple[str, ...] = ()


@dataclass(slots=True)
class ExecutionTrace:
    session_scope: str
    channel: str
    prompt: str
    created_at: str
    steps: list[TraceStep] = field(default_factory=list)


_TRACE_LOCK = Lock()
_ACTIVE_TRACES: dict[str, ExecutionTrace] = {}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _compact(text: str | None, limit: int = 160) -> str:
    if not text:
        return ""
    normalized = " ".join(str(text).split())
    return normalized if len(normalized) <= limit else normalized[: limit - 3] + "..."


def _extract_touched_files(arguments: dict[str, Any]) -> tuple[str, ...]:
    candidates: list[str] = []
    for key in ("path", "workdir", "definition_json"):
        value = arguments.get(key)
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if not stripped:
            continue
        if key == "definition_json":
            continue
        candidates.append(stripped)
    seen: set[str] = set()
    ordered: list[str] = []
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return tuple(ordered[:8])


def start_execution_trace(session_scope: str | None, channel: str | None, prompt: str) -> None:
    if not session_scope:
        return
    trace = ExecutionTrace(
        session_scope=session_scope,
        channel=channel or "",
        prompt=_compact(prompt, 300),
        created_at=_now_iso(),
    )
    with _TRACE_LOCK:
        _ACTIVE_TRACES[session_scope] = trace


def clear_execution_trace(session_scope: str | None) -> None:
    if not session_scope:
        return
    with _TRACE_LOCK:
        _ACTIVE_TRACES.pop(session_scope, None)


def record_tool_trace_start(
    session_scope: str | None,
    *,
    name: str,
    category: str,
    risk_level: str,
    summary: str,
    arguments: dict[str, Any],
) -> None:
    if not session_scope:
        return
    with _TRACE_LOCK:
        trace = _ACTIVE_TRACES.get(session_scope)
        if trace is None:
            return
        trace.steps.append(
            TraceStep(
                name=name,
                category=category,
                risk_level=risk_level,
                summary=_compact(summary),
                started_at=_now_iso(),
                touched_files=_extract_touched_files(arguments),
            )
        )


def record_tool_trace_finish(session_scope: str | None, *, name: str, success: bool, result_excerpt: str) -> None:
    if not session_scope:
        return
    with _TRACE_LOCK:
        trace = _ACTIVE_TRACES.get(session_scope)
        if trace is None:
            return
        for step in reversed(trace.steps):
            if step.name == name and not step.finished_at:
                step.finished_at = _now_iso()
                step.success = bool(success)
                step.result_excerpt = _compact(result_excerpt)
                return


def get_execution_trace_snapshot(session_scope: str | None) -> dict[str, Any]:
    if not session_scope:
        return {"used_tools": (), "used_workflows": (), "tool_sequence": (), "touched_files": ()}
    with _TRACE_LOCK:
        trace = _ACTIVE_TRACES.get(session_scope)
        if trace is None:
            return {"used_tools": (), "used_workflows": (), "tool_sequence": (), "touched_files": ()}
        steps = list(trace.steps)

    used_tools: list[str] = []
    used_workflows: list[str] = []
    tool_sequence: list[str] = []
    touched_files: list[str] = []
    seen_tools: set[str] = set()
    seen_workflows: set[str] = set()
    seen_files: set[str] = set()

    for step in steps:
        tool_sequence.append(step.name)
        if step.category == "workflows":
            if step.name not in seen_workflows:
                seen_workflows.add(step.name)
                used_workflows.append(step.name)
        else:
            if step.name not in seen_tools:
                seen_tools.add(step.name)
                used_tools.append(step.name)
        for file_path in step.touched_files:
            if file_path not in seen_files:
                seen_files.add(file_path)
                touched_files.append(file_path)

    return {
        "used_tools": tuple(used_tools),
        "used_workflows": tuple(used_workflows),
        "tool_sequence": tuple(tool_sequence),
        "touched_files": tuple(touched_files[:16]),
    }


def get_execution_trace_details(session_scope: str | None) -> dict[str, Any]:
    if not session_scope:
        return {"prompt": "", "steps": []}
    with _TRACE_LOCK:
        trace = _ACTIVE_TRACES.get(session_scope)
        if trace is None:
            return {"prompt": "", "steps": []}
        steps = [
            {
                "name": step.name,
                "category": step.category,
                "risk_level": step.risk_level,
                "summary": step.summary,
                "started_at": step.started_at,
                "finished_at": step.finished_at,
                "success": step.success,
                "result_excerpt": step.result_excerpt,
                "touched_files": list(step.touched_files),
            }
            for step in trace.steps
        ]
        return {
            "prompt": trace.prompt,
            "channel": trace.channel,
            "created_at": trace.created_at,
            "steps": steps,
        }
