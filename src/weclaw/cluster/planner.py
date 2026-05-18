from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .models import ClusterBlueprint, ClusterTask


class ClusterPlanError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PlannerTaskSpec:
    task_id: str
    title: str
    assigned_agent: str
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    input_payload: str = ""
    expected_output: str = ""


@dataclass(frozen=True, slots=True)
class PlannerTaskPlan:
    objective: str
    tasks: tuple[PlannerTaskSpec, ...]


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_first_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        raise ClusterPlanError("Planner response does not contain a JSON object.")
    in_string = False
    escape_next = False
    depth = 0
    for index in range(start, len(text)):
        ch = text[index]
        if in_string:
            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                continue
            if ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
            continue
        if ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
            continue
    raise ClusterPlanError("Planner response contains an incomplete JSON object.")


def _parse_planner_json(text: str) -> dict[str, Any]:
    candidates = [_strip_json_fence(text)]
    stripped = candidates[0]
    if not stripped.startswith("{"):
        try:
            candidates.append(_extract_first_json_object(stripped))
        except ClusterPlanError:
            pass
    else:
        try:
            candidates.append(_extract_first_json_object(stripped))
        except ClusterPlanError:
            pass
    last_error: json.JSONDecodeError | None = None
    for candidate in candidates:
        try:
            raw = json.loads(candidate)
            if not isinstance(raw, dict):
                raise ClusterPlanError("Planner task plan must be a JSON object.")
            return raw
        except json.JSONDecodeError as exc:
            last_error = exc
    if len(candidates) == 1:
        raise ClusterPlanError("Planner task plan must be valid JSON: no JSON object found.")
    if last_error is not None:
        raise ClusterPlanError(f"Planner task plan must be valid JSON: {last_error}") from last_error
    raise ClusterPlanError("Planner task plan must be valid JSON.")


def _string_list(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ClusterPlanError(f"Planner task field '{field_name}' must be a list of strings.")
    return tuple(item.strip() for item in value if item.strip())


def parse_planner_task_plan(text: str) -> PlannerTaskPlan:
    raw = _parse_planner_json(text)
    objective = str(raw.get("objective") or "").strip()
    tasks_raw = raw.get("tasks")
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise ClusterPlanError("Planner task plan must contain a non-empty tasks list.")

    tasks: list[PlannerTaskSpec] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(tasks_raw, start=1):
        if not isinstance(item, dict):
            raise ClusterPlanError(f"Planner task #{index} must be an object.")
        task_id = str(item.get("id") or item.get("task_id") or "").strip()
        title = str(item.get("title") or "").strip()
        assigned_agent = str(item.get("agent") or item.get("assigned_agent") or "").strip()
        if not task_id:
            task_id = f"task_{index}"
        if task_id in seen_ids:
            raise ClusterPlanError(f"Duplicate planner task id: {task_id}")
        if not title:
            raise ClusterPlanError(f"Planner task '{task_id}' must define title.")
        if not assigned_agent:
            raise ClusterPlanError(f"Planner task '{task_id}' must define agent.")
        depends_on = _string_list(item.get("depends_on"), "depends_on")
        if task_id in depends_on:
            raise ClusterPlanError(f"Planner task '{task_id}' cannot depend on itself.")
        tasks.append(
            PlannerTaskSpec(
                task_id=task_id,
                title=title,
                assigned_agent=assigned_agent,
                depends_on=depends_on,
                input_payload=str(item.get("input") or item.get("input_payload") or "").strip(),
                expected_output=str(item.get("expected_output") or "").strip(),
            )
        )
        seen_ids.add(task_id)

    defined_ids = {task.task_id for task in tasks}
    for task in tasks:
        missing = [dep for dep in task.depends_on if dep not in defined_ids]
        if missing:
            raise ClusterPlanError(f"Planner task '{task.task_id}' depends on unknown task(s): {', '.join(missing)}")

    return PlannerTaskPlan(objective=objective, tasks=_topologically_order_tasks(tuple(tasks)))


def _topologically_order_tasks(tasks: tuple[PlannerTaskSpec, ...]) -> tuple[PlannerTaskSpec, ...]:
    by_id = {task.task_id: task for task in tasks}
    remaining = set(by_id)
    ordered: list[PlannerTaskSpec] = []
    while remaining:
        ready = sorted(
            task_id
            for task_id in remaining
            if all(dep not in remaining for dep in by_id[task_id].depends_on)
        )
        if not ready:
            cycle = ", ".join(sorted(remaining))
            raise ClusterPlanError(f"Planner task plan contains a dependency cycle: {cycle}")
        for task_id in ready:
            ordered.append(by_id[task_id])
            remaining.remove(task_id)
    return tuple(ordered)


def cluster_tasks_from_planner_plan(plan: PlannerTaskPlan, blueprint: ClusterBlueprint) -> tuple[ClusterTask, ...]:
    known_agents = {agent.agent_id for agent in blueprint.agents}
    known_specs = {agent.spec_name for agent in blueprint.agents if agent.spec_name}
    tasks: list[ClusterTask] = []
    for task in plan.tasks:
        if task.assigned_agent not in known_agents and task.assigned_agent not in known_specs:
            raise ClusterPlanError(f"Planner task '{task.task_id}' uses unknown agent: {task.assigned_agent}")
        assigned_agent = task.assigned_agent
        if assigned_agent not in known_agents:
            for agent in blueprint.agents:
                if agent.spec_name == assigned_agent:
                    assigned_agent = agent.agent_id
                    break
        tasks.append(
            ClusterTask(
                task_id=f"{blueprint.cluster_id}:{task.task_id}",
                cluster_id=blueprint.cluster_id,
                title=task.title,
                assigned_agent=assigned_agent,
                depends_on=tuple(f"{blueprint.cluster_id}:{dep}" for dep in task.depends_on),
                input_payload=task.input_payload,
                output_payload=task.expected_output,
            )
        )
    return tuple(tasks)
