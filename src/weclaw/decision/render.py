from __future__ import annotations

from weclaw.decision.models import DecisionPlan


def render_decision_plan(plan: DecisionPlan | None) -> str:
    if plan is None:
        return ""

    lines = [
        "当前任务识别：",
        f"- 意图类型：{plan.intent_type}",
        f"- 目标概括：{plan.task_intent.goal}",
        f"- 建议策略：{plan.strategy}",
    ]
    if plan.task_intent.target:
        lines.append(f"- 目标对象：{plan.task_intent.target}")
    if plan.task_intent.constraints:
        lines.append(f"- 关键约束：{'；'.join(plan.task_intent.constraints)}")
        if plan.task_intent.is_followup and plan.task_intent.context_summary and "持续约束：" in plan.task_intent.context_summary:
            lines.append("- 已自动继承本任务线中的持续约束")
    if plan.task_line.primary_goal:
        lines.append(f"- 当前任务主线：{plan.task_line.primary_goal}")
    if plan.task_line.active_subtask:
        lines.append(f"- 当前子任务：{plan.task_line.active_subtask}")
    if plan.task_line.stage:
        lines.append(f"- 当前阶段：{plan.task_line.stage}")
    if plan.continuation.continue_previous_strategy:
        lines.append(f"- 延续策略：{plan.continuation.preferred_strategy} ({plan.continuation.reason})")
    if plan.selected_workflows:
        lines.append(f"- 优先 workflow：{', '.join(plan.selected_workflows)}")
    if plan.selected_skills:
        lines.append(f"- 优先 skill：{', '.join(plan.selected_skills)}")
    if plan.summary:
        lines.append(f"- 路由摘要：{plan.summary}")
    lines.extend(
        [
            "执行要求：",
            "- 优先按建议策略处理。",
            "- 若首选 workflow/skill 不适合，再回退到工具执行。",
            "- 不要虚构工具结果。",
        ]
    )
    return "\n".join(lines).strip()


def render_decision_plan_debug(plan: DecisionPlan | None) -> str:
    if plan is None:
        return "当前没有可用的决策计划。"

    strategy_labels = {
        "answer_directly": "直接回答",
        "prefer_workflow": "优先 workflow",
        "prefer_skill": "优先 skill",
        "prefer_tools": "优先工具执行",
    }
    intent_labels = {
        "workflow_task": "工作流任务",
        "skill_task": "技能任务",
        "file_task": "文件/代码任务",
        "memory_task": "记忆任务",
        "schedule_task": "定时任务",
        "research_task": "调研任务",
        "question": "问答任务",
        "mixed_task": "混合任务",
    }
    style_labels = {
        "ask": "提问/咨询",
        "execute": "执行/修改",
        "design": "方案/设计",
        "review": "分析/评估",
    }

    lines = [
        "系统对这句话的理解：",
        f"- 任务类型：{intent_labels.get(plan.intent_type, plan.intent_type)}",
        f"- 表达类型：{style_labels.get(plan.task_intent.request_style, plan.task_intent.request_style)}",
        f"- 核心目标：{plan.task_intent.goal}",
        f"- 建议做法：{strategy_labels.get(plan.strategy, plan.strategy)}",
    ]
    if plan.task_intent.target:
        lines.append(f"- 主要对象：{plan.task_intent.target}")
    if plan.task_intent.expected_output:
        lines.append(f"- 预期结果：{plan.task_intent.expected_output}")
    if plan.task_intent.constraints:
        lines.append(f"- 关键约束：{'；'.join(plan.task_intent.constraints)}")
    if plan.strategy == "answer_directly":
        lines.append("- 当前更适合先解释/回答，再决定是否需要动手")
    elif plan.strategy == "prefer_tools":
        lines.append("- 当前更适合先动手获取依据或直接执行")
    elif plan.strategy == "prefer_skill":
        lines.append("- 当前更适合先参考已有方法/模板再回答或执行")
    if plan.task_intent.is_followup:
        lines.append("- 识别为续接上文：是")
    if plan.task_intent.inferred_from_context:
        lines.append("- 使用了会话上下文补全理解")
    if plan.continuation.continue_previous_strategy:
        lines.append(f"- 延续上一轮策略：{strategy_labels.get(plan.continuation.preferred_strategy, plan.continuation.preferred_strategy)}")
        if plan.continuation.reason:
            lines.append(f"- 延续原因：{plan.continuation.reason}")
    if plan.selected_workflows:
        lines.append(f"- 优先考虑的 workflow：{', '.join(plan.selected_workflows)}")
    if plan.selected_candidate_workflows:
        lines.append(f"- 相似候选 workflow：{', '.join(plan.selected_candidate_workflows)}")
    if plan.selected_skills:
        lines.append(f"- 优先考虑的 skill：{', '.join(plan.selected_skills)}")
    if plan.strategy == "prefer_workflow":
        lines.append("- 执行层会在高置信度且参数可推断时优先尝试 workflow")

    lines.append("")
    lines.append("调试细节：")
    lines = [
        *lines,
        f"- 回退工具：{'是' if plan.fallback_to_tools else '否'}",
        f"- 解释来源：{plan.task_intent.source}",
        f"- 解释置信度：{plan.task_intent.confidence:.2f}",
    ]
    if plan.continuation.continue_previous_strategy:
        lines.append(f"- continuation strategy key: {plan.continuation.preferred_strategy}")
        if plan.continuation.preferred_workflows:
            lines.append(f"- 延续 workflow：{', '.join(plan.continuation.preferred_workflows)}")
        if plan.continuation.preferred_skills:
            lines.append(f"- 延续 skill：{', '.join(plan.continuation.preferred_skills)}")
    if plan.task_intent.context_summary:
        lines.append(f"- 上下文摘要：{plan.task_intent.context_summary}")
    if plan.selected_workflows:
        lines.append(f"- selected workflow：{', '.join(plan.selected_workflows)}")
    if plan.selected_candidate_workflows:
        lines.append(f"- candidate workflow：{', '.join(plan.selected_candidate_workflows)}")
    if plan.selected_skills:
        lines.append(f"- selected skill：{', '.join(plan.selected_skills)}")
    if plan.summary:
        lines.append(f"- 路由摘要：{plan.summary}")
    if plan.candidate_capabilities:
        lines.append("")
        lines.append("候选能力打分：")
        for item in plan.candidate_capabilities:
            reasons = f" | reasons={','.join(item.reasons)}" if item.reasons else ""
            lines.append(f"- [{item.kind}] {item.name} | score={item.score:.1f} | {item.category}/{item.risk_level}{reasons}")
    return "\n".join(lines).strip()
