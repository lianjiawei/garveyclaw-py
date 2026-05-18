from __future__ import annotations

import json
from datetime import datetime

from weclaw.config import DATA_DIR
from weclaw.decision.models import DecisionPlan, ExecutionOutcome, TaskLineState

EXECUTION_OUTCOMES_FILE = DATA_DIR / "execution_outcomes.jsonl"
SESSION_CAPABILITY_PREFERENCES_DIR = DATA_DIR / "session_capability_preferences"
SESSION_USER_CONSTRAINTS_DIR = DATA_DIR / "session_user_constraints"
SESSION_TASK_LINE_DIR = DATA_DIR / "session_task_lines"


def _sanitize_scope(scope: str | None) -> str:
    if not scope:
        return "default"
    return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in scope).strip("_") or "default"


def session_capability_preferences_file(scope: str | None) -> str:
    return str(SESSION_CAPABILITY_PREFERENCES_DIR / f"{_sanitize_scope(scope)}.json")


def load_session_capability_preferences(scope: str | None) -> dict[str, object]:
    SESSION_CAPABILITY_PREFERENCES_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSION_CAPABILITY_PREFERENCES_DIR / f"{_sanitize_scope(scope)}.json"
    if not path.exists():
        return {
            "session_scope": scope or "",
            "updated_at": "",
            "last_strategy": "",
            "last_goal": "",
            "last_intent_type": "",
            "preferred_skills": [],
            "preferred_workflows": [],
            "recent_tools": [],
            "last_success": False,
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "session_scope": scope or "",
            "updated_at": "",
            "last_strategy": "",
            "last_goal": "",
            "last_intent_type": "",
            "preferred_skills": [],
            "preferred_workflows": [],
            "recent_tools": [],
            "last_success": False,
        }


def load_session_user_constraints(scope: str | None) -> dict[str, object]:
    SESSION_USER_CONSTRAINTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSION_USER_CONSTRAINTS_DIR / f"{_sanitize_scope(scope)}.json"
    if not path.exists():
        return {"session_scope": scope or "", "updated_at": "", "active_constraints": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"session_scope": scope or "", "updated_at": "", "active_constraints": []}


