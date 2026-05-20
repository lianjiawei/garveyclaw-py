from __future__ import annotations



from datetime import datetime

from typing import Any



from weclaw.core.agent_activity import build_agent_activity_snapshot



ROLE_AGENT_IDS = {

    "main": 1,

    "primary": 1,

    "planner": 101,

    "executor": 102,

    "reviewer": 103,

}



ROLE_LABELS = {

    "primary": "\u4e3b\u667a\u80fd\u4f53",

    "main": "\u4e3b\u667a\u80fd\u4f53",

    "planner": "\u89c4\u5212\u5458",

    "executor": "\u6267\u884c\u5458",

    "reviewer": "\u590d\u6838\u5458",

}



ROLE_PALETTES = {

    "primary": 3,

    "main": 3,

    "planner": 0,

    "executor": 1,

    "reviewer": 2,

}



PERMISSION_WAITING_MARKERS = (

    "\u6388\u6743",

    "\u786e\u8ba4",

    "\u5141\u8bb8",

    "\u6279\u51c6",

    "permission",

    "approval",

    "approve",

    "confirm",

)





def build_pixel_office_core_payload(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:

    """Translate WeClaw monitor state into pixel-office-core commands."""

    snapshot = snapshot or build_agent_activity_snapshot()

    cluster_state = str((snapshot.get("cluster") or {}).get("state") or "idle").lower()

    agent_entries = _select_agents(snapshot)

    commands: list[dict[str, Any]] = [

        {

            "type": "setAgents",

            "agents": [_agent_input(entry) for entry in agent_entries],

        }

    ]



    focused_agent_id: int | None = None

    for entry in agent_entries:

        command = _mode_command(entry, cluster_state=cluster_state)

        status_command = _status_command(entry)

        commands.append(command)

        commands.append(status_command)

        if focused_agent_id is None and command.get("mode") in {"working", "thinking", "waiting", "blocked"}:

            focused_agent_id = int(command["id"])



    commands.append({"type": "focusAgent", "id": focused_agent_id})



    return {

        "generated_at": datetime.now().isoformat(timespec="seconds"),

        "commands": commands,

        "agents": [_display_agent(entry, cluster_state=cluster_state) for entry in agent_entries],

        "cluster": snapshot.get("cluster") or {},

    }





def _select_agents(snapshot: dict[str, Any]) -> list[dict[str, Any]]:

    cluster_agents = [dict(item) for item in snapshot.get("agents") or [] if isinstance(item, dict)]

    if cluster_agents:

        ordered_cluster_agents = sorted(
            cluster_agents,
            key=lambda item: _sort_key(str(item.get("role") or item.get("agent_id") or "")),
        )
        return ordered_cluster_agents + [_main_agent_entry(snapshot, cluster_active=True)]



    return [_main_agent_entry(snapshot)]



def _main_agent_entry(snapshot: dict[str, Any], *, cluster_active: bool = False) -> dict[str, Any]:

    main = dict(snapshot.get("agent") or {})
    status = "idle" if cluster_active else main.get("state") or "idle"
    summary = "" if cluster_active else main.get("current_tool") or main.get("tool_status") or main.get("current_task") or ""

    return {

        "agent_id": main.get("agent_id") or "main",

        "name": main.get("name") or "Weclaw",

        "role": "primary",

        "status": status,

        "summary": summary,

    }





def _sort_key(role: str) -> int:

    order = {"planner": 0, "executor": 1, "reviewer": 2, "primary": 3, "main": 3}

    return order.get(role, 99)





def _agent_input(entry: dict[str, Any]) -> dict[str, Any]:

    role = _role(entry)

    return {

        "id": _numeric_id(entry),

        "label": _label(entry),

        "palette": ROLE_PALETTES.get(role, 4),

        "isActive": _status(entry) == "working",

        "currentTool": _tool_for_entry(entry),

    }





def _mode_command(entry: dict[str, Any], *, cluster_state: str = "idle") -> dict[str, Any]:

    return {

        "type": "setAgentMode",

        "id": _numeric_id(entry),

        "mode": _mode_for_entry(entry, cluster_state=cluster_state),

        "tool": _tool_for_entry(entry),

    }





def _display_agent(entry: dict[str, Any], *, cluster_state: str = "idle") -> dict[str, Any]:

    return {

        "id": _numeric_id(entry),

        "agent_id": str(entry.get("agent_id") or entry.get("id") or ""),

        "label": _label(entry),

        "role": _role(entry),

        "status": _status(entry),

        "mode": _mode_for_entry(entry, cluster_state=cluster_state),

        "summary": str(entry.get("summary") or ""),

    }





def _numeric_id(entry: dict[str, Any]) -> int:

    role = _role(entry)

    if role in ROLE_AGENT_IDS:

        return ROLE_AGENT_IDS[role]

    raw_id = str(entry.get("agent_id") or entry.get("id") or role or "agent")

    return 1000 + sum(ord(char) for char in raw_id) % 8000





def _label(entry: dict[str, Any]) -> str:

    role = _role(entry)

    return ROLE_LABELS.get(role) or str(entry.get("name") or entry.get("agent_id") or "\u667a\u80fd\u4f53")





def _role(entry: dict[str, Any]) -> str:

    return str(entry.get("role") or entry.get("agent_id") or "agent").lower()





def _status(entry: dict[str, Any]) -> str:

    return str(entry.get("status") or entry.get("state") or "idle").lower()





def _mode_for_entry(entry: dict[str, Any], *, cluster_state: str = "idle") -> str:

    status = _status(entry)

    role = _role(entry)

    if status == "planning":

        return "thinking"

    if status == "reviewing":

        return "thinking"

    if status == "working":

        return "working" if role == "executor" else "thinking"

    if status == "queued":

        return "idle"

    if status == "waiting":

        return "blocked" if _is_permission_wait(entry) else "waiting"

    if status == "error":

        return "blocked"

    if status == "done" and cluster_state in {"queued", "planning", "working", "reviewing", "waiting"}:

        return "thinking"

    return "idle"





def _is_permission_wait(entry: dict[str, Any]) -> bool:

    summary = str(entry.get("summary") or entry.get("current_tool") or "").lower()

    return any(marker in summary for marker in PERMISSION_WAITING_MARKERS)





def _tool_for_entry(entry: dict[str, Any]) -> str | None:

    status = _status(entry)

    if status != "working":

        return None



    role = _role(entry)

    summary = str(entry.get("summary") or entry.get("current_tool") or "").lower()

    if "grep" in summary or "\u641c\u7d22\u5de5\u4f5c\u533a" in summary:

        return "Grep"

    if "glob" in summary or "\u67e5\u627e\u6587\u4ef6" in summary:

        return "Glob"

    if "web_search" in summary or "web search" in summary or "\u8054\u7f51" in summary:

        return "WebSearch"

    if "read" in summary or "\u8bfb\u53d6" in summary or "\u5206\u6790" in summary or role in {"planner", "reviewer"}:

        return "Read"

    return "Write"





def _status_command(entry: dict[str, Any]) -> dict[str, Any]:

    status = _status(entry)

    mode = _mode_for_entry(entry)

    summary = str(entry.get("summary") or "")



    # Idle/queued agents should look alive in their own idle range, without a permanent bubble.

    if status in {"idle", "queued"}:

        if "\u5df2\u53d6\u6d88\u5de5\u5177" in summary:

            return {

                "type": "setAgentStatus",

                "id": _numeric_id(entry),

                "text": "\u5df2\u53d6\u6d88\u5de5\u5177",

                "detail": _compact_detail(summary),

                "ttlSeconds": 2,

            }

        return {"type": "setAgentStatus", "id": _numeric_id(entry), "text": "", "detail": ""}



    if mode in {"waiting", "blocked"}:

        return {

            "type": "setAgentStatus",

            "id": _numeric_id(entry),

            "text": _status_text(entry),

            "detail": _compact_detail(summary),

        }



    command: dict[str, Any] = {

        "type": "setAgentStatus",

        "id": _numeric_id(entry),

        "text": _status_text(entry),

        "detail": _compact_detail(summary),

    }

    if status == "done":

        command["ttlSeconds"] = 2

    return command





def _status_text(entry: dict[str, Any]) -> str:

    status = _status(entry)

    role = _role(entry)

    if status == "working":

        if role == "planner":

            return "\u89c4\u5212\u4e2d"

        if role == "reviewer":

            return "\u590d\u6838\u4e2d"

        return "\u6267\u884c\u4e2d"

    if status == "planning":

        return "\u89c4\u5212\u4e2d"

    if status == "reviewing":

        return "\u590d\u6838\u4e2d"

    if status == "waiting":

        return "\u9700\u8981\u6388\u6743" if _is_permission_wait(entry) else "\u7b49\u5f85\u4e2d"

    if status == "error":

        if "\u5df2\u53d6\u6d88\u5de5\u5177" in str(entry.get("summary") or ""):

            return "\u5df2\u53d6\u6d88\u5de5\u5177"

        return "\u6267\u884c\u53d7\u963b"

    if status == "done":

        return "\u5df2\u5b8c\u6210"

    return ""





def _compact_detail(value: str, max_length: int = 52) -> str:

    normalized = " ".join(value.split())

    if len(normalized) <= max_length:

        return normalized

    return normalized[: max_length - 1] + "\u2026"
