from __future__ import annotations

from uuid import uuid4

from weclaw.agentspec.registry import require_agent_spec
from weclaw.config import AGENT_CLUSTER_ENABLED, AGENT_CLUSTER_REVIEW_ENABLED
from weclaw.core.types import ConversationRef
from weclaw.decision.models import DecisionPlan

from .models import ClusterAgent, ClusterBlueprint
from .store import (
    finish_cluster_run as persist_cluster_finished,
    mark_cluster_agent_finished as persist_agent_finished,
    mark_cluster_agent_started as persist_agent_started,
    mark_cluster_agent_waiting as persist_agent_waiting,
    record_cluster_event,
    start_cluster_run as persist_cluster_started,
)


def cluster_enabled_for_plan(plan: DecisionPlan) -> bool:
    if not AGENT_CLUSTER_ENABLED:
        return False
    return plan.strategy in {"prefer_tools", "prefer_workflow", "prefer_skill"}


def build_cluster_blueprint(plan: DecisionPlan) -> ClusterBlueprint:
    cluster_id = f"cluster-{uuid4().hex[:10]}"
    planner_spec = require_agent_spec("planner")
    executor_spec = require_agent_spec("executor")
    agents: list[ClusterAgent] = [
        ClusterAgent(
            agent_id=planner_spec.name,
            role="planner",
            name=planner_spec.title,
            objective=f"拆解目标并分发执行：{plan.task_intent.goal}",
            spec_name=planner_spec.name,
        ),
        ClusterAgent(
            agent_id=executor_spec.name,
            role="executor",
            name=executor_spec.title,
            objective=plan.task_intent.expected_output or plan.task_intent.goal,
            spec_name=executor_spec.name,
        ),
    ]
    if AGENT_CLUSTER_REVIEW_ENABLED and plan.intent_type in {"file_task", "research_task", "mixed_task", "workflow_task"}:
        reviewer_spec = require_agent_spec("reviewer")
        agents.append(
            ClusterAgent(
                agent_id=reviewer_spec.name,
                role="reviewer",
                name=reviewer_spec.title,
                objective="检查结果完整性、风险和下一步建议",
                spec_name=reviewer_spec.name,
            )
        )
    steps = [
        f"理解任务与策略：{plan.summary or plan.task_intent.goal}",
        f"执行主任务：{plan.task_intent.expected_output or plan.task_intent.goal}",
    ]
    return ClusterBlueprint(
        cluster_id=cluster_id,
        mode="collaborative",
        objective=plan.task_intent.goal,
        agents=tuple(agents),
        planned_steps=tuple(steps),
    )


def start_cluster_run(conversation: ConversationRef, blueprint: ClusterBlueprint, plan: DecisionPlan) -> None:
    persist_cluster_started(conversation, blueprint)
    record_cluster_event(
        blueprint.cluster_id,
        kind="cluster_started",
        agent_id="planner",
        summary="Cluster started",
        detail=plan.summary or plan.task_intent.goal,
    )
    persist_agent_started(blueprint.cluster_id, "planner", "分析任务并生成执行分工")
    record_cluster_event(
        blueprint.cluster_id,
        kind="task_dispatched",
        agent_id="planner",
        summary="Tasks dispatched",
        detail=" -> ".join(blueprint.planned_steps),
    )
    persist_agent_finished(blueprint.cluster_id, "planner", "分工完成", message_kind="plan")


def mark_executor_started(conversation: ConversationRef, blueprint: ClusterBlueprint, summary: str) -> None:
    persist_agent_started(blueprint.cluster_id, "executor", summary)


def mark_executor_waiting(conversation: ConversationRef, blueprint: ClusterBlueprint, waiting_text: str) -> None:
    persist_agent_waiting(blueprint.cluster_id, "executor", waiting_text)


def mark_executor_finished(conversation: ConversationRef, blueprint: ClusterBlueprint, summary: str) -> None:
    persist_agent_finished(blueprint.cluster_id, "executor", summary)


def mark_reviewer_finished(conversation: ConversationRef, blueprint: ClusterBlueprint, summary: str) -> None:
    if not any(agent.agent_id == "reviewer" for agent in blueprint.agents):
        return
    persist_agent_started(blueprint.cluster_id, "reviewer", "复核执行结果")
    record_cluster_event(
        blueprint.cluster_id,
        kind="agent_note",
        agent_id="reviewer",
        summary="Reviewer summary",
        detail=summary,
    )
    persist_agent_finished(blueprint.cluster_id, "reviewer", "复核完成", message_kind="review")


def finish_cluster_run(conversation: ConversationRef, blueprint: ClusterBlueprint, success: bool, summary: str) -> None:
    persist_cluster_finished(blueprint.cluster_id, success, summary)
