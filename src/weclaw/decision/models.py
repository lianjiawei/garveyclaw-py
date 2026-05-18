from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

CapabilityKind = Literal["tool", "workflow", "skill"]
DecisionStrategy = Literal["answer_directly", "prefer_workflow", "prefer_skill", "prefer_tools"]


@dataclass(frozen=True, slots=True)
class CapabilityContinuation:
    continue_previous_strategy: bool = False
    preferred_strategy: str = ""
    preferred_skills: tuple[str, ...] = ()
    preferred_workflows: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class TaskLineState:
    primary_goal: str = ""
    active_subtask: str = ""
    stage: str = ""
    carried_constraints: tuple[str, ...] = ()
    updated_at: str = ""


@dataclass(frozen=True, slots=True)
class TaskIntent:
    intent_type: str
    goal: str
    request_style: str = "execute"
    target: str = ""
    constraints: tuple[str, ...] = ()
    expected_output: str = ""
    is_followup: bool = False
    confidence: float = 0.0
    source: str = "heuristic"
    context_summary: str = ""
    inferred_from_context: bool = False
    ambiguity_note: str = ""


@dataclass(frozen=True, slots=True)
class CapabilityCandidate:
    kind: CapabilityKind
    name: str
    title: str
    description: str
    category: str
    risk_level: str
    provider_support: frozenset[str]
    source: str
    score: float = 0.0
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DecisionPlan:
    task_intent: TaskIntent
    intent_type: str
    strategy: DecisionStrategy
    task_line: TaskLineState = TaskLineState()
    continuation: CapabilityContinuation = CapabilityContinuation()
    selected_skills: tuple[str, ...] = ()
    selected_workflows: tuple[str, ...] = ()
    selected_candidate_workflows: tuple[str, ...] = ()
    candidate_capabilities: tuple[CapabilityCandidate, ...] = ()
    fallback_to_tools: bool = True
    summary: str = ""


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    strategy: DecisionStrategy
    success: bool
    waiting_for_user: bool
    workflow_first_attempted: bool = False
    workflow_first_succeeded: bool = False
    workflow_first_fallback: bool = False
    workflow_first_name: str = ""
    workflow_first_reason: str = ""
    used_skills: tuple[str, ...] = ()
    used_workflows: tuple[str, ...] = ()
    used_tools: tuple[str, ...] = ()
    tool_sequence: tuple[str, ...] = ()
    touched_files: tuple[str, ...] = ()
    error_summary: str = ""
    reply_text: str = ""
