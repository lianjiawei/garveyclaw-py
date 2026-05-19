from __future__ import annotations

from collections.abc import Iterable

from weclaw.capabilities.tools import list_openai_tool_names, parse_openai_allowed_tools
from weclaw.config import WORKSPACE_DIR
from weclaw.decision.models import DecisionPlan
from weclaw.decision.render import render_decision_plan
from weclaw.memory.store import build_context_snapshot
from weclaw.skills.store import build_skill_prompt


PROMPTS_DIR = WORKSPACE_DIR / "prompts"


DEFAULT_AGENT_RULES = """
规则：

1. **联网搜索（最高优先）**：需要搜索互联网信息（天气、新闻、百科等）时，必须调用 web_search 工具，禁止用 curl、wget、bash 等任何方式直接爬取网页替代搜索。

2. 当用户询问文件、目录或当前时间时，优先使用工具。

3. 如果需要额外主动给当前会话发送一条消息，请使用 send_message 工具。

4. 不要编造文件内容；如果需要文件数据，就调用工具读取。

5. **Bash 工具使用场景**：当任务涉及以下情况时，请优先使用 Bash 工具编写脚本执行：

   - 多步骤文件操作（批量重命名、移动、复制等）

   - 复杂的数据处理（日志分析、格式转换、统计计算等）

   - 需要自动化重复操作时

   - 处理大量文件或目录时

   - 需要生成报告或汇总信息时

6. **Shell 平台适配**：`bash` 工具已按操作系统自动选择 shell。在 Linux/macOS 上直接编写 Bash 命令；在 Windows 上编写 PowerShell 命令。Windows 下的常用 PowerShell 等效命令参考：

   - 移动/重命名：Move-Item、Rename-Item

   - 删除：Remove-Item

   - 复制：Copy-Item

   - 列出文件：Get-ChildItem

   - 读取文件：Get-Content

   - 搜索：Select-String

7. 写 Bash 脚本时，复杂任务建议先写脚本文件再执行。

8. 当前环境里不要默认使用 `python3`，优先尝试 `python`。

9. 当前环境不保证安装了 `gh` 等额外命令行工具，不要默认依赖它们。

10. **任务管理**：你可以使用 list_tasks 工具查看当前会话的待执行任务，使用 cancel_task 工具取消指定任务，使用 create_task 工具创建单次定时任务。

11. 当用户希望你设置提醒、定时通知、稍后执行某事，而规则层没有直接识别成功时，你可以先用 get_current_time 获取当前时间，自己推算目标执行时间，再调用 create_task 创建任务。

12. 当用户提到取消提醒、取消任务时，请先用 list_tasks 确认任务 ID，再调用 cancel_task 取消。

13. 面向用户回复任务列表时，优先用"第1个、第2个"这类自然序号表达，不要默认暴露内部任务 ID，除非用户明确要求查看 ID。

14. 如果你自己查看了任务列表并准备回复给用户，统一使用这种纯文本格式，不要自由发挥：第一行写"你当前的定时任务："；后面每行一条，格式为"1. 时间 | 类型 | 内容"。

15. 当用户只是想看当前任务或提醒时，回复尽量简洁直接，不要用表格，不要加"Boss"等额外称呼，不要主动问"需要调整或取消吗"这类销售式追问。

16. 回答尽量使用自然、清晰的中文。
""".strip()


def load_prompt_fragment(name: str) -> str | None:
    """从 workspace/prompts/ 加载 prompt 片段，文件不存在时返回 None。"""

    path = PROMPTS_DIR / f"{name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def _format_tool_names(tool_names: Iterable[str] | None) -> str:
    if not tool_names:
        return ""
    return "、".join(f"`{name}`" for name in tool_names)


def _build_image_guidance(provider: str, has_uploaded_image: bool) -> str:
    if not has_uploaded_image:
        return ""
    if provider == "openai":
        return "本轮用户上传了图片，图片已作为视觉输入随用户消息传入。请直接结合图片内容和用户说明回答；如果当前模型不支持视觉输入，请清楚说明模型限制。"
    if provider == "claude":
        return "本轮用户上传了图片。需要查看图片内容时，请先调用 get_uploaded_image 工具获取本轮图片，再结合图片内容和用户说明回答。"
    return "本轮用户上传了图片。请结合图片内容和用户说明回答；如果当前 Provider 不支持图片理解，请清楚说明限制。"


