from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

AgentRole = Literal["planner", "executor", "reviewer", "researcher", "coder", "custom"]
AgentMemoryScope = Literal["session", "shared", "private"]
AgentExecutionMode = Literal["single", "collaborative", "both"]


@dataclass(frozen=True, slots=True)
class AgentSpec:
    name: str
    title: str
    role: AgentRole
    description: str
    system_prompt: str
    default_provider: str = "inherit"
    execution_mode: AgentExecutionMode = "both"
    memory_scope: AgentMemoryScope = "session"
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)
    allowed_workflows: tuple[str, ...] = field(default_factory=tuple)
    allowed_skills: tuple[str, ...] = field(default_factory=tuple)
    can_delegate: bool = False
    can_review: bool = False
    source: str = "builtin"

    @property
    def supports_single(self) -> bool:
        return self.execution_mode in {"single", "both"}

    @property
    def supports_collaboration(self) -> bool:
        return self.execution_mode in {"collaborative", "both"}


@dataclass(frozen=True, slots=True)
class AgentTask:
    task_id: str
    title: str
    objective: str
    input_payload: str = ""
    depends_on: tuple[str, ...] = ()
    expected_output: str = ""


@dataclass(frozen=True, slots=True)
class AgentTaskContext:
    cluster_id: str = ""
    session_scope: str = ""
    channel: str = ""
    target_id: str = ""
    conversation_key: str = ""
    shared_context: str = ""


@dataclass(frozen=True, slots=True)
class AgentTaskResult:
    agent_name: str
    task_id: str
    text: str
    provider: str = ""
    success: bool = True
    error: str = ""
