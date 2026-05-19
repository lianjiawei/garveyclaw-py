from __future__ import annotations



import json

from datetime import datetime

from threading import Lock

from typing import Any

from uuid import uuid4



from weclaw.config import AGENT_CLUSTER_MAX_EVENTS, CLUSTER_RUNTIME_FILE

from weclaw.core.types import ConversationRef



from .models import ClusterAgent, ClusterBlueprint, ClusterReviewOutcome, ClusterTask



_STORE_LOCK = Lock()

COMPLETED_RUN_DASHBOARD_TTL_SECONDS = 30
ACTIVE_RUN_DASHBOARD_TTL_SECONDS = 900



DEFAULT_CLUSTER_RUNTIME_STATE: dict[str, Any] = {

    "runs": {},

    "run_order": [],

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





def _compact(text: str | None, limit: int = 220) -> str:

    if not text:

        return ""

    normalized = " ".join(str(text).split())

    if len(normalized) <= limit:

        return normalized

    return normalized[: limit - 3] + "..."





def _ensure_store_file() -> None:

    CLUSTER_RUNTIME_FILE.parent.mkdir(parents=True, exist_ok=True)

    if not CLUSTER_RUNTIME_FILE.exists():

        CLUSTER_RUNTIME_FILE.write_text(json.dumps(DEFAULT_CLUSTER_RUNTIME_STATE, ensure_ascii=False, indent=2), encoding="utf-8")





def load_cluster_runtime_state() -> dict[str, Any]:

    _ensure_store_file()

    with _STORE_LOCK:

        try:

            raw = json.loads(CLUSTER_RUNTIME_FILE.read_text(encoding="utf-8"))

        except (OSError, json.JSONDecodeError):

            raw = {}

        state = json.loads(json.dumps(DEFAULT_CLUSTER_RUNTIME_STATE, ensure_ascii=False))

        state["runs"] = dict(raw.get("runs") or {})

        state["run_order"] = list(raw.get("run_order") or [])

        return state





def save_cluster_runtime_state(state: dict[str, Any]) -> None:

    _ensure_store_file()

    payload = {

        "runs": dict(state.get("runs") or {}),

        "run_order": list(state.get("run_order") or []),

    }

    with _STORE_LOCK:

        CLUSTER_RUNTIME_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")





def _update_state(mutator) -> dict[str, Any]:

    state = load_cluster_runtime_state()

    mutator(state)

    save_cluster_runtime_state(state)

    return state





def _build_agent_entries(agents: tuple[ClusterAgent, ...]) -> dict[str, Any]:

    now = _now_iso()

    return {

        agent.agent_id: {

            "agent_id": agent.agent_id,

            "name": agent.name,

            "role": agent.role,

            "spec_name": agent.spec_name or agent.agent_id,

            "status": "queued",

            "summary": _compact(agent.objective, 180),

            "updated_at": now,

        }

        for agent in agents

    }





def _agent_id_for_role(blueprint: ClusterBlueprint, role: str, fallback: str) -> str:

    for agent in blueprint.agents:

        if agent.role == role:

            return agent.agent_id

    return fallback





def build_cluster_tasks_from_blueprint(blueprint: ClusterBlueprint) -> tuple[ClusterTask, ...]:

    tasks: list[ClusterTask] = []

    planner_id = _agent_id_for_role(blueprint, "planner", "planner")

    executor_id = _agent_id_for_role(blueprint, "executor", "executor")

    for index, step in enumerate(blueprint.planned_steps, start=1):

        assigned_agent = planner_id if index == 1 else executor_id

        tasks.append(

            ClusterTask(

                task_id=f"{blueprint.cluster_id}:task:{index}",

                cluster_id=blueprint.cluster_id,

                title=step,

                assigned_agent=assigned_agent,

                depends_on=(tasks[-1].task_id,) if tasks else (),

            )

        )

    return tuple(tasks)





def _build_task_entries(blueprint: ClusterBlueprint) -> list[dict[str, Any]]:

    return _task_entries_from_tasks(build_cluster_tasks_from_blueprint(blueprint))





def _task_entries_from_tasks(tasks: tuple[ClusterTask, ...]) -> list[dict[str, Any]]:

    return [

        {

            "task_id": task.task_id,

            "cluster_id": task.cluster_id,

            "title": task.title,

            "assigned_agent": task.assigned_agent,

            "state": task.state,

            "depends_on": list(task.depends_on),

            "input_payload": task.input_payload,

            "output_payload": task.output_payload,

            "attempt_count": task.attempt_count,

            "max_attempts": task.max_attempts,

            "review_outcome": task.review_outcome,

            "review_summary": task.review_summary,

        }

        for task in tasks

    ]





def _append_message(run: dict[str, Any], *, from_agent: str, to_agent: str, kind: str, content: str) -> None:

    messages = list(run.get("messages") or [])

    messages.append(

        {

            "message_id": uuid4().hex,

            "cluster_id": run.get("cluster_id") or "",

            "from_agent": from_agent,

            "to_agent": to_agent,

            "kind": kind,

            "content": _compact(content, 220),

            "created_at": _now_iso(),

        }

    )

    run["messages"] = messages[-AGENT_CLUSTER_MAX_EVENTS:]





def _append_event(run: dict[str, Any], *, kind: str, agent_id: str, summary: str, detail: str = "") -> None:

    events = list(run.get("events") or [])

    events.append(

        {

            "cluster_id": run.get("cluster_id") or "",

            "kind": kind,

            "agent_id": agent_id,

            "summary": _compact(summary, 120),

            "detail": _compact(detail, 220),

            "created_at": _now_iso(),

        }

    )

    run["events"] = events[-AGENT_CLUSTER_MAX_EVENTS:]

    run["last_event_at"] = _now_iso()





def _set_agent_status(run: dict[str, Any], agent_id: str, *, status: str, summary: str = "") -> None:

    agents = dict(run.get("agents") or {})

    current = dict(agents.get(agent_id) or {})

    if not current:

        current = {

            "agent_id": agent_id,

            "name": agent_id.title(),

            "role": agent_id,

        }

    current.update(

        {

            "status": status,

            "summary": _compact(summary, 180),

            "updated_at": _now_iso(),

        }

    )

    agents[agent_id] = current

    run["agents"] = agents





def _set_task_state(run: dict[str, Any], assigned_agent: str, state: str, output_payload: str = "") -> None:

    tasks = list(run.get("tasks") or [])

    updated = False

    for task in tasks:

        if task.get("assigned_agent") == assigned_agent and task.get("state") in {"queued", "in_progress", "waiting"}:

            task["state"] = state

            if output_payload:

                task["output_payload"] = _compact(output_payload, 220)

            updated = True

            if state != "done":

                break

    if updated:

        run["tasks"] = tasks





def start_cluster_run(conversation: ConversationRef, blueprint: ClusterBlueprint) -> None:

    now = _now_iso()



    def mutator(state: dict[str, Any]) -> None:

        runs = dict(state.get("runs") or {})

        run = {

            "cluster_id": blueprint.cluster_id,

            "session_scope": conversation.session_scope,

            "conversation_key": conversation.conversation_key,

            "channel": conversation.channel,

            "objective": _compact(blueprint.objective, 180),

            "mode": blueprint.mode,

            "state": "planning",

            "agents": _build_agent_entries(blueprint.agents),

            "tasks": _build_task_entries(blueprint),

            "messages": [],

            "events": [],

            "planned_steps": list(blueprint.planned_steps),

            "active_agents": [agent.agent_id for agent in blueprint.agents],

            "current_task_id": "",

            "current_role": "planner",

            "created_at": now,

            "updated_at": now,

            "last_event_at": now,

        }

        runs[blueprint.cluster_id] = run

        order = [item for item in state.get("run_order") or [] if item != blueprint.cluster_id]

        order.append(blueprint.cluster_id)

        state["runs"] = runs

        state["run_order"] = order[-200:]



    _update_state(mutator)





def record_cluster_event(cluster_id: str, *, kind: str, agent_id: str, summary: str, detail: str = "") -> None:

    def mutator(state: dict[str, Any]) -> None:

        runs = dict(state.get("runs") or {})

        run = dict(runs.get(cluster_id) or {})

        if not run:

            return

        _append_event(run, kind=kind, agent_id=agent_id, summary=summary, detail=detail)

        run["updated_at"] = _now_iso()

        runs[cluster_id] = run

        state["runs"] = runs



    _update_state(mutator)





def replace_cluster_tasks(cluster_id: str, tasks: tuple[ClusterTask, ...]) -> None:

    def mutator(state: dict[str, Any]) -> None:

        runs = dict(state.get("runs") or {})

        run = dict(runs.get(cluster_id) or {})

        if not run:

            return

        run["tasks"] = _task_entries_from_tasks(tasks)

        run["planned_steps"] = [task.title for task in tasks]

        run["updated_at"] = _now_iso()

        runs[cluster_id] = run

        state["runs"] = runs



    _update_state(mutator)





def mark_cluster_agent_started(cluster_id: str, agent_id: str, summary: str) -> None:

    def mutator(state: dict[str, Any]) -> None:

        runs = dict(state.get("runs") or {})

        run = dict(runs.get(cluster_id) or {})

        if not run:

            return

        _set_agent_status(run, agent_id, status="working", summary=summary)

        _set_task_state(run, agent_id, "in_progress")

        _append_event(run, kind="agent_started", agent_id=agent_id, summary=summary)

        run["updated_at"] = _now_iso()

        runs[cluster_id] = run

        state["runs"] = runs



    _update_state(mutator)





def mark_cluster_agent_waiting(cluster_id: str, agent_id: str, waiting_text: str) -> None:

    def mutator(state: dict[str, Any]) -> None:

        runs = dict(state.get("runs") or {})

        run = dict(runs.get(cluster_id) or {})

        if not run:

            return

        run["state"] = "waiting"

        _set_agent_status(run, agent_id, status="waiting", summary=waiting_text)

        _set_task_state(run, agent_id, "waiting", output_payload=waiting_text)

        _append_event(run, kind="agent_note", agent_id=agent_id, summary="Agent waiting", detail=waiting_text)

        _append_message(run, from_agent=agent_id, to_agent="user", kind="waiting", content=waiting_text)

        run["updated_at"] = _now_iso()

        runs[cluster_id] = run

        state["runs"] = runs



    _update_state(mutator)





def mark_cluster_agent_finished(cluster_id: str, agent_id: str, summary: str, *, message_kind: str = "result") -> None:

    def mutator(state: dict[str, Any]) -> None:

        runs = dict(state.get("runs") or {})

        run = dict(runs.get(cluster_id) or {})

        if not run:

            return

        _set_agent_status(run, agent_id, status="done", summary=summary)

        _set_task_state(run, agent_id, "done", output_payload=summary)

        _append_event(run, kind="agent_finished", agent_id=agent_id, summary=summary)

        _append_message(run, from_agent=agent_id, to_agent="cluster", kind=message_kind, content=summary)

        run["state"] = "working"

        run["updated_at"] = _now_iso()

        runs[cluster_id] = run

        state["runs"] = runs



    _update_state(mutator)





def _set_task_state_by_id(run: dict[str, Any], task_id: str, state: str, output_payload: str = "") -> None:

    tasks = list(run.get("tasks") or [])

    for task in tasks:

        if task.get("task_id") != task_id:

            continue

        task["state"] = state

        if output_payload:

            task["output_payload"] = _compact(output_payload, 220)

        break

    run["tasks"] = tasks





def _update_task_by_id(

    run: dict[str, Any],

    task_id: str,

    *,

    state: str | None = None,

    output_payload: str | None = None,

    attempt_count: int | None = None,

    review_outcome: ClusterReviewOutcome | None = None,

    review_summary: str | None = None,

) -> None:

    tasks = list(run.get("tasks") or [])

    for task in tasks:

        if task.get("task_id") != task_id:

            continue

        if state is not None:

            task["state"] = state

        if output_payload is not None:

            task["output_payload"] = _compact(output_payload, 220)

        if attempt_count is not None:

            task["attempt_count"] = max(0, int(attempt_count))

        if review_outcome is not None:

            task["review_outcome"] = review_outcome

        if review_summary is not None:

            task["review_summary"] = _compact(review_summary, 220)

        break

    run["tasks"] = tasks





def _set_current_execution(run: dict[str, Any], *, task_id: str = "", role: str = "") -> None:

    run["current_task_id"] = task_id

    run["current_role"] = role


def _finalize_agent_statuses(run: dict[str, Any], *, success: bool, summary: str) -> None:

    agents = dict(run.get("agents") or {})

    if not agents:

        return

    terminal_summary = _compact(summary or ("已完成" if success else "执行失败"), 180)

    for agent_id, entry in list(agents.items()):

        current = dict(entry or {})

        status = str(current.get("status") or "").lower()

        if success:

            if status != "error":

                current["status"] = "done"

                if status in {"working", "reviewing", "waiting", "queued", "planning"} or not current.get("summary"):

                    current["summary"] = terminal_summary

        elif status in {"working", "reviewing", "waiting", "queued", "planning"}:

            current["status"] = "error"

            current["summary"] = terminal_summary

        current["updated_at"] = _now_iso()

        agents[agent_id] = current

    run["agents"] = agents





def mark_cluster_task_started(cluster_id: str, task_id: str, agent_id: str, summary: str) -> None:

    def mutator(state: dict[str, Any]) -> None:

        runs = dict(state.get("runs") or {})

        run = dict(runs.get(cluster_id) or {})

        if not run:

            return

        role = str((run.get("agents") or {}).get(agent_id, {}).get("role") or agent_id)

        run["state"] = "reviewing" if role == "reviewer" else "working"

        _set_current_execution(run, task_id=task_id, role=role)

        _set_agent_status(run, agent_id, status="working", summary=summary)

        _set_task_state_by_id(run, task_id, "in_progress")

        _append_event(run, kind="task_started", agent_id=agent_id, summary=summary, detail=task_id)

        run["updated_at"] = _now_iso()

        runs[cluster_id] = run

        state["runs"] = runs



    _update_state(mutator)





def mark_cluster_task_finished(cluster_id: str, task_id: str, agent_id: str, output: str, *, success: bool = True) -> None:

    def mutator(state: dict[str, Any]) -> None:

        runs = dict(state.get("runs") or {})

        run = dict(runs.get(cluster_id) or {})

        if not run:

            return

        role = str((run.get("agents") or {}).get(agent_id, {}).get("role") or agent_id)

        task_state = "review_required" if success and role == "executor" else ("done" if success else "error")

        agent_state = "done" if success else "error"

        event_kind = "task_finished" if success else "task_failed"

        message_kind = "result" if success else "error"

        _set_agent_status(run, agent_id, status=agent_state, summary=output)

        _set_task_state_by_id(run, task_id, task_state, output)

        _append_event(run, kind=event_kind, agent_id=agent_id, summary=output or task_state, detail=task_id)

        _append_message(run, from_agent=agent_id, to_agent="cluster", kind=message_kind, content=output)

        if not success:

            run["state"] = "error"

            _set_current_execution(run, task_id=task_id, role=role)

        elif role == "executor":

            run["state"] = "reviewing"

            _set_current_execution(run, task_id=task_id, role="reviewer")

        else:

            run["state"] = "working"

            _set_current_execution(run)

        run["updated_at"] = _now_iso()

        runs[cluster_id] = run

        state["runs"] = runs



    _update_state(mutator)





def finish_cluster_run(cluster_id: str, success: bool, summary: str) -> None:

    def mutator(state: dict[str, Any]) -> None:

        runs = dict(state.get("runs") or {})

        run = dict(runs.get(cluster_id) or {})

        if not run:

            return

        run["state"] = "done" if success else "error"

        _finalize_agent_statuses(run, success=success, summary=summary)

        _set_current_execution(run)

        _append_event(

            run,

            kind="cluster_finished",

            agent_id="planner",

            summary="Cluster finished" if success else "Cluster failed",

            detail=summary,

        )

        _append_message(run, from_agent="cluster", to_agent="user", kind="final", content=summary)

        run["updated_at"] = _now_iso()

        runs[cluster_id] = run

        state["runs"] = runs



    _update_state(mutator)





def mark_cluster_task_reviewed(

    cluster_id: str,

    task_id: str,

    reviewer_agent_id: str,

    *,

    outcome: ClusterReviewOutcome,

    summary: str,

    next_state: str,

    attempt_count: int | None = None,

) -> None:

    def mutator(state: dict[str, Any]) -> None:

        runs = dict(state.get("runs") or {})

        run = dict(runs.get(cluster_id) or {})

        if not run:

            return

        _set_agent_status(run, reviewer_agent_id, status="done", summary=summary)

        _update_task_by_id(

            run,

            task_id,

            state=next_state,

            attempt_count=attempt_count,

            review_outcome=outcome,

            review_summary=summary,

        )

        _append_event(run, kind="task_reviewed", agent_id=reviewer_agent_id, summary=summary, detail=f"{task_id} / {outcome}")

        _append_message(run, from_agent=reviewer_agent_id, to_agent="cluster", kind="review", content=summary)

        if outcome == "rejected":

            run["state"] = "error"

            _set_current_execution(run, task_id=task_id, role="reviewer")

        elif outcome == "changes_requested":

            run["state"] = "working"

            _set_current_execution(run, task_id=task_id, role="executor")

        else:

            run["state"] = "working"

            _set_current_execution(run)

        run["updated_at"] = _now_iso()

        runs[cluster_id] = run

        state["runs"] = runs



    _update_state(mutator)





def _run_age_seconds(run: dict[str, Any]) -> float | None:

    event_at = _parse_iso_datetime(str(run.get("last_event_at") or run.get("updated_at") or ""))

    if event_at is None:

        return None

    return max((datetime.now() - event_at).total_seconds(), 0)



def _select_dashboard_run(state: dict[str, Any]) -> dict[str, Any] | None:

    runs = dict(state.get("runs") or {})

    if not runs:

        return None

    order = list(state.get("run_order") or [])

    ordered_runs = [runs[item] for item in order if item in runs]

    if not ordered_runs:

        return None

    for run in reversed(ordered_runs):

        run_state = str(run.get("state") or "")

        if run_state in {"queued", "planning", "working", "reviewing", "waiting"}:

            age_seconds = _run_age_seconds(run)

            if age_seconds is None or age_seconds <= ACTIVE_RUN_DASHBOARD_TTL_SECONDS:

                return run

            continue

        if run_state in {"done", "error"}:

            age_seconds = _run_age_seconds(run)

            if age_seconds is not None and age_seconds <= COMPLETED_RUN_DASHBOARD_TTL_SECONDS:

                return run

            continue

        return run

    return None





def build_cluster_projection() -> dict[str, Any]:

    state = load_cluster_runtime_state()

    run = _select_dashboard_run(state)

    if run is None:

        return {

            "agents": [],

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

                "current_task_id": "",

                "current_role": "",

                "messages": [],

                "tasks": [],

            },

        }

    return {

        "agents": list((run.get("agents") or {}).values()),

        "cluster": {

            "enabled": True,

            "cluster_id": str(run.get("cluster_id") or ""),

            "state": str(run.get("state") or "idle"),

            "objective": str(run.get("objective") or ""),

            "active_agents": list(run.get("active_agents") or []),

            "planned_steps": list(run.get("planned_steps") or []),

            "events": list(run.get("events") or []),

            "last_event_at": str(run.get("last_event_at") or ""),

            "updated_at": str(run.get("updated_at") or ""),

            "current_task_id": str(run.get("current_task_id") or ""),

            "current_role": str(run.get("current_role") or ""),

            "messages": list(run.get("messages") or []),

            "tasks": list(run.get("tasks") or []),

        },

    }
