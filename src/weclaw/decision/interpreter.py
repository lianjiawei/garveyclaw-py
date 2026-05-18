from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin

import httpx

from weclaw.config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_BASE_URL,
    DECISION_INTERPRETER_MAX_PROMPT_CHARS,
    DECISION_INTERPRETER_MODE,
    DECISION_INTERPRETER_TIMEOUT_SECONDS,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
)
from weclaw.core.provider_model import get_effective_api_key, get_effective_base_url, get_effective_model
from weclaw.decision.models import TaskIntent
from weclaw.decision.store import load_session_task_line, load_session_user_constraints
from weclaw.memory.store import load_session_summary, load_working_state

_FOLLOWUP_RE = re.compile(r"(这个|这个也|顺手|继续|还是那个|同样|另外|再把|再顺便|also|too)", re.IGNORECASE)
_CONSTRAINT_SPLIT_RE = re.compile(r"(?:并且|而且|同时|注意|要求|不要|必须|尽量|记得|确保|with|without)")
_AMBIGUITY_RE = re.compile(r"(随便|都行|看着办|大概|可能|也许|差不多|随便弄|弄一下|搞一下|弄弄)", re.IGNORECASE)
_EXECUTE_SIGNALS = re.compile(r"(帮我|请你|改一下|修改|创建|生成|实现|修复|更新|整理成|顺手改|继续改|执行|跑一下)", re.IGNORECASE)
_QUESTION_SIGNALS = re.compile(r"(什么|为什么|如何|吗|？|\?|怎么做|思路|方案|建议|评估|分析|比较|区别)", re.IGNORECASE)


def _detect_request_style(text: str) -> str:
    lowered = text.lower().strip()
    if any(token in lowered for token in ("先别", "帮我", "请你", "顺手", "继续改", "改一下", "处理一下")):
        return "execute"
    if any(token in lowered for token in ("review", "代码审查", "帮我看看", "评估一下", "比较一下", "分析一下", "看看哪里")):
        return "review"
    if any(token in lowered for token in ("怎么做", "思路", "方案", "设计", "规划", "路线图", "建议")):
        return "design"
    if any(token in lowered for token in ("什么", "为什么", "如何", "吗", "？", "?")):
        return "ask"
    return "execute"


def _build_context_summary(session_scope: str | None) -> str:
    if not session_scope:
        return ""
    working_state = load_working_state(session_scope)
    session_summary = load_session_summary(session_scope)
    parts: list[str] = []
    active_goal = str(working_state.get("active_goal") or "").strip()
    if active_goal:
        parts.append(f"当前目标：{active_goal}")
    tasks = [str(item).strip() for item in working_state.get("active_tasks") or [] if str(item).strip()]
    if tasks:
        parts.append(f"近期任务：{'；'.join(tasks[:3])}")
    files = [str(item).strip() for item in working_state.get("touched_files") or [] if str(item).strip()]
    if files:
        parts.append(f"最近文件：{'；'.join(files[-3:])}")
    questions = [str(item).strip() for item in working_state.get("open_questions") or [] if str(item).strip()]
    if questions:
        parts.append(f"待解决问题：{'；'.join(questions[-2:])}")
    latest_user = str(session_summary.get("latest_user_message") or "").strip()
    if latest_user:
        parts.append(f"上一条用户消息：{latest_user[:120]}")
    topics = [str(item).strip() for item in session_summary.get("recent_topics") or [] if str(item).strip()]
    if topics:
        parts.append(f"近期话题：{'；'.join(topics[-3:])}")
    constraint_state = load_session_user_constraints(session_scope)
    active_constraints = [str(item).strip() for item in constraint_state.get("active_constraints") or [] if str(item).strip()]
    if active_constraints:
        parts.append(f"持续约束：{'；'.join(active_constraints[:3])}")
    task_line = load_session_task_line(session_scope)
    if task_line.primary_goal:
        parts.append(f"任务主线：{task_line.primary_goal}")
    if task_line.active_subtask:
        parts.append(f"当前子任务：{task_line.active_subtask}")
    if task_line.stage:
        parts.append(f"当前阶段：{task_line.stage}")
    return " | ".join(parts[:6])[:600]


def _prefer_context_target(text: str, context_summary: str) -> str:
    combined = f"{text} {context_summary}"
    return _heuristic_target(combined)


