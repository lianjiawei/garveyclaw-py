from __future__ import annotations

from dataclasses import replace
import re

from weclaw.capabilities.tools import ToolContext, list_tool_specs
from weclaw.capabilities.workflows import load_workflow_report
from weclaw.decision.candidates import load_workflow_candidate_capabilities
from weclaw.decision.interpreter import interpret_task_intent
from weclaw.decision.models import CapabilityCandidate, CapabilityContinuation, DecisionPlan, TaskIntent
from weclaw.decision.store import load_session_capability_preferences, load_session_task_line
from weclaw.skills.store import list_skills

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_\-]{2,}|[\u4e00-\u9fa5]{2,10}")


def _extract_tokens(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text or "")}


def classify_intent(prompt: str) -> str:
    text = prompt.lower()
    if any(token in text for token in ("workflow", "工作流", "流程", "自动化流程")):
        return "workflow_task"
    if any(token in text for token in ("skill", "技能", "模板")):
        return "skill_task"
    if any(token in text for token in ("记住", "长期记忆", "memory", "偏好", "忘记")):
        return "memory_task"
    if any(token in text for token in ("提醒", "定时", "稍后", "每天", "任务", "cron")):
        return "schedule_task"
    if any(token in text for token in ("搜索", "查资料", "调研", "对比", "网页", "新闻")):
        return "research_task"
    if any(token in text for token in ("文件", "代码", "修改", "新建", "删除", "路径", "workspace", "bash", "powershell")):
        return "file_task"
    if any(token in text for token in ("什么", "为什么", "如何", "吗", "？", "?")):
        return "question"
    return "mixed_task"


def _build_tool_candidates(provider: str, session_scope: str | None, channel: str | None) -> list[CapabilityCandidate]:
    ctx = ToolContext(sender=None, target_id="decision", channel=channel, session_scope=session_scope)
    candidates: list[CapabilityCandidate] = []
    for spec in list_tool_specs(provider=provider, context=ctx):
        candidates.append(
            CapabilityCandidate(
                kind="tool",
                name=spec.name,
                title=spec.name,
                description=spec.description,
                category=spec.category,
                risk_level=spec.risk_level,
                provider_support=spec.providers,
                source="tool_registry",
            )
        )
    return candidates


def _build_workflow_candidates() -> list[CapabilityCandidate]:
    report = load_workflow_report()
    return [
        CapabilityCandidate(
            kind="workflow",
            name=workflow.name,
            title=workflow.name,
            description=workflow.description,
            category=workflow.category,
            risk_level=workflow.risk_level,
            provider_support=workflow.providers,
            source=str(workflow.source_path.name if workflow.source_path else "workflow_registry"),
        )
        for workflow in report.specs
    ]


def _build_workflow_candidate_candidates(provider: str) -> list[CapabilityCandidate]:
    return load_workflow_candidate_capabilities(provider)


def _build_skill_candidates() -> list[CapabilityCandidate]:
    candidates: list[CapabilityCandidate] = []
    for skill in list_skills():
        candidates.append(
            CapabilityCandidate(
                kind="skill",
                name=skill.name,
                title=skill.title,
                description=skill.description,
                category="skills",
                risk_level="normal",
                provider_support=frozenset({"claude", "openai"}),
                source=skill.file_name,
            )
        )
    return candidates


def _score_candidate(candidate: CapabilityCandidate, prompt: str, task_intent: TaskIntent) -> CapabilityCandidate:
    text = prompt.lower()
    semantic_text = " ".join(
        part for part in [prompt, task_intent.goal, task_intent.target, *task_intent.constraints, task_intent.expected_output] if part
    )
    tokens = _extract_tokens(semantic_text)
    intent_type = task_intent.intent_type
    request_style = task_intent.request_style
    score = candidate.score
    reasons = list(candidate.reasons)

    if f"#{candidate.name.lower()}" in text:
        score += 8
        reasons.append("explicit_name")
    if candidate.name.lower() in text:
        score += 5
        reasons.append("name_match")
    if candidate.title and candidate.title.lower() in text:
        score += 4
        reasons.append("title_match")
    metadata_tokens = _extract_tokens(" ".join((candidate.name, candidate.title, candidate.description)))
    overlap = len(tokens & metadata_tokens)
    if overlap > 0:
        score += min(overlap, 4)
        reasons.append(f"token_overlap:{overlap}")
    if candidate.kind == "workflow" and intent_type in {"workflow_task", "file_task"}:
        score += 3
        reasons.append("workflow_intent_match")
    if candidate.kind == "workflow" and task_intent.target and task_intent.target.lower() in candidate.name.lower():
        score += 3
        reasons.append("workflow_target_match")
    if candidate.kind == "workflow" and candidate.category == "workflows":
        score += 1
        reasons.append("structured_capability")
    if candidate.kind == "workflow" and candidate.category == "workflow_candidates":
        score += 1.5
        reasons.append("candidate_workflow_memory")
    if candidate.kind == "workflow" and request_style in {"ask", "design", "review"}:
        score -= 2.5
        reasons.append("workflow_deprioritized_for_non_execute")
    if candidate.kind == "skill" and intent_type in {"skill_task", "question", "research_task"}:
        score += 2
        reasons.append("skill_intent_match")
    if candidate.kind == "skill" and any(token in text for token in ("模板", "话术", "规范", "指南", "framework", "技能")):
        score += 2
        reasons.append("skill_prompt_match")
    if candidate.kind == "skill" and task_intent.target and task_intent.target.lower() in candidate.description.lower():
        score += 2
        reasons.append("skill_target_match")
    if candidate.kind == "skill" and request_style in {"design", "review"}:
        score += 1.5
        reasons.append("skill_design_review_bias")
    if candidate.kind == "tool" and intent_type in {"file_task", "schedule_task", "research_task"}:
        score += 2
        reasons.append("tool_intent_match")
    if candidate.kind == "tool" and request_style == "review":
        score += 1
        reasons.append("tool_review_bias")
    if candidate.kind == "tool" and task_intent.is_followup and intent_type in {"file_task", "mixed_task"}:
        score += 1
        reasons.append("followup_tool_bias")

    return replace(candidate, score=score, reasons=tuple(dict.fromkeys(reasons)))