def save_session_user_constraints(scope: str | None, constraints: tuple[str, ...]) -> None:
    SESSION_USER_CONSTRAINTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSION_USER_CONSTRAINTS_DIR / f"{_sanitize_scope(scope)}.json"
    deduped: list[str] = []
    seen: set[str] = set()
    for item in constraints:
        normalized = str(item).strip()
        lowered = normalized.lower()
        if not normalized or lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(normalized)
    payload = {
        "session_scope": scope or "",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "active_constraints": deduped[:8],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_session_task_line(scope: str | None) -> TaskLineState:
    SESSION_TASK_LINE_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSION_TASK_LINE_DIR / f"{_sanitize_scope(scope)}.json"
    if not path.exists():
        return TaskLineState()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return TaskLineState()
    return TaskLineState(
        primary_goal=str(payload.get("primary_goal") or "").strip(),
        active_subtask=str(payload.get("active_subtask") or "").strip(),
        stage=str(payload.get("stage") or "").strip(),
        carried_constraints=tuple(str(item).strip() for item in payload.get("carried_constraints") or [] if str(item).strip()),
        updated_at=str(payload.get("updated_at") or "").strip(),
    )


def save_session_task_line(
    scope: str | None,
    *,
    primary_goal: str,
    active_subtask: str,
    stage: str,
    carried_constraints: tuple[str, ...],
) -> None:
    SESSION_TASK_LINE_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSION_TASK_LINE_DIR / f"{_sanitize_scope(scope)}.json"
    payload = {
        "session_scope": scope or "",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "primary_goal": primary_goal.strip()[:240],
        "active_subtask": active_subtask.strip()[:240],
        "stage": stage.strip()[:80],
        "carried_constraints": [str(item).strip() for item in carried_constraints if str(item).strip()][:8],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def save_session_capability_preferences(
    scope: str | None,
    *,
    strategy: str,
    goal: str,
    intent_type: str,
    used_skills: tuple[str, ...],
    used_workflows: tuple[str, ...],
    used_tools: tuple[str, ...],
    success: bool,
) -> None:
    SESSION_CAPABILITY_PREFERENCES_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSION_CAPABILITY_PREFERENCES_DIR / f"{_sanitize_scope(scope)}.json"
    payload = {
        "session_scope": scope or "",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "last_strategy": strategy,
        "last_goal": goal,
        "last_intent_type": intent_type,
        "preferred_skills": list(used_skills),
        "preferred_workflows": list(used_workflows),
        "recent_tools": list(used_tools[:8]),
        "last_success": bool(success),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _serialize_decision_plan(plan: DecisionPlan) -> dict[str, object]:
    return {
        "task_intent": {
            "intent_type": plan.task_intent.intent_type,
            "goal": plan.task_intent.goal,
            "request_style": plan.task_intent.request_style,
            "target": plan.task_intent.target,
            "constraints": list(plan.task_intent.constraints),
            "expected_output": plan.task_intent.expected_output,
            "is_followup": plan.task_intent.is_followup,
            "confidence": plan.task_intent.confidence,
            "source": plan.task_intent.source,
            "context_summary": plan.task_intent.context_summary,
            "inferred_from_context": plan.task_intent.inferred_from_context,
        },
        "task_line": {
            "primary_goal": plan.task_line.primary_goal,
            "active_subtask": plan.task_line.active_subtask,
            "stage": plan.task_line.stage,
            "carried_constraints": list(plan.task_line.carried_constraints),
            "updated_at": plan.task_line.updated_at,
        },
        "intent_type": plan.intent_type,
        "strategy": plan.strategy,
        "continuation": {
            "continue_previous_strategy": plan.continuation.continue_previous_strategy,
            "preferred_strategy": plan.continuation.preferred_strategy,
            "preferred_skills": list(plan.continuation.preferred_skills),
            "preferred_workflows": list(plan.continuation.preferred_workflows),
            "reason": plan.continuation.reason,
        },
        "selected_skills": list(plan.selected_skills),
        "selected_workflows": list(plan.selected_workflows),
        "selected_candidate_workflows": list(plan.selected_candidate_workflows),
        "fallback_to_tools": plan.fallback_to_tools,
        "summary": plan.summary,
        "candidate_capabilities": [
            {
                "kind": item.kind,
                "name": item.name,
                "title": item.title,
                "description": item.description,
                "category": item.category,
                "risk_level": item.risk_level,
                "provider_support": list(item.provider_support),
                "source": item.source,
                "score": item.score,
                "reasons": list(item.reasons),
            }
            for item in plan.candidate_capabilities
        ],
    }


def _serialize_execution_outcome(outcome: ExecutionOutcome) -> dict[str, object]:
    return {
        "strategy": outcome.strategy,
        "success": outcome.success,
        "waiting_for_user": outcome.waiting_for_user,
        "workflow_first_attempted": outcome.workflow_first_attempted,
        "workflow_first_succeeded": outcome.workflow_first_succeeded,
        "workflow_first_fallback": outcome.workflow_first_fallback,
        "workflow_first_name": outcome.workflow_first_name,
        "workflow_first_reason": outcome.workflow_first_reason,
        "used_skills": list(outcome.used_skills),
        "used_workflows": list(outcome.used_workflows),
        "used_tools": list(outcome.used_tools),
        "tool_sequence": list(outcome.tool_sequence),
        "touched_files": list(outcome.touched_files),
        "error_summary": outcome.error_summary,
        "reply_text": outcome.reply_text,
    }


def append_execution_outcome(
    *,
    session_scope: str | None,
    channel: str | None,
    provider: str,
    prompt: str,
    plan: DecisionPlan,
    outcome: ExecutionOutcome,
) -> None:
    EXECUTION_OUTCOMES_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "session_scope": session_scope or "",
        "channel": channel or "",
        "provider": provider,
        "prompt": prompt,
        "decision_plan": _serialize_decision_plan(plan),
        "outcome": _serialize_execution_outcome(outcome),
    }
    with EXECUTION_OUTCOMES_FILE.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")