def _clamp_text(text: str) -> str:
    cleaned = text.strip()
    if len(cleaned) <= DECISION_INTERPRETER_MAX_PROMPT_CHARS:
        return cleaned
    return cleaned[:DECISION_INTERPRETER_MAX_PROMPT_CHARS]


def _heuristic_intent_type(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ("workflow", "工作流", "流程", "自动化流程")):
        return "workflow_task"
    if any(token in lowered for token in ("skill", "技能", "模板")):
        return "skill_task"
    if any(token in lowered for token in ("记住", "长期记忆", "memory", "偏好", "忘记")):
        return "memory_task"
    if any(token in lowered for token in ("提醒", "定时", "稍后", "每天", "任务", "cron")):
        return "schedule_task"
    if any(token in lowered for token in ("搜索", "查资料", "调研", "对比", "网页", "新闻")):
        return "research_task"
    if any(token in lowered for token in ("文件", "代码", "修改", "新建", "删除", "路径", "workspace", "bash", "powershell")):
        return "file_task"
    if any(token in lowered for token in ("什么", "为什么", "如何", "吗", "？", "?")):
        return "question"
    return "mixed_task"


def _heuristic_goal(text: str) -> str:
    normalized = " ".join(text.split())
    return normalized[:200]


def _heuristic_constraints(text: str) -> tuple[str, ...]:
    constraints: list[str] = []
    normalized = text.replace("\n", " ")
    for fragment in _CONSTRAINT_SPLIT_RE.split(normalized):
        item = fragment.strip(" ，,。.;；")
        if len(item) < 6:
            continue
        if item == normalized.strip():
            continue
        constraints.append(item[:120])
    seen: set[str] = set()
    ordered: list[str] = []
    for item in constraints:
        lowered = item.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        ordered.append(item)
    return tuple(ordered[:4])


def _load_active_constraints(session_scope: str | None) -> tuple[str, ...]:
    state = load_session_user_constraints(session_scope)
    return tuple(str(item).strip() for item in state.get("active_constraints") or [] if str(item).strip())


def _merge_persistent_constraints(current: tuple[str, ...], active: tuple[str, ...], is_followup: bool) -> tuple[str, ...]:
    if not active:
        return current
    if not is_followup and current:
        return current
    merged = list(current)
    seen = {item.lower() for item in merged}
    for item in active:
        lowered = item.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        merged.append(item)
    return tuple(merged[:8])


def _heuristic_target(text: str) -> str:
    named_match = re.search(r'(?:skill|技能|workflow|工作流)\s*[:：]?[\s"]*([A-Za-z][A-Za-z0-9_-]{2,})', text, re.IGNORECASE)
    if named_match:
        return named_match.group(1)
    match = re.search(r"([A-Za-z0-9_./\\-]+\.(?:py|md|json|txt|yaml|yml|sh|ps1|html|css|js|ts|tsx))", text)
    if match:
        return match.group(1)
    if "skill" in text.lower() or "技能" in text:
        return "skill"
    if "workflow" in text.lower() or "工作流" in text:
        return "workflow"
    return ""


def _heuristic_expected_output(intent_type: str) -> str:
    mapping = {
        "workflow_task": "一个可执行或可更新的 workflow 定义或执行结果",
        "skill_task": "一个 skill 定义、更新结果或技能说明",
        "file_task": "文件修改结果、代码变更或执行结果",
        "memory_task": "记忆写入、查询或整理结果",
        "schedule_task": "定时任务创建、取消或列表结果",
        "research_task": "资料搜索、汇总或调研结果",
        "question": "直接回答用户问题",
    }
    return mapping.get(intent_type, "完成用户请求并给出清晰结果")


