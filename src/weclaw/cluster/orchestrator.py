from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from weclaw.agentspec.models import AgentTask, AgentTaskResult
from weclaw.agentspec.registry import require_agent_spec
from weclaw.agentspec.runtime import AgentTaskRunner, agent_task_context_from_conversation, agent_task_from_cluster_task, run_agent_task
from weclaw.core.delivery import MessageSender
from weclaw.core.types import ConversationRef

from .models import ClusterBlueprint, ClusterTask
from .planner import ClusterPlanError, cluster_tasks_from_planner_plan, parse_planner_task_plan
from .store import (
    build_cluster_tasks_from_blueprint,
    mark_cluster_task_finished,
    mark_cluster_task_reviewed,
    mark_cluster_task_started,
    record_cluster_event,
    replace_cluster_tasks,
)


@dataclass(frozen=True, slots=True)
class ClusterOrchestrationResult:
    cluster_id: str
    success: bool
    task_results: tuple[AgentTaskResult, ...] = field(default_factory=tuple)
    error: str = ""


def render_cluster_orchestration_reply(result: ClusterOrchestrationResult) -> str:
    if not result.task_results:
        return result.error or "Cluster orchestration finished without task output."
    lines: list[str] = []
    for task_result in result.task_results:
        if task_result.success:
            lines.append(f"[{task_result.agent_name}] {task_result.text}".strip())
        else:
            lines.append(f"[{task_result.agent_name}] 执行失败：{task_result.error}")
    if result.error and result.success is False:
        lines.append(f"Cluster stopped: {result.error}")
    return "\n\n".join(line for line in lines if line).strip()


def render_cluster_user_reply(result: ClusterOrchestrationResult) -> str:
    if not result.task_results:
        return result.error or "Cluster orchestration finished without task output."
    if not result.success:
        failed = next((item for item in reversed(result.task_results) if not item.success), None)
        return (failed.error if failed else result.error) or "Cluster stopped before producing a final answer."

    final_result = _select_user_facing_result(result.task_results)
    text = _strip_role_prefix(final_result.text).strip()
    return _render_structured_user_text(text) or text


def _select_user_facing_result(results: tuple[AgentTaskResult, ...]) -> AgentTaskResult:
    non_review_results = [
        item for item in results
        if item.success and item.agent_name.lower() not in {"planner", "reviewer"}
    ]
    if non_review_results:
        return non_review_results[-1]
    successful_results = [item for item in results if item.success]
    return successful_results[-1] if successful_results else results[-1]


def _strip_role_prefix(text: str) -> str:
    stripped = text.strip()
    for role in ("planner", "executor", "reviewer"):
        prefix = f"[{role}]"
        if stripped.lower().startswith(prefix):
            return stripped[len(prefix):].strip()
    return stripped


def _render_structured_user_text(text: str) -> str:
    payload = _load_json_object(text)
    if not isinstance(payload, dict):
        return ""
    weather = _render_weather_payload(payload)
    if weather:
        return weather
    return _render_generic_json_payload(payload)


