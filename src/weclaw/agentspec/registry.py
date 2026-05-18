from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from weclaw.agentspec.models import AgentSpec
from weclaw.agentspec.store import AgentSpecError, load_agent_specs


def builtin_agent_specs() -> tuple[AgentSpec, ...]:
    return (
        AgentSpec(
            name="planner",
            title="Planner",
            role="planner",
            description="拆解目标、生成执行分工，并维护协作计划。",
            system_prompt="你是协作集群中的规划 Agent。你的职责是理解用户目标，拆解任务，明确依赖关系，并把任务分发给合适的 Agent。",
            execution_mode="collaborative",
            memory_scope="shared",
            can_delegate=True,
            source="builtin",
        ),
        AgentSpec(
            name="executor",
            title="Executor",
            role="executor",
            description="执行主任务，调用必要工具，并产出可交付结果。",
            system_prompt="你是协作集群中的执行 Agent。你的职责是根据计划完成具体任务，必要时使用工具，并返回清晰的执行结果。",
            execution_mode="both",
            memory_scope="session",
            allowed_tools=("*",),
            source="builtin",
        ),
        AgentSpec(
            name="reviewer",
            title="Reviewer",
            role="reviewer",
            description="复核执行结果，识别风险、遗漏和下一步建议。",
            system_prompt="你是协作集群中的复核 Agent。你的职责是检查执行结果是否满足用户目标，指出风险和遗漏，并给出改进建议。",
            execution_mode="collaborative",
            memory_scope="shared",
            can_review=True,
            source="builtin",
        ),
    )


def build_agent_registry(directory: Path | None = None) -> dict[str, AgentSpec]:
    registry = {spec.name: spec for spec in builtin_agent_specs()}
    for spec in load_agent_specs(directory):
        registry[spec.name] = spec
    return registry


def list_agent_specs(directory: Path | None = None) -> list[AgentSpec]:
    return sorted(build_agent_registry(directory).values(), key=lambda item: item.name)


def get_agent_spec(name: str, directory: Path | None = None) -> AgentSpec | None:
    return build_agent_registry(directory).get(name)


def require_agent_spec(name: str, directory: Path | None = None) -> AgentSpec:
    spec = get_agent_spec(name, directory)
    if spec is None:
        raise AgentSpecError(f"Agent '{name}' is not defined.")
    return spec


def with_runtime_objective(spec: AgentSpec, objective: str) -> AgentSpec:
    return replace(spec, description=objective or spec.description)