def _compute_signal_confidence(
    text: str,
    intent_type: str,
    request_style: str,
    has_target: bool,
    has_constraints: bool,
    is_followup: bool,
    inferred_from_context: bool,
) -> tuple[float, str]:
    """多信号置信度评分：组合启发式信号计算置信度。"""
    signals: list[tuple[str, float]] = []
    lowered = text.lower()

    # 信号 1: 目标明确度
    if has_target:
        signals.append(("explicit_target", 0.15))
    else:
        signals.append(("no_target", -0.1))

    # 信号 2: 约束丰富度
    if has_constraints:
        signals.append(("has_constraints", 0.1))

    # 信号 3: 执行信号强度
    exec_signals = len(_EXECUTE_SIGNALS.findall(text))
    if exec_signals >= 2:
        signals.append(("strong_execute_signal", 0.15))
    elif exec_signals == 1:
        signals.append(("execute_signal", 0.08))

    # 信号 4: 提问信号强度
    question_signals = len(_QUESTION_SIGNALS.findall(text))
    if question_signals >= 2 and exec_signals == 0:
        signals.append(("strong_question_signal", 0.12))

    # 信号 5: 歧义检测
    ambiguity_matches = _AMBIGUITY_RE.findall(text)
    if ambiguity_matches:
        signals.append(("ambiguous_language", -0.2))

    # 信号 6: 文本长度信息量
    text_len = len(text.strip())
    if text_len >= 50:
        signals.append(("rich_text", 0.08))
    elif text_len <= 8:
        signals.append(("very_short_text", -0.15))

    # 信号 7: 上下文推断惩罚
    if inferred_from_context:
        signals.append(("inferred_from_context", -0.12))

    # 信号 8: 续接信号
    if is_followup:
        signals.append(("followup", 0.05))

    # 信号 9: 意图类型与请求风格一致性
    style_intent_consistent = (
        (request_style == "execute" and intent_type in {"file_task", "workflow_task", "skill_task", "memory_task", "schedule_task"})
        or (request_style == "ask" and intent_type == "question")
        or (request_style == "design" and intent_type in {"mixed_task", "research_task"})
        or (request_style == "review" and intent_type in {"file_task", "research_task", "mixed_task"})
    )
    if style_intent_consistent:
        signals.append(("style_intent_consistent", 0.1))
    else:
        signals.append(("style_intent_mismatch", -0.08))

    # 信号 10: 意图类型特异性
    specific_intents = {"workflow_task", "skill_task", "memory_task", "schedule_task", "research_task"}
    if intent_type in specific_intents:
        signals.append(("specific_intent", 0.08))
    elif intent_type == "mixed_task":
        signals.append(("generic_intent", -0.05))

    confidence = 0.45 + sum(score for _, score in signals)
    confidence = max(0.1, min(confidence, 0.95))

    # 生成置信度说明
    positive = [name for name, score in signals if score > 0]
    negative = [name for name, score in signals if score < 0]
    confidence_note = f"+{len(positive)}" if positive else ""
    if negative:
        confidence_note += f" -{len(negative)}" if confidence_note else f"-{len(negative)}"

    return round(confidence, 3), confidence_note


def _detect_ambiguity(text: str, intent_type: str, has_target: bool, confidence: float) -> str:
    """歧义检测：当意图不明确时返回歧义说明。"""
    notes: list[str] = []
    lowered = text.lower()

    # 检测 1: 歧义词汇
    if _AMBIGUITY_RE.search(text):
        notes.append("表达含糊，使用了模糊词汇")

    # 检测 2: 无明确目标
    if not has_target and intent_type == "mixed_task":
        notes.append("未识别到明确操作对象")

    # 检测 3: 低置信度
    if confidence < 0.35:
        notes.append("意图判断置信度较低")

    # 检测 4: 文本过短
    if len(text.strip()) <= 6:
        notes.append("输入过短，难以准确判断意图")

    # 检测 5: 多重意图冲突
    has_exec = bool(_EXECUTE_SIGNALS.search(text))
    has_question = bool(_QUESTION_SIGNALS.search(text))
    if has_exec and has_question:
        notes.append("同时包含执行和提问信号，意图可能存在冲突")

    return "；".join(notes) if notes else ""