def _load_json_object(text: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None


def _render_weather_payload(payload: dict[str, Any]) -> str:
    city = payload.get("city")
    weather_date = payload.get("weather_date") or payload.get("date")
    forecast = payload.get("forecast")
    if not city or not isinstance(forecast, dict):
        return ""

    title = f"{city}{weather_date}天气：" if weather_date else f"{city}天气："
    lines = [title]
    for key, value in forecast.items():
        if isinstance(value, (dict, list)):
            continue
        lines.append(f"- {key}：{value}")
    warning = payload.get("risk_warning") or payload.get("warning") or payload.get("tips")
    if warning:
        lines.append("")
        lines.append(str(warning))
    source = payload.get("data_source")
    if source:
        lines.append("")
        lines.append(f"数据来源：{source}")
    return "\n".join(lines).strip()


def _render_generic_json_payload(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, value in payload.items():
        if isinstance(value, dict):
            lines.append(f"{key}：")
            for child_key, child_value in value.items():
                if isinstance(child_value, (dict, list)):
                    continue
                lines.append(f"- {child_key}：{child_value}")
        elif isinstance(value, list):
            if not value:
                continue
            lines.append(f"{key}：")
            for item in value[:8]:
                lines.append(f"- {item}")
        else:
            lines.append(f"{key}：{value}")
    return "\n".join(lines).strip()


def _agent_spec_name_for_task(blueprint: ClusterBlueprint, task: ClusterTask) -> str:
    for agent in blueprint.agents:
        if agent.agent_id == task.assigned_agent:
            return agent.spec_name or agent.agent_id
    return task.assigned_agent


def _shared_context_from_results(results: list[AgentTaskResult]) -> str:
    if not results:
        return ""
    parts: list[str] = []
    for result in results:
        status = "success" if result.success else "error"
        content = result.text if result.success else result.error
        parts.append(f"[{result.agent_name} / {result.task_id} / {status}]\n{content}")
    return "\n\n".join(parts)


def _planner_agent_id(blueprint: ClusterBlueprint) -> str:
    for agent in blueprint.agents:
        if agent.role == "planner":
            return agent.agent_id
    return "planner"


def _reviewer_agent_id(blueprint: ClusterBlueprint) -> str:
    for agent in blueprint.agents:
        if agent.role == "reviewer":
            return agent.agent_id
    return "reviewer"


def _has_reviewer(blueprint: ClusterBlueprint) -> bool:
    return any(agent.role == "reviewer" for agent in blueprint.agents)


def _has_explicit_reviewer_task(cluster_tasks: tuple[ClusterTask, ...], task_id: str, reviewer_id: str) -> bool:
    return any(
        task.assigned_agent == reviewer_id and task_id in task.depends_on
        for task in cluster_tasks
    )


def _retry_review_task_id(task_id: str, attempt_count: int) -> str:
    base_id = task_id.split(":retry:", 1)[0]
    return f"{base_id}:retry:{attempt_count}"


def _executor_task_needs_retry(result: AgentTaskResult) -> tuple[bool, str, str]:
    text = (result.text or "").strip()
    if not text:
        return True, "changes_requested", "Reviewer requested changes: executor output is empty."
    lowered = text.lower()
    if any(marker in lowered for marker in ("need more info", "insufficient", "unknown", "无法", "缺少", "未完成")):
        return True, "changes_requested", "Reviewer requested changes: executor result looks incomplete."
    return False, "approved", "Reviewer approved executor result."


def _review_result_to_outcome(text: str) -> tuple[bool, str, str]:
    normalized = (text or "").strip().lower()
    if any(marker in normalized for marker in ("changes requested", "request changes", "needs changes", "返工", "修改", "补充")):
        return True, "changes_requested", text.strip() or "Reviewer requested changes."
    if any(marker in normalized for marker in ("reject", "rejected", "驳回", "拒绝")):
        return True, "rejected", text.strip() or "Reviewer rejected the executor result."
    return False, "approved", text.strip() or "Reviewer approved executor result."


def _available_agents_text(blueprint: ClusterBlueprint) -> str:
    lines: list[str] = []
    for agent in blueprint.agents:
        lines.append(f"- {agent.agent_id} ({agent.role}, spec={agent.spec_name or agent.agent_id}): {agent.objective}")
    return "\n".join(lines)


def build_dynamic_planner_task(blueprint: ClusterBlueprint, user_prompt: str) -> AgentTask:
    json_contract = """
{
  "objective": "high-level objective",
  "tasks": [
    {
      "id": "short_stable_id",
      "title": "clear task title",
      "agent": "executor",
      "depends_on": [],
      "input": "task input for the assigned agent",
      "expected_output": "what this task must produce"
    }
  ]
}
    """.strip()
    return AgentTask(
        task_id=f"{blueprint.cluster_id}:planner_generate_dag",
        title="生成多 Agent 协作任务 DAG",
        objective=(
            "根据用户目标生成可执行的 JSON 任务 DAG。"
            "只返回 JSON，不要返回 Markdown、解释、寒暄或代码块。"
        ),
        input_payload=(
            f"用户目标：{user_prompt}\n\n"
            f"集群目标：{blueprint.objective}\n\n"
            f"可用 Agent：\n{_available_agents_text(blueprint)}\n\n"
            f"JSON 合同：\n{json_contract}\n\n"
            "要求：\n"
            "1. tasks 必须是非空数组。\n"
            "2. id 使用短英文、数字或下划线，不要带 cluster 前缀。\n"
            "3. agent 必须使用可用 Agent 的 agent_id 或 spec 名称。\n"
            "4. depends_on 只能引用同一 JSON 里已经定义或将定义的 task id。\n"
            "5. 如果任务需要复核，请加入 reviewer 任务并依赖执行任务。\n"
            "6. 只返回 JSON 对象本身。"
        ),
        expected_output="符合 Planner JSON Contract 的纯 JSON 对象。",
    )


async def run_cluster_tasks_serial(
    conversation: ConversationRef,
    blueprint: ClusterBlueprint,
    sender: MessageSender,
    *,
    tasks: tuple[ClusterTask, ...] | None = None,
    runner: AgentTaskRunner | None = None,
) -> ClusterOrchestrationResult:
    results: list[AgentTaskResult] = []
    completed_by_id: dict[str, AgentTaskResult] = {}
    cluster_tasks = tasks or build_cluster_tasks_from_blueprint(blueprint)
    if tasks is not None:
        replace_cluster_tasks(blueprint.cluster_id, cluster_tasks)

    pending = {task.task_id: task for task in cluster_tasks}
    task_index = {task.task_id: task for task in cluster_tasks}
    reviewer_id = _reviewer_agent_id(blueprint)
    reviewer_enabled = _has_reviewer(blueprint)
    max_task_runs = max(8, sum(max(1, task.max_attempts) * 2 for task in cluster_tasks) + 4)
    task_runs = 0

    async def run_one(cluster_task: ClusterTask) -> AgentTaskResult:
        spec_name = _agent_spec_name_for_task(blueprint, cluster_task)
        spec = require_agent_spec(spec_name)
        mark_cluster_task_started(blueprint.cluster_id, cluster_task.task_id, cluster_task.assigned_agent, cluster_task.title)
        dependency_results = [
            completed_by_id[task_id]
            for task_id in cluster_task.depends_on
            if task_id in completed_by_id
        ]
        context = agent_task_context_from_conversation(
            conversation,
            cluster_id=blueprint.cluster_id,
            shared_context=_shared_context_from_results(dependency_results or results),
        )
        result = await run_agent_task(
            spec,
            agent_task_from_cluster_task(cluster_task),
            context,
            sender,
            runner=runner,
        )
        output = result.text if result.success else result.error
        mark_cluster_task_finished(
            blueprint.cluster_id,
            cluster_task.task_id,
            cluster_task.assigned_agent,
            output,
            success=result.success,
        )
        return result

    async def review_executor_result(cluster_task: ClusterTask, result: AgentTaskResult) -> tuple[bool, str]:
        if not reviewer_enabled:
            return True, ""
        review_task = ClusterTask(
            task_id=f"{cluster_task.task_id}:review:{cluster_task.attempt_count + 1}",
            cluster_id=cluster_task.cluster_id,
            title=f"复核任务结果：{cluster_task.title}",
            assigned_agent=reviewer_id,
            input_payload=(
                f"请复核 executor 任务结果。\n\n"
                f"原任务：{cluster_task.title}\n"
                f"任务输入：{cluster_task.input_payload}\n\n"
                f"执行结果：\n{result.text}\n\n"
                "要求：判断结果是否可直接交付；若不完整，明确要求返工。"
            ),
            output_payload="输出简洁的审查结论。",
        )
        review_result = await run_one(review_task)
        results.append(review_result)
        needs_retry, outcome, fallback_summary = _executor_task_needs_retry(result)
        summary = review_result.text.strip() or fallback_summary
        attempt_count = cluster_task.attempt_count + 1
        next_state = "ready" if needs_retry else "done"
        final_outcome = "changes_requested" if needs_retry else "approved"
        if attempt_count >= cluster_task.max_attempts and needs_retry:
            final_outcome = "rejected"
            next_state = "error"
            summary = review_result.text.strip() or "Reviewer rejected executor result after max attempts."
        mark_cluster_task_reviewed(
            blueprint.cluster_id,
            cluster_task.task_id,
            reviewer_id,
            outcome=final_outcome,
            summary=summary,
            next_state=next_state,
            attempt_count=attempt_count,
        )
        return (final_outcome == "approved"), summary

    while pending:
        ready = [
            task
            for task in pending.values()
            if all(dep in completed_by_id for dep in task.depends_on)
        ]
        if task_runs + len(ready) > max_task_runs:
            error = f"Cluster orchestration stopped after {task_runs} task runs to avoid a review/retry loop."
            return ClusterOrchestrationResult(
                cluster_id=blueprint.cluster_id,
                success=False,
                task_results=tuple(results),
                error=error,
            )
        if not ready:
            blocked = ", ".join(sorted(pending))
            return ClusterOrchestrationResult(
                cluster_id=blueprint.cluster_id,
                success=False,
                task_results=tuple(results),
                error=f"Cluster DAG has unresolved dependencies: {blocked}",
            )

        layer_results = await asyncio.gather(*(run_one(task) for task in ready))
        task_runs += len(ready)
        for cluster_task, result in zip(ready, layer_results):
            results.append(result)
            if not result.success:
                pending.pop(cluster_task.task_id, None)
                completed_by_id[cluster_task.task_id] = result
                return ClusterOrchestrationResult(
                    cluster_id=blueprint.cluster_id,
                    success=False,
                    task_results=tuple(results),
                    error=result.error,
                )
            if any(agent.agent_id == cluster_task.assigned_agent and agent.role == "reviewer" for agent in blueprint.agents) and cluster_task.depends_on:
                target_task_id = cluster_task.depends_on[-1]
                current = task_index.get(target_task_id)
                needs_retry, outcome, review_summary = _review_result_to_outcome(result.text)
                if current is not None:
                    next_attempt = current.attempt_count + 1
                    final_outcome = outcome
                    if outcome == "changes_requested" and next_attempt >= current.max_attempts:
                        final_outcome = "rejected"
                    next_state = "ready" if final_outcome == "changes_requested" else ("error" if final_outcome == "rejected" else "done")
                    mark_cluster_task_reviewed(
                        blueprint.cluster_id,
                        target_task_id,
                        cluster_task.assigned_agent,
                        outcome=final_outcome,
                        summary=review_summary,
                        next_state=next_state,
                        attempt_count=next_attempt,
                    )
                    if final_outcome == "changes_requested":
                        retried_task = ClusterTask(
                            task_id=current.task_id,
                            cluster_id=current.cluster_id,
                            title=current.title,
                            assigned_agent=current.assigned_agent,
                            state="queued",
                            depends_on=current.depends_on,
                            input_payload=current.input_payload,
                            output_payload=current.output_payload,
                            attempt_count=next_attempt,
                            max_attempts=current.max_attempts,
                            review_outcome="changes_requested",
                            review_summary=review_summary,
                        )
                        retry_review_task = ClusterTask(
                            task_id=_retry_review_task_id(cluster_task.task_id, next_attempt),
                            cluster_id=cluster_task.cluster_id,
                            title=cluster_task.title,
                            assigned_agent=cluster_task.assigned_agent,
                            state="queued",
                            depends_on=(target_task_id,),
                            input_payload=cluster_task.input_payload,
                            output_payload=cluster_task.output_payload,
                            attempt_count=next_attempt,
                            max_attempts=cluster_task.max_attempts,
                        )
                        completed_by_id.pop(target_task_id, None)
                        task_index[target_task_id] = retried_task
                        task_index[retry_review_task.task_id] = retry_review_task
                        pending[target_task_id] = retried_task
                        pending[retry_review_task.task_id] = retry_review_task
                    elif final_outcome == "rejected":
                        failed_result = AgentTaskResult(
                            agent_name=cluster_task.assigned_agent,
                            task_id=target_task_id,
                            text="",
                            success=False,
                            error=review_summary,
                        )
                        pending.pop(cluster_task.task_id, None)
                        completed_by_id[cluster_task.task_id] = result
                        results.append(failed_result)
                        return ClusterOrchestrationResult(
                            cluster_id=blueprint.cluster_id,
                            success=False,
                            task_results=tuple(results),
                            error=review_summary,
                        )
            if (
                any(agent.agent_id == cluster_task.assigned_agent and agent.role == "executor" for agent in blueprint.agents)
                and not _has_explicit_reviewer_task(cluster_tasks, cluster_task.task_id, reviewer_id)
            ):
                approved, review_summary = await review_executor_result(cluster_task, result)
                if not approved:
                    current = task_index[cluster_task.task_id]
                    next_attempt = current.attempt_count + 1
                    max_attempts = current.max_attempts
                    if next_attempt >= max_attempts:
                        failed_result = AgentTaskResult(
                            agent_name=reviewer_id,
                            task_id=cluster_task.task_id,
                            text="",
                            success=False,
                            error=review_summary,
                        )
                        pending.pop(cluster_task.task_id, None)
                        results.append(failed_result)
                        completed_by_id[cluster_task.task_id] = failed_result
                        return ClusterOrchestrationResult(
                            cluster_id=blueprint.cluster_id,
                            success=False,
                            task_results=tuple(results),
                            error=review_summary,
                        )
                    retried_task = ClusterTask(
                        task_id=current.task_id,
                        cluster_id=current.cluster_id,
                        title=current.title,
                        assigned_agent=current.assigned_agent,
                        state="queued",
                        depends_on=current.depends_on,
                        input_payload=current.input_payload,
                        output_payload=current.output_payload,
                        attempt_count=next_attempt,
                        max_attempts=current.max_attempts,
                        review_outcome="changes_requested",
                        review_summary=review_summary,
                    )
                    task_index[cluster_task.task_id] = retried_task
                    pending[cluster_task.task_id] = retried_task
                    continue
            pending.pop(cluster_task.task_id, None)
            completed_by_id[cluster_task.task_id] = result

    return ClusterOrchestrationResult(
        cluster_id=blueprint.cluster_id,
        success=True,
        task_results=tuple(results),
    )


async def run_cluster_with_dynamic_planner(
    conversation: ConversationRef,
    blueprint: ClusterBlueprint,
    sender: MessageSender,
    *,
    user_prompt: str,
    runner: AgentTaskRunner | None = None,
) -> ClusterOrchestrationResult:
    planner_id = _planner_agent_id(blueprint)
    planner_spec = require_agent_spec(_agent_spec_name_for_task(blueprint, ClusterTask(
        task_id=f"{blueprint.cluster_id}:planner_generate_dag",
        cluster_id=blueprint.cluster_id,
        title="生成多 Agent 协作任务 DAG",
        assigned_agent=planner_id,
    )))
    planner_task = build_dynamic_planner_task(blueprint, user_prompt)
    record_cluster_event(
        blueprint.cluster_id,
        kind="task_started",
        agent_id=planner_id,
        summary="Planner generating dynamic task DAG",
        detail=planner_task.task_id,
    )
    planner_result = await run_agent_task(
        planner_spec,
        planner_task,
        agent_task_context_from_conversation(conversation, cluster_id=blueprint.cluster_id),
        sender,
        runner=runner,
    )
    if not planner_result.success:
        record_cluster_event(
            blueprint.cluster_id,
            kind="task_failed",
            agent_id=planner_id,
            summary=planner_result.error,
            detail=planner_task.task_id,
        )
        return ClusterOrchestrationResult(
            cluster_id=blueprint.cluster_id,
            success=False,
            task_results=(planner_result,),
            error=planner_result.error,
        )
    record_cluster_event(
        blueprint.cluster_id,
        kind="task_finished",
        agent_id=planner_id,
        summary="Planner generated dynamic task DAG",
        detail=planner_result.text[:220],
    )
    try:
        planner_plan = parse_planner_task_plan(planner_result.text)
        cluster_tasks = cluster_tasks_from_planner_plan(planner_plan, blueprint)
    except ClusterPlanError as exc:
        error = str(exc)
        record_cluster_event(
            blueprint.cluster_id,
            kind="task_failed",
            agent_id=planner_id,
            summary=error,
            detail=planner_task.task_id,
        )
        return ClusterOrchestrationResult(
            cluster_id=blueprint.cluster_id,
            success=False,
            task_results=(planner_result,),
            error=error,
        )
    return await run_cluster_tasks_serial(
        conversation,
        blueprint,
        sender,
        tasks=cluster_tasks,
        runner=runner,
    )