def _goal_similarity(current_goal: str, previous_goal: str) -> float:
    current_tokens = _extract_tokens(current_goal)
    previous_tokens = _extract_tokens(previous_goal)
    if not current_tokens or not previous_tokens:
        return 0.0
    overlap = len(current_tokens & previous_tokens)
    union = len(current_tokens | previous_tokens)
    return overlap / union if union else 0.0


def _build_continuation(task_intent: TaskIntent, session_scope: str | None) -> CapabilityContinuation:
    preferences = load_session_capability_preferences(session_scope)
    last_strategy = str(preferences.get("last_strategy") or "").strip()
    if not task_intent.is_followup or not last_strategy or not bool(preferences.get("last_success")):
        return CapabilityContinuation()
    previous_goal = str(preferences.get("last_goal") or "").strip()
    similarity = _goal_similarity(task_intent.goal, previous_goal) if previous_goal else 0.0
    if not task_intent.inferred_from_context and not previous_goal:
        return CapabilityContinuation()
    if not task_intent.inferred_from_context and similarity < 0.08:
        return CapabilityContinuation()
    preferred_skills = tuple(str(item).strip() for item in preferences.get("preferred_skills") or [] if str(item).strip())
    preferred_workflows = tuple(str(item).strip() for item in preferences.get("preferred_workflows") or [] if str(item).strip())
    reason = "续接上文且上一轮成功"
    if similarity >= 0.2:
        reason += f"，目标相似度 {similarity:.2f}"
    elif task_intent.inferred_from_context:
        reason += "，当前目标由上下文补全"
    return CapabilityContinuation(
        continue_previous_strategy=True,
        preferred_strategy=last_strategy,
        preferred_skills=preferred_skills,
        preferred_workflows=preferred_workflows,
        reason=reason,
    )


def _apply_continuation_bias(candidate: CapabilityCandidate, continuation: CapabilityContinuation) -> CapabilityCandidate:
    if not continuation.continue_previous_strategy:
        return candidate
    score = candidate.score
    reasons = list(candidate.reasons)
    if candidate.kind == "workflow" and candidate.name in continuation.preferred_workflows:
        score += 4
        reasons.append("continuation_workflow")
    if candidate.kind == "skill" and candidate.name in continuation.preferred_skills:
        score += 3
        reasons.append("continuation_skill")
    if candidate.kind == "tool" and continuation.preferred_strategy == "prefer_tools":
        score += 1.5
        reasons.append("continuation_tools")
    return replace(candidate, score=score, reasons=tuple(dict.fromkeys(reasons)))


def _has_actionable_execute_signal(prompt: str, task_intent: TaskIntent) -> bool:
    text = prompt.lower()
    return any(
        token in text
        for token in (
            "帮我",
            "请你",
            "改一下",
            "修改",
            "创建",
            "生成",
            "实现",
            "修复",
            "更新",
            "整理成",
            "顺手改",
            "继续改",
        )
    ) or bool(task_intent.target)


def _is_explain_first_request(prompt: str, task_intent: TaskIntent) -> bool:
    text = prompt.lower()
    if task_intent.request_style in {"design", "review"}:
        return True
    if task_intent.request_style == "ask" and not _has_actionable_execute_signal(prompt, task_intent):
        return True
    return any(
        token in text
        for token in (
            "为什么",
            "原理",
            "区别",
            "优缺点",
            "值不值得",
            "如何理解",
            "你怎么看",
        )
    )