def heuristic_task_intent(prompt: str, session_scope: str | None = None) -> TaskIntent:
    text = _clamp_text(prompt)
    context_summary = _build_context_summary(session_scope)
    active_constraints = _load_active_constraints(session_scope)
    task_line = load_session_task_line(session_scope)
    intent_type = _heuristic_intent_type(text)
    request_style = _detect_request_style(text)
    inferred_from_context = False
    goal = _heuristic_goal(text)
    target = _heuristic_target(text)
    constraints = _heuristic_constraints(text)
    is_followup = bool(_FOLLOWUP_RE.search(text))
    constraints = _merge_persistent_constraints(constraints, active_constraints, is_followup)
    if context_summary and (is_followup or len(text) <= 24 or not target):
        inferred_from_context = True
        if not target:
            target = _prefer_context_target(text, context_summary)
        if is_followup and "当前目标：" in context_summary:
            active_goal = context_summary.split("当前目标：", 1)[1].split("|", 1)[0].strip()
            if active_goal:
                goal = f"延续当前目标：{active_goal}；本轮补充：{text[:120]}"
        elif is_followup and task_line.primary_goal:
            goal = f"围绕任务主线继续推进：{task_line.primary_goal}；本轮补充：{text[:120]}"
        if not constraints and "最近文件：" in context_summary:
            file_hint = context_summary.split("最近文件：", 1)[1].split("|", 1)[0].strip()
            if file_hint:
                constraints = (f"优先参考最近相关文件：{file_hint}",)
    if task_line.carried_constraints:
        merged = list(constraints)
        seen = {item.lower() for item in merged}
        for item in task_line.carried_constraints:
            lowered = item.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            merged.append(item)
        constraints = tuple(merged[:8])
    confidence, confidence_note = _compute_signal_confidence(
        text=text,
        intent_type=intent_type,
        request_style=request_style,
        has_target=bool(target),
        has_constraints=bool(constraints),
        is_followup=is_followup,
        inferred_from_context=inferred_from_context,
    )
    ambiguity_note = _detect_ambiguity(text, intent_type, bool(target), confidence)
    return TaskIntent(
        intent_type=intent_type,
        goal=goal,
        request_style=request_style,
        target=target,
        constraints=constraints,
        expected_output=_heuristic_expected_output(intent_type),
        is_followup=is_followup,
        confidence=confidence,
        source="heuristic",
        context_summary=context_summary,
        inferred_from_context=inferred_from_context,
        ambiguity_note=ambiguity_note,
    )


def _should_use_model(prompt: str, provider: str) -> bool:
    mode = DECISION_INTERPRETER_MODE
    if mode == "heuristic":
        return False
    if provider == "openai" and not get_effective_api_key("openai"):
        return False
    if provider == "claude" and not get_effective_api_key("claude"):
        return False
    text = prompt.strip()
    if mode == "model":
        return True
    if text.startswith("/"):
        return False
    lowered = text.lower()
    execution_markers = (
        "分析",
        "检查",
        "对比",
        "修复",
        "实现",
        "优化",
        "重构",
        "生成",
        "创建",
        "读取",
        "搜索",
        "调研",
        "review",
        "analyze",
        "compare",
        "implement",
        "fix",
        "refactor",
        "workspace",
    )
    if len(text) < 40 and not any(marker in lowered for marker in execution_markers):
        return False
    return len(text) >= 18


def _build_interpreter_prompt(prompt: str, context_summary: str = "") -> str:
    context_block = f"\n会话上下文：\n{context_summary}\n" if context_summary else ""
    return (
        "你是一个任务意图解释器。请把用户请求解析成 JSON，只返回 JSON，不要解释。\n"
        "请先判断用户是在提问、要你执行修改、要方案建议，还是在请求评估/分析。\n"
        "字段要求：\n"
        '{"intent_type":"question|file_task|workflow_task|skill_task|memory_task|schedule_task|research_task|mixed_task",'
        '"goal":"一句话概括用户真正目标",'
        '"request_style":"ask|execute|design|review",'
        '"target":"主要对象，如文件、skill、workflow、任务或空字符串",'
        '"constraints":["约束1","约束2"],'
        '"expected_output":"用户期望的结果形式",'
        '"is_followup":true,'
        '"confidence":0.0}\n'
        "如果用户在续接上文，要充分利用会话上下文解释其真实目标。\n"
        "如果不确定，也必须给出最合理推断。\n\n"
        f"用户请求：\n{_clamp_text(prompt)}{context_block}"
    )