def _build_base_system(
    prompt: str,
    session_scope: str | None,
    decision_plan: DecisionPlan | None,
    *,
    provider: str,
    tool_names: Iterable[str] | None = None,
    has_uploaded_image: bool = False,
) -> str:
    context_snapshot = build_context_snapshot(session_scope, prompt)
    selected_skills, skill_prompt = build_skill_prompt(
        prompt,
        decision_plan.selected_skills if decision_plan is not None else None,
    )
    selected_skill_names = ", ".join(skill.name for skill in selected_skills) or "无"
    decision_text = render_decision_plan(decision_plan)
    tool_list_text = _format_tool_names(tool_names)
    image_guidance = _build_image_guidance(provider, has_uploaded_image)

    system_template = load_prompt_fragment("system")
    if system_template:
        system = system_template.format(
            WORKSPACE_DIR=WORKSPACE_DIR,
            CONTEXT_SNAPSHOT=context_snapshot,
            SELECTED_SKILLS=selected_skill_names,
            SKILL_PROMPT=skill_prompt,
            DECISION_PLAN=decision_text,
            PROVIDER=provider,
            TOOL_LIST=tool_list_text,
            IMAGE_GUIDANCE=image_guidance,
        )
        extras: list[str] = []
        if decision_text and "{DECISION_PLAN}" not in system_template:
            extras.append(decision_text)
        if image_guidance and "{IMAGE_GUIDANCE}" not in system_template:
            extras.append(image_guidance)
        if tool_list_text and "{TOOL_LIST}" not in system_template:
            extras.append(f"当前 {provider} 模式可用工具：{tool_list_text}")
        if extras:
            return "\n\n".join([system, *extras])
        return system

    image_hint = f"\n\n{image_guidance}" if image_guidance else ""
    tool_hint = f"\n\n当前 {provider} 模式可用工具：{tool_list_text}" if tool_list_text else ""

    return f"""
你现在运行在一个多入口个人智能体系统中。

当前工作区目录是：{WORKSPACE_DIR}

下面是当前可用的分层上下文快照：

{context_snapshot}

本轮命中的 skill：{selected_skill_names}

{skill_prompt}

{decision_text}{image_hint}{tool_hint}
""".strip()


def build_agent_instructions(
    prompt: str,
    session_scope: str | None = None,
    decision_plan: DecisionPlan | None = None,
    *,
    provider: str,
    tool_names: Iterable[str] | None = None,
    has_uploaded_image: bool = False,
) -> str:
    """Build the shared system instructions used by all text providers."""

    system = _build_base_system(
        prompt,
        session_scope,
        decision_plan,
        provider=provider,
        tool_names=tool_names,
        has_uploaded_image=has_uploaded_image,
    )
    rules_text = load_prompt_fragment("rules") or DEFAULT_AGENT_RULES
    tools_text = load_prompt_fragment("tools") or ""

    parts = [system, rules_text]
    if tools_text:
        parts.append(tools_text)
    return "\n\n".join(parts)


def build_claude_instructions(
    prompt: str,
    session_scope: str | None = None,
    decision_plan: DecisionPlan | None = None,
    *,
    has_uploaded_image: bool = False,
) -> str:
    return build_agent_instructions(
        prompt,
        session_scope,
        decision_plan,
        provider="claude",
        has_uploaded_image=has_uploaded_image,
    )


def build_openai_instructions(
    prompt: str,
    session_scope: str | None = None,
    decision_plan: DecisionPlan | None = None,
    *,
    has_uploaded_image: bool = False,
) -> str:
    tool_names = list_openai_tool_names(allowed_names=parse_openai_allowed_tools())
    return build_agent_instructions(
        prompt,
        session_scope,
        decision_plan,
        provider="openai",
        tool_names=tool_names,
        has_uploaded_image=has_uploaded_image,
    )