def _should_prefer_tools_for_execution(task_intent: TaskIntent) -> bool:
    if task_intent.request_style != "execute":
        return False
    return task_intent.intent_type in {"file_task", "schedule_task", "research_task", "mixed_task"}


async def build_decision_plan(prompt: str, provider: str, session_scope: str | None, channel: str | None) -> DecisionPlan:
    task_intent = await interpret_task_intent(prompt, provider, session_scope, channel)
    task_line = load_session_task_line(session_scope)
    intent_type = task_intent.intent_type or classify_intent(prompt)
    continuation = _build_continuation(task_intent, session_scope)
    raw_candidates = [
        *_build_workflow_candidates(),
        *_build_workflow_candidate_candidates(provider),
        *_build_skill_candidates(),
        *_build_tool_candidates(provider, session_scope, channel),
    ]
    scored = [
        _apply_continuation_bias(_score_candidate(candidate, prompt, task_intent), continuation)
        for candidate in raw_candidates
        if provider in candidate.provider_support
    ]
    ranked = sorted(scored, key=lambda item: item.score, reverse=True)

    top_workflows = tuple(
        item.name for item in ranked if item.kind == "workflow" and item.category == "workflows" and item.score >= 6
    )[:3]
    top_candidate_workflows = tuple(
        item.name for item in ranked if item.kind == "workflow" and item.category == "workflow_candidates" and item.score >= 6
    )[:3]
    top_skills = tuple(item.name for item in ranked if item.kind == "skill" and item.score >= 6)[:3]
    request_style = task_intent.request_style
    explain_first = _is_explain_first_request(prompt, task_intent)
    actionable_execute = _has_actionable_execute_signal(prompt, task_intent)
    is_ambiguous = bool(task_intent.ambiguity_note) and task_intent.confidence < 0.4

    if is_ambiguous:
        strategy = "answer_directly"
        summary = f"意图存在歧义（{task_intent.ambiguity_note}），优先直接回答并请求澄清。"
    elif explain_first and request_style == "ask":
        strategy = "answer_directly"
        summary = "当前更像提问/咨询，优先直接回答，必要时再补充工具依据。"
    elif request_style == "design" and top_skills:
        strategy = "prefer_skill"
        summary = f"当前更像方案/设计请求，优先参考 skill {', '.join(top_skills)}。"
    elif request_style == "review" and intent_type in {"file_task", "research_task", "mixed_task"}:
        strategy = "prefer_tools"
        summary = "当前更像分析/评估请求，优先通过读取和搜索获取依据。"
    elif request_style == "ask" and actionable_execute and _should_prefer_tools_for_execution(task_intent):
        strategy = "prefer_tools"
        summary = "虽然表达中带有询问成分，但核心仍是让系统动手处理，优先使用工具执行。"
    elif continuation.continue_previous_strategy and continuation.preferred_strategy == "prefer_workflow" and top_workflows:
        strategy = "prefer_workflow"
        summary = f"延续上一轮 workflow 策略，优先 {', '.join(top_workflows)}。"
    elif continuation.continue_previous_strategy and continuation.preferred_strategy == "prefer_skill" and top_skills:
        strategy = "prefer_skill"
        summary = f"延续上一轮 skill 策略，优先 {', '.join(top_skills)}。"
    elif continuation.continue_previous_strategy and continuation.preferred_strategy == "prefer_tools" and intent_type in {"file_task", "research_task", "mixed_task", "schedule_task"}:
        strategy = "prefer_tools"
        summary = "延续上一轮工具执行策略。"
    elif top_workflows and intent_type in {"workflow_task", "file_task"}:
        strategy = "prefer_workflow"
        summary = f"命中 workflow 候选 {', '.join(top_workflows)}。"
    elif top_candidate_workflows and intent_type in {"workflow_task", "file_task", "mixed_task"}:
        strategy = "prefer_tools"
        summary = f"发现相似候选 workflow {', '.join(top_candidate_workflows)}，先按工具路径处理并参考其结构。"
    elif top_skills and intent_type not in {"file_task", "schedule_task"}:
        strategy = "prefer_skill"
        summary = f"命中 skill 候选 {', '.join(top_skills)}。"
    elif _should_prefer_tools_for_execution(task_intent):
        strategy = "prefer_tools"
        summary = "当前任务更适合直接使用工具完成。"
    elif explain_first:
        strategy = "answer_directly"
        summary = "当前更适合先解释、分析或给建议，而不是立即执行。"
    else:
        strategy = "answer_directly"
        summary = "当前任务以直接回答为主，必要时补充工具。"

    return DecisionPlan(
        task_intent=task_intent,
        task_line=task_line,
        intent_type=intent_type,
        strategy=strategy,
        continuation=continuation,
        selected_skills=top_skills,
        selected_workflows=top_workflows,
        selected_candidate_workflows=top_candidate_workflows,
        candidate_capabilities=tuple(ranked[:8]),
        fallback_to_tools=True,
        summary=summary,
    )