def _normalize_model_payload(payload: dict[str, Any], fallback: TaskIntent, source: str) -> TaskIntent:
    intent_type = str(payload.get("intent_type") or fallback.intent_type).strip() or fallback.intent_type
    goal = str(payload.get("goal") or fallback.goal).strip() or fallback.goal
    request_style = str(payload.get("request_style") or fallback.request_style).strip() or fallback.request_style
    target = str(payload.get("target") or fallback.target).strip()
    constraints_raw = payload.get("constraints") or []
    constraints: list[str] = []
    if isinstance(constraints_raw, list):
        constraints = [str(item).strip() for item in constraints_raw if str(item).strip()]
    expected_output = str(payload.get("expected_output") or fallback.expected_output).strip() or fallback.expected_output
    is_followup = bool(payload.get("is_followup"))
    try:
        model_confidence = float(payload.get("confidence") or fallback.confidence)
    except (TypeError, ValueError):
        model_confidence = fallback.confidence
    model_confidence = max(0.0, min(model_confidence, 1.0))

    # 将模型置信度与启发式信号置信度加权融合
    signal_confidence, _ = _compute_signal_confidence(
        text=goal,
        intent_type=intent_type,
        request_style=request_style,
        has_target=bool(target),
        has_constraints=bool(constraints),
        is_followup=is_followup,
        inferred_from_context=fallback.inferred_from_context or is_followup,
    )
    # 模型权重 0.6，信号权重 0.4
    blended_confidence = round(model_confidence * 0.6 + signal_confidence * 0.4, 3)

    # 歧义检测
    ambiguity_note = _detect_ambiguity(goal, intent_type, bool(target), blended_confidence)

    return TaskIntent(
        intent_type=intent_type,
        goal=goal,
        request_style=request_style,
        target=target,
        constraints=tuple(constraints[:6]),
        expected_output=expected_output,
        is_followup=is_followup,
        confidence=max(blended_confidence, fallback.confidence),
        source=source,
        context_summary=fallback.context_summary,
        inferred_from_context=fallback.inferred_from_context or is_followup,
        ambiguity_note=ambiguity_note or fallback.ambiguity_note,
    )


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


async def _interpret_with_openai(prompt: str, fallback: TaskIntent) -> TaskIntent | None:
    api_key = get_effective_api_key("openai")
    base_url = get_effective_base_url("openai")
    if not api_key or not base_url:
        return None
    payload = {
        "model": get_effective_model("openai"),
        "messages": [
            {"role": "system", "content": "你是任务意图解释器，只返回 JSON。"},
            {"role": "user", "content": _build_interpreter_prompt(prompt, fallback.context_summary)},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = urljoin(base_url.rstrip("/") + "/", "chat/completions")
    async with httpx.AsyncClient(timeout=DECISION_INTERPRETER_TIMEOUT_SECONDS) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    content = str((((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")).strip()
    if not content:
        return None
    parsed = json.loads(_strip_json_fence(content))
    if not isinstance(parsed, dict):
        return None
    return _normalize_model_payload(parsed, fallback, "openai_interpreter")


async def _interpret_with_claude(prompt: str, fallback: TaskIntent) -> TaskIntent | None:
    api_key = get_effective_api_key("claude")
    if not api_key:
        return None
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query

    options = ClaudeAgentOptions(
        permission_mode="acceptEdits",
        env={
            "ANTHROPIC_API_KEY": api_key,
            "ANTHROPIC_BASE_URL": get_effective_base_url("claude"),
            "ANTHROPIC_MODEL": get_effective_model("claude"),
        },
        cwd=".",
        tools=[],
        allowed_tools=[],
        system_prompt="你是任务意图解释器，只返回 JSON。",
        continue_conversation=False,
        resume=None,
    )
    text_parts: list[str] = []
    async for message in query(prompt=_build_interpreter_prompt(prompt, fallback.context_summary), options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
        elif isinstance(message, ResultMessage) and message.result:
            text_parts.append(message.result)
    content = "\n".join(part for part in text_parts if part).strip()
    if not content:
        return None
    parsed = json.loads(_strip_json_fence(content))
    if not isinstance(parsed, dict):
        return None
    return _normalize_model_payload(parsed, fallback, "claude_interpreter")


async def interpret_task_intent(prompt: str, provider: str, session_scope: str | None = None, channel: str | None = None) -> TaskIntent:
    fallback = heuristic_task_intent(prompt, session_scope)
    if not _should_use_model(prompt, provider):
        return fallback
    try:
        if provider == "openai":
            interpreted = await _interpret_with_openai(prompt, fallback)
        elif provider == "claude":
            interpreted = await _interpret_with_claude(prompt, fallback)
        else:
            interpreted = None
    except Exception:
        interpreted = None
    return interpreted or fallback
