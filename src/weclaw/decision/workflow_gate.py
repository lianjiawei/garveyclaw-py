from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from weclaw.capabilities.tools import ToolContext, execute_tool
from weclaw.capabilities.workflows import get_workflow
from weclaw.decision.models import DecisionPlan


@dataclass(frozen=True, slots=True)
class WorkflowFirstResult:
    attempted: bool = False
    succeeded: bool = False
    fallback: bool = False
    workflow_name: str = ""
    reason: str = ""
    output_text: str = ""


def should_attempt_workflow_first(plan: DecisionPlan) -> tuple[bool, str]:
    if plan.strategy != "prefer_workflow":
        return False, "当前策略不是 prefer_workflow"
    if not plan.selected_workflows:
        return False, "没有可前置尝试的 workflow"
    if plan.task_intent.confidence < 0.62:
        return False, "任务理解置信度不足"
    if plan.intent_type not in {"workflow_task", "file_task"}:
        return False, "当前任务类型不适合 workflow-first"
    return True, "满足 workflow-first 条件"


def _infer_workflow_arguments(plan: DecisionPlan, workflow_name: str) -> tuple[dict[str, Any] | None, str]:
    workflow = get_workflow(workflow_name)
    if workflow is None:
        return None, "workflow 未找到"
    required = list(workflow.parameters.get("required") or [])
    if not required:
        return {}, "workflow 无必填参数"

    task_intent = plan.task_intent
    target = task_intent.target.strip()
    args: dict[str, Any] = {}

    for field in required:
        if field == "request_text":
            args[field] = task_intent.goal
            continue
        if field == "prompt":
            args[field] = task_intent.goal
            continue
        if field == "description":
            args[field] = task_intent.expected_output or task_intent.goal
            continue
        if field == "name":
            if target and target not in {"skill", "workflow"} and " " not in target:
                args[field] = target
                continue
            return None, "缺少可安全推断的 name 参数"
        return None, f"暂不支持自动推断参数 {field}"

    return args, "已推断 workflow 所需参数"


async def attempt_workflow_first(plan: DecisionPlan, ctx: ToolContext) -> WorkflowFirstResult:
    allowed, reason = should_attempt_workflow_first(plan)
    if not allowed:
        return WorkflowFirstResult(reason=reason)
    workflow_name = plan.selected_workflows[0]
    args, arg_reason = _infer_workflow_arguments(plan, workflow_name)
    if args is None:
        return WorkflowFirstResult(attempted=False, fallback=True, workflow_name=workflow_name, reason=arg_reason)
    result = await execute_tool(workflow_name, args, ctx)
    text = result.to_text()
    if result.is_error:
        return WorkflowFirstResult(attempted=True, succeeded=False, fallback=True, workflow_name=workflow_name, reason=text or "workflow-first 执行失败", output_text=text)
    return WorkflowFirstResult(attempted=True, succeeded=True, fallback=False, workflow_name=workflow_name, reason=arg_reason, output_text=text)
