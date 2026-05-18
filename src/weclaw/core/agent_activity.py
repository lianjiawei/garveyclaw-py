from __future__ import annotations

import json
from datetime import datetime
from threading import Lock
from typing import Any

from weclaw.config import AGENT_CLUSTER_MAX_EVENTS, MONITOR_ACTIVITY_FILE
from weclaw.cluster.store import build_cluster_projection
from weclaw.core.types import ConversationRef

_STATE_LOCK = Lock()
STALE_ACTIVE_RUN_SECONDS = 900
STALE_IDLE_RUN_SECONDS = 120

DEFAULT_AGENT_ACTIVITY: dict[str, Any] = {
    "agent": {
        "agent_id": "main",
        "name": "Weclaw",
        "state": "idle",
        "current_task": "",
        "current_tool": "",
        "tool_status": "",
        "last_active_at": "",
        "active_runs": {},
        "last_error": "",
        "last_channel": "",
        "updated_at": "",
    },
    "agents": {},
    "cluster": {
        "enabled": False,
        "cluster_id": "",
        "state": "idle",
        "objective": "",
        "active_agents": [],
        "planned_steps": [],
        "events": [],
        "last_event_at": "",
        "updated_at": "",
    },
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _prune_stale_runs(agent: dict[str, Any]) -> None:
    active_runs = dict(agent.get("active_runs") or {})
    if not active_runs:
        return

    now = datetime.now()
    pruned: dict[str, Any] = {}
    for key, run in active_runs.items():
        if not isinstance(run, dict):
            continue
        last_event = _parse_iso_datetime(str(run.get("last_tool_at") or ""))
        if last_event is None:
            last_event = _parse_iso_datetime(str(run.get("started_at") or ""))
        if last_event is None:
            pruned[key] = run
            continue
        age_seconds = max((now - last_event).total_seconds(), 0)
        current_tool = str(run.get("current_tool") or "").strip()
        ttl_seconds = STALE_ACTIVE_RUN_SECONDS if current_tool else STALE_IDLE_RUN_SECONDS
        if age_seconds <= ttl_seconds:
            pruned[key] = run

    agent["active_runs"] = pruned
    if pruned:
        return

    if str(agent.get("state") or "idle") in {"working", "waiting"}:
        agent["state"] = "idle"
        agent["current_task"] = ""
        agent["current_tool"] = ""
        agent["tool_status"] = ""


def _compact_text(text: str | None, max_length: int = 120) -> str:
    if not text:
        return ""
    normalized = " ".join(str(text).split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 3] + "..."


def _sync_named_agent(state: dict[str, Any], agent_id: str, *, name: str, role: str, status: str, summary: str = "") -> None:
    agents = dict(state.get("agents") or {})
    previous = dict(agents.get(agent_id) or {})
    previous.update(
        {
            "agent_id": agent_id,
            "name": name,
            "role": role,
            "status": status,
            "summary": _compact_text(summary, 180),
            "updated_at": _now_iso(),
        }
    )
    agents[agent_id] = previous
    state["agents"] = agents


def _append_cluster_event(state: dict[str, Any], *, cluster_id: str, kind: str, agent_id: str, summary: str, detail: str = "") -> None:
    cluster = state["cluster"]
    events = list(cluster.get("events") or [])
    events.append(
        {
            "cluster_id": cluster_id,
            "kind": kind,
            "agent_id": agent_id,
            "summary": _compact_text(summary, 120),
            "detail": _compact_text(detail, 220),
            "created_at": _now_iso(),
        }
    )
    cluster["events"] = events[-AGENT_CLUSTER_MAX_EVENTS:]
    cluster["last_event_at"] = _now_iso()


def _ensure_state_file() -> None:
    MONITOR_ACTIVITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not MONITOR_ACTIVITY_FILE.exists():
        MONITOR_ACTIVITY_FILE.write_text(json.dumps(DEFAULT_AGENT_ACTIVITY, ensure_ascii=False, indent=2), encoding="utf-8")


def load_agent_activity_state() -> dict[str, Any]:
    _ensure_state_file()
    with _STATE_LOCK:
        try:
            raw = json.loads(MONITOR_ACTIVITY_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        merged = json.loads(json.dumps(DEFAULT_AGENT_ACTIVITY, ensure_ascii=False))
        merged_agent = merged["agent"]
        merged_agent.update(raw.get("agent") or {})
        merged_agent["active_runs"] = dict(merged_agent.get("active_runs") or {})
        merged["agents"] = dict(raw.get("agents") or {})
        merged_cluster = merged["cluster"]
        merged_cluster.update(raw.get("cluster") or {})
        merged_cluster["active_agents"] = list(merged_cluster.get("active_agents") or [])
        merged_cluster["planned_steps"] = list(merged_cluster.get("planned_steps") or [])
        merged_cluster["events"] = list(merged_cluster.get("events") or [])
        _prune_stale_runs(merged_agent)
        return merged


def save_agent_activity_state(state: dict[str, Any]) -> None:
    _ensure_state_file()
    payload = load_agent_activity_state()
    payload["agent"].update(state.get("agent") or {})
    payload["agents"] = dict(state.get("agents") or payload.get("agents") or {})
    payload["cluster"].update(state.get("cluster") or {})
    payload["agent"]["updated_at"] = _now_iso()
    payload["cluster"]["updated_at"] = _now_iso()
    with _STATE_LOCK:
        MONITOR_ACTIVITY_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _update_state(mutator) -> dict[str, Any]:
    state = load_agent_activity_state()
    mutator(state)
    save_agent_activity_state(state)
    return state


def mark_agent_run_started(conversation: ConversationRef, prompt: str) -> None:
    now = _now_iso()

    def mutator(state: dict[str, Any]) -> None:
        agent = state["agent"]
        active_runs = dict(agent.get("active_runs") or {})
        active_runs[conversation.conversation_key] = {
            "conversation_key": conversation.conversation_key,
            "channel": conversation.channel,
            "target_id": conversation.target_id,
            "session_scope": conversation.session_scope,
            "prompt": _compact_text(prompt),
            "started_at": now,
            "current_tool": "",
            "tool_status": "",
        }
        latest_run = active_runs[conversation.conversation_key]
        agent.update(
            {
                "state": "working",
                "current_task": latest_run["prompt"],
                "current_tool": "",
                "tool_status": "",
                "last_active_at": now,
                "last_channel": conversation.channel,
                "last_error": "",
                "active_runs": active_runs,
            }
        )
        _sync_named_agent(state, "main", name="Main Agent", role="primary", status="working", summary=latest_run["prompt"])

    _update_state(mutator)


def _pick_latest_run(active_runs: dict[str, Any]) -> tuple[str, dict[str, Any]] | tuple[None, None]:
    if not active_runs:
        return None, None
    latest_key = max(active_runs, key=lambda key: str(active_runs[key].get("started_at") or ""))
    return latest_key, dict(active_runs[latest_key])


def mark_agent_tool_started(conversation: ConversationRef, tool_name: str, tool_status: str | None = None) -> None:
    now = _now_iso()
    safe_tool_name = _compact_text(tool_name, 80)
    safe_status = _compact_text(tool_status, 160)

    def mutator(state: dict[str, Any]) -> None:
        agent = state["agent"]
        active_runs = dict(agent.get("active_runs") or {})
        run = dict(active_runs.get(conversation.conversation_key) or {})
        if not run:
            run = {
                "conversation_key": conversation.conversation_key,
                "channel": conversation.channel,
                "target_id": conversation.target_id,
                "session_scope": conversation.session_scope,
                "prompt": "",
                "started_at": now,
            }
        run["current_tool"] = safe_tool_name
        run["tool_status"] = safe_status
        run["last_tool_at"] = now
        active_runs[conversation.conversation_key] = run
        agent.update(
            {
                "state": "working",
                "current_tool": safe_tool_name,
                "tool_status": safe_status,
                "last_active_at": now,
                "active_runs": active_runs,
            }
        )
        if run.get("prompt"):
            agent["current_task"] = run["prompt"]
        _sync_named_agent(state, "main", name="Main Agent", role="primary", status="working", summary=safe_tool_name or run.get("prompt") or "")

    _update_state(mutator)


def mark_agent_tool_finished(conversation: ConversationRef, tool_name: str, tool_status: str | None = None) -> None:
    now = _now_iso()
    safe_status = _compact_text(tool_status, 160)

    def mutator(state: dict[str, Any]) -> None:
        agent = state["agent"]
        active_runs = dict(agent.get("active_runs") or {})
        run = dict(active_runs.get(conversation.conversation_key) or {})
        if run:
            if str(run.get("current_tool") or "") == tool_name:
                run["current_tool"] = ""
            run["tool_status"] = safe_status
            run["last_tool_at"] = now
            active_runs[conversation.conversation_key] = run
        latest_key, latest_run = _pick_latest_run(active_runs)
        agent.update(
            {
                "state": "working" if active_runs else "idle",
                "current_tool": str((latest_run or {}).get("current_tool") or ""),
                "tool_status": str((latest_run or {}).get("tool_status") or ""),
                "current_task": str((latest_run or {}).get("prompt") or ""),
                "last_active_at": now,
                "active_runs": active_runs,
            }
        )
        _sync_named_agent(state, "main", name="Main Agent", role="primary", status="working" if active_runs else "idle", summary=safe_status or tool_name)

    _update_state(mutator)


def mark_agent_tool_cancelled(conversation: ConversationRef, tool_name: str, tool_status: str | None = None) -> None:
    now = _now_iso()
    safe_status = _compact_text(tool_status or tool_name, 160)

    def mutator(state: dict[str, Any]) -> None:
        agent = state["agent"]
        active_runs = dict(agent.get("active_runs") or {})
        active_runs.pop(conversation.conversation_key, None)
        _, latest_run = _pick_latest_run(active_runs)
        agent.update(
            {
                "state": "working" if active_runs else "idle",
                "current_tool": str((latest_run or {}).get("current_tool") or ""),
                "tool_status": safe_status,
                "current_task": str((latest_run or {}).get("prompt") or safe_status),
                "last_active_at": now,
                "active_runs": active_runs,
            }
        )
        _sync_named_agent(
            state,
            "main",
            name="Main Agent",
            role="primary",
            status="working" if active_runs else "idle",
            summary=safe_status,
        )

    _update_state(mutator)


def mark_agent_waiting(conversation: ConversationRef, waiting_text: str | None = None) -> None:
    now = _now_iso()
    safe_text = _compact_text(waiting_text, 160)

    def mutator(state: dict[str, Any]) -> None:
        agent = state["agent"]
        active_runs = dict(agent.get("active_runs") or {})
        run = dict(active_runs.get(conversation.conversation_key) or {})
        if run:
            run["current_tool"] = ""
            run["tool_status"] = safe_text
            active_runs[conversation.conversation_key] = run
        agent.update(
            {
                "state": "waiting",
                "current_tool": "",
                "tool_status": safe_text,
                "last_active_at": now,
                "active_runs": active_runs,
            }
        )
        if safe_text:
            agent["current_task"] = safe_text
        _sync_named_agent(state, "main", name="Main Agent", role="primary", status="waiting", summary=safe_text)

    _update_state(mutator)


def mark_agent_run_finished(conversation: ConversationRef, error: str | None = None) -> None:
    now = _now_iso()

    def mutator(state: dict[str, Any]) -> None:
        agent = state["agent"]
        active_runs = dict(agent.get("active_runs") or {})
        active_runs.pop(conversation.conversation_key, None)
        _, latest_run = _pick_latest_run(active_runs)
        agent.update(
            {
                "state": "working" if active_runs else "idle",
                "current_task": str((latest_run or {}).get("prompt") or ""),
                "current_tool": str((latest_run or {}).get("current_tool") or ""),
                "tool_status": str((latest_run or {}).get("tool_status") or ""),
                "last_active_at": now,
                "last_error": _compact_text(error, 180) if error else "",
                "active_runs": active_runs,
            }
        )
        _sync_named_agent(state, "main", name="Main Agent", role="primary", status="error" if error else ("working" if active_runs else "idle"), summary=error or str((latest_run or {}).get("prompt") or ""))

    _update_state(mutator)


def mark_cluster_run_started(
    conversation: ConversationRef,
    cluster_id: str,
    objective: str,
    agents: tuple[Any, ...],
    planned_steps: tuple[str, ...],
) -> None:
    now = _now_iso()

    def mutator(state: dict[str, Any]) -> None:
        cluster = state["cluster"]
        cluster.update(
            {
                "enabled": True,
                "cluster_id": cluster_id,
                "state": "working",
                "objective": _compact_text(objective, 180),
                "active_agents": [agent.agent_id for agent in agents],
                "planned_steps": list(planned_steps),
                "last_event_at": now,
            }
        )
        for agent in agents:
            _sync_named_agent(state, agent.agent_id, name=agent.name, role=agent.role, status="queued", summary=agent.objective)

    _update_state(mutator)


def mark_cluster_event(
    conversation: ConversationRef,
    cluster_id: str,
    *,
    kind: str,
    agent_id: str,
    summary: str,
    detail: str = "",
) -> None:
    def mutator(state: dict[str, Any]) -> None:
        _append_cluster_event(state, cluster_id=cluster_id, kind=kind, agent_id=agent_id, summary=summary, detail=detail)

    _update_state(mutator)


def mark_cluster_agent_started(conversation: ConversationRef, cluster_id: str, agent_id: str, summary: str) -> None:
    def mutator(state: dict[str, Any]) -> None:
        agents = dict(state.get("agents") or {})
        current = dict(agents.get(agent_id) or {})
        _sync_named_agent(
            state,
            agent_id,
            name=str(current.get("name") or agent_id.title()),
            role=str(current.get("role") or agent_id),
            status="working",
            summary=summary,
        )
        _append_cluster_event(state, cluster_id=cluster_id, kind="agent_started", agent_id=agent_id, summary=summary)

    _update_state(mutator)


def mark_cluster_agent_finished(conversation: ConversationRef, cluster_id: str, agent_id: str, summary: str) -> None:
    def mutator(state: dict[str, Any]) -> None:
        agents = dict(state.get("agents") or {})
        current = dict(agents.get(agent_id) or {})
        _sync_named_agent(
            state,
            agent_id,
            name=str(current.get("name") or agent_id.title()),
            role=str(current.get("role") or agent_id),
            status="done",
            summary=summary,
        )
        _append_cluster_event(state, cluster_id=cluster_id, kind="agent_finished", agent_id=agent_id, summary=summary)

    _update_state(mutator)


def mark_cluster_run_finished(conversation: ConversationRef, cluster_id: str, success: bool, summary: str) -> None:
    now = _now_iso()

    def mutator(state: dict[str, Any]) -> None:
        cluster = state["cluster"]
        cluster.update(
            {
                "enabled": True,
                "cluster_id": cluster_id,
                "state": "idle" if success else "error",
                "last_event_at": now,
            }
        )
        _append_cluster_event(
            state,
            cluster_id=cluster_id,
            kind="cluster_finished",
            agent_id="planner",
            summary="Cluster finished" if success else "Cluster failed",
            detail=summary,
        )

    _update_state(mutator)


def reply_requires_waiting(text: str) -> bool:
    normalized = " ".join((text or "").split()).strip()
    if not normalized:
        return False
    non_waiting_closers = (
        "需要我帮你做别的吗？",
        "需要我帮你做别的吗?",
        "还有什么我可以帮你的吗？",
        "还有什么我可以帮你的吗?",
        "还需要我继续吗？",
        "还需要我继续吗?",
        "要我继续吗？",
        "要我继续吗?",
        "如果你需要",
        "如需继续",
        "如需我继续",
    )
    if any(marker in normalized for marker in non_waiting_closers):
        return False

    if normalized.endswith(("?", "？")):
        waiting_question_markers = (
            "请确认",
            "请告诉我",
            "请提供",
            "你希望",
            "你想让我",
            "要不要",
            "是否需要",
            "麻烦你",
            "请问",
            "要我",
            "还是",
            "哪个",
            "哪一个",
        )
        return any(marker in normalized for marker in waiting_question_markers)

    waiting_markers = (
        "请确认",
        "请告诉我",
        "请提供",
        "你希望",
        "你想让我",
        "要不要",
        "是否需要",
        "麻烦你",
    )
    return any(marker in normalized for marker in waiting_markers)


def build_agent_activity_snapshot() -> dict[str, Any]:
    state = load_agent_activity_state()
    agent = state["agent"]
    active_runs = list((agent.get("active_runs") or {}).values())
    cluster_projection = build_cluster_projection()
    return {
        "agent": {
            "agent_id": str(agent.get("agent_id") or "main"),
            "name": str(agent.get("name") or "Weclaw"),
            "state": str(agent.get("state") or "idle"),
            "current_task": str(agent.get("current_task") or ""),
            "current_tool": str(agent.get("current_tool") or ""),
            "tool_status": str(agent.get("tool_status") or ""),
            "last_active_at": str(agent.get("last_active_at") or ""),
            "last_channel": str(agent.get("last_channel") or ""),
            "last_error": str(agent.get("last_error") or ""),
            "active_runs_count": len(active_runs),
            "active_runs": active_runs,
            "updated_at": str(agent.get("updated_at") or ""),
        },
        "agents": cluster_projection["agents"],
        "cluster": cluster_projection["cluster"],
    }
