from __future__ import annotations



import asyncio

import os

import shutil

import sys

import uuid

import unicodedata

from dataclasses import dataclass

from datetime import datetime

from pathlib import Path

from time import perf_counter



from prompt_toolkit import prompt as pt_prompt

from prompt_toolkit.formatted_text import ANSI

from prompt_toolkit.auto_suggest import AutoSuggestFromHistory

from prompt_toolkit.completion import Completer, Completion

from prompt_toolkit.history import InMemoryHistory

from prompt_toolkit.styles import Style



from weclaw.agents.router import AgentServiceError, build_tui_conversation

from weclaw.agents.runtime import run_agent_for_conversation

from weclaw.capabilities.catalog import build_tool_catalog_text, build_tool_detail_text, build_workflow_catalog_text, build_workflow_detail_text

from weclaw.capabilities.runtime import start_background_capability_watcher, stop_background_capability_watcher

from weclaw.capabilities.tools import ToolContext

from weclaw.core.confirmation import (

    ToolConfirmationRequest,

    clear_session_tool_grants,

    grant_session_tool_access,

    list_session_tool_grants,

    normalize_confirmation_reply,

    revoke_session_tool_grant,

)

from weclaw.core.response import AgentReply

from weclaw.config import PROJECT_ROOT, SHOW_TOOL_TRACE, TUI_OUTPUT_DIR, WORKSPACE_DIR
from weclaw.core.model_profiles import render_model_profiles, resolve_model_profile_selector, set_active_model_profile
from weclaw.core.provider_model import get_effective_model, get_provider_mode_label
from weclaw.core.provider_state import get_provider
from weclaw.core.delivery import DeliveryRouter

from weclaw.decision.render import render_decision_plan_debug

from weclaw.decision.router import build_decision_plan

from weclaw.memory.intent import build_memory_intent_ack, detect_memory_intent, should_auto_accept_memory_intent

from weclaw.memory.store import append_memory_candidate, append_structured_long_term_memory, clear_session_context, create_memory_metadata

from weclaw.tasks.runtime import start_background_scheduler, stop_background_scheduler

from weclaw.tasks.service import handle_task_command

from weclaw.tasks.store import init_task_db

from weclaw.memory.session import init_session_db, clear_session_id, get_session_file

from weclaw.skills.store import list_skills, get_skill, get_last_matched_skills



TUI_SESSION_SCOPE_PREFIX = "tui"

TUI_INSTANCE_ID = os.getenv("WECLAW_TUI_INSTANCE_ID", f"pid{os.getpid()}_{uuid.uuid4().hex[:8]}")

MIN_PANEL_WIDTH = 72

PROMPT = "> "

INPUT_HISTORY_LIMIT = 100

INPUT_HISTORY: list[str] = []

PROMPT_HISTORY = InMemoryHistory()

THEME_PRIMARY = "38;2;199;125;43"
THEME_PRIMARY_BOLD = "1;38;2;199;125;43"
THEME_SECONDARY = "38;2;154;143;133"
THEME_MUTED = "38;2;111;102;94"
THEME_SOFT = "38;2;221;214;207"
THEME_OK = "38;2;72;187;120"
THEME_WARN = "38;2;231;177;73"
THEME_ERROR = "31;1"
TUI_COLOR_MODE = (os.getenv("WECLAW_TUI_COLOR_MODE", "auto") or "auto").strip().lower()


def _is_compat_color_mode() -> bool:
    if TUI_COLOR_MODE == "compat":
        return True
    if TUI_COLOR_MODE == "full":
        return False
    term_program = (os.getenv("TERM_PROGRAM", "") or "").lower()
    if "xshell" in term_program:
        return True

    term = (os.getenv("TERM", "") or "").lower()
    colorterm = (os.getenv("COLORTERM", "") or "").lower()
    ssh_session = any(os.getenv(name) for name in ("SSH_TTY", "SSH_CONNECTION", "SSH_CLIENT"))

    known_full_terminals = ("vscode", "apple_terminal", "iterm", "wezterm", "windows_terminal")
    if any(name in term_program for name in known_full_terminals):
        return False

    if ssh_session and colorterm not in {"truecolor", "24bit"}:
        return True

    return term in {"xterm", "xterm-color", "vt100", "vt220", "ansi", "cygwin"}


if _is_compat_color_mode():
    THEME_PRIMARY = "33"
    THEME_PRIMARY_BOLD = "1;33"
    THEME_SECONDARY = "37"
    THEME_MUTED = "90"
    THEME_SOFT = "37"
    THEME_OK = "32"
    THEME_WARN = "33"
    THEME_ERROR = "31;1"

PROMPT_STYLE = Style.from_dict(
    {
        "completion-menu": "#ddd6cf" if _is_compat_color_mode() else "bg:#201c1a #ddd6cf",
        "completion-menu.completion.current": "bold #ffd75f" if _is_compat_color_mode() else "bg:#c77d2b #fffaf3 bold",
        "completion-menu.meta.completion": "#bfbfbf" if _is_compat_color_mode() else "bg:#201c1a #9a8f85",
        "completion-menu.meta.completion.current": "#ffd75f" if _is_compat_color_mode() else "bg:#c77d2b #fff1dc",
        "auto-suggestion": "#9e9e9e" if _is_compat_color_mode() else "#8b8178",
    }
)




class TuiMode:

    NORMAL = "Normal"

    MULTILINE = "Multiline"

    BUSY = "Busy"





@dataclass(frozen=True, slots=True)

class CommandInfo:

    name: str

    description: str





COMMANDS = [
    CommandInfo("/help", "查看帮助"),
    CommandInfo("/palette", "打开命令面板"),
    CommandInfo("/status", "查看当前 TUI 状态"),
    CommandInfo("/clear", "清屏并重绘状态栏"),

    CommandInfo("/retry", "重发上一条用户输入"),

    CommandInfo("/reset", "清空 TUI 独立连续会话"),
    CommandInfo("/provider", "查看当前 Agent Provider"),
    CommandInfo("/model", "查看或切换模型 Provider"),
    CommandInfo("/schedule_in", "创建单次定时任务"),

    CommandInfo("/tasks", "查看当前 TUI 定时任务"),

    CommandInfo("/cancel", "取消指定定时任务"),

    CommandInfo("/paste", "进入多行输入，支持 /preview /send /cancel"),

    CommandInfo("/skills", "查看可用技能"),

    CommandInfo("/tools", "查看可用工具"),

    CommandInfo("/workflows", "查看可用工作流"),

    CommandInfo("/plan", "查看请求路由计划"),

    CommandInfo("/grants", "查看会话工具授权"),

    CommandInfo("/revoke", "撤销工具授权"),

    CommandInfo("/exit", "退出"),

]

COMMAND_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Session", ("/status", "/clear", "/retry", "/reset", "/provider", "/model", "/exit")),
    ("Input", ("/paste", "/plan")),
    ("Capabilities", ("/skills", "/tools", "/workflows")),
    ("Tasks", ("/schedule_in", "/tasks", "/cancel")),
    ("Permissions", ("/grants", "/revoke")),
)




@dataclass(slots=True)

class ConsoleBot:

    indicator_pause: asyncio.Event | None = None



    async def send_text(self, target_id: str, text: str) -> None:

        print_message_block("Tool", text, accent=THEME_SECONDARY)



    async def send_message(self, chat_id: str | int, text: str) -> None:

        await self.send_text(str(chat_id), text)



    async def confirm_tool_use(self, target_id: str, request: ToolConfirmationRequest) -> bool:
        pause_event = self.indicator_pause
        if pause_event is not None:
            pause_event.set()
        try:
            detail_lines = [
                color("工具请求正在等待你的确认。", THEME_WARN),
                "",
                request.prompt,
                "",
                f"Tool      {request.tool_name}",
                f"Category  {request.category}",
                f"Risk      {request.risk_level}",
            ]
            if request.summary:
                detail_lines.append(f"Summary   {request.summary}")
            detail_lines.extend(
                [
                    "",
                    "回复：允许 = 执行一次；本会话允许 = 本会话同工具自动执行；拒绝 = 取消。",
                ]
            )
            print_message_block("Tool Approval", "\n".join(detail_lines), subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Blocked"), accent=THEME_WARN)
            answer = await asyncio.to_thread(input, color("确认执行? [允许/本会话允许/拒绝] ", THEME_WARN))
            decision = normalize_confirmation_reply(answer)
            if decision == "approve_session":
                granted = grant_session_tool_access(request.session_scope, request)
                if granted:
                    print_message_block("Tool Approval", f"已记住当前会话对工具 {request.tool_name} 的自动授权。", subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Session grant"), accent=THEME_OK)
                else:
                    print_message_block("Tool Approval", "当前工具不支持会话级授权，已按单次确认执行。", subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Session grant"), accent=THEME_MUTED)
                return True
            approved = decision == "approve_once"
            if not approved:
                print_message_block("Tool Approval", f"已拒绝工具 {request.tool_name}，本次操作已取消。", subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Rejected"), accent=THEME_MUTED)
            return approved
        finally:
            if pause_event is not None:
                pause_event.clear()




@dataclass(slots=True)

class TuiState:

    session_scope: str

    provider: str

    mode: str = TuiMode.NORMAL

    is_busy: bool = False

    last_error: str | None = None

    last_image_path: str | None = None

    last_latency_ms: int | None = None

    last_user_input: str | None = None





class CommandCompleter(Completer):

    def get_completions(self, document, complete_event):

        text = document.text_before_cursor.lstrip()

        if not text.startswith("/"):

            return

        for command in COMMANDS:

            if command.name.startswith(text):

                yield Completion(

                    command.name,

                    start_position=-len(text),

                    display=command.name,

                    display_meta=command.description,

                )





def get_tui_scope() -> str:

    return f"{TUI_SESSION_SCOPE_PREFIX}:{TUI_INSTANCE_ID}"





def configure_stdio() -> None:

    for stream in (sys.stdin, sys.stdout, sys.stderr):

        if hasattr(stream, "reconfigure"):

            stream.reconfigure(encoding="utf-8")





def supports_color() -> bool:

    return sys.stdout.isatty() and os.getenv("NO_COLOR") is None





def color(text: str, code: str) -> str:

    if not supports_color():

        return text

    return f"\033[{code}m{text}\033[0m"





def terminal_width() -> int:

    width = shutil.get_terminal_size(fallback=(96, 24)).columns

    return max(MIN_PANEL_WIDTH, min(width, 110))





def display_path(path: Path) -> str:

    try:

        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))

    except ValueError:

        return path.name





def display_width(text: str) -> int:

    width = 0

    for char in text:

        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1

    return width





def trim_right(text: str, max_width: int) -> str:

    result: list[str] = []

    width = 0

    for char in text:

        char_width = 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1

        if width + char_width > max_width:

            break

        result.append(char)

        width += char_width

    return "".join(result)





def trim_middle(text: str, max_width: int) -> str:

    if display_width(text) <= max_width:

        return text

    if max_width <= 3:

        return trim_right(text, max_width)

    keep_left = max_width // 2 - 1

    keep_right = max_width - keep_left - 3

    left = trim_right(text, keep_left)

    right = text[-keep_right:] if keep_right > 0 else ""

    return f"{left}...{right}"





def pad_display(text: str, width: int) -> str:

    current_width = display_width(text)

    if current_width > width:

        text = trim_right(text, width)

        current_width = display_width(text)

    return text + " " * max(0, width - current_width)





def box_line(text: str, width: int, color_code: str | None = None) -> str:

    inner_width = width - 4

    line = f"│ {pad_display(text, inner_width)} │"

    return color(line, color_code) if color_code else line





def box_line_center(text: str, width: int, color_code: str | None = None) -> str:

    inner_width = width - 4

    visible_text = text if display_width(text) <= inner_width else trim_middle(text, inner_width)

    left_padding = max(0, (inner_width - display_width(visible_text)) // 2)

    padded = " " * left_padding + visible_text

    line = f"│ {pad_display(padded, inner_width)} │"

    return color(line, color_code) if color_code else line





def panel_line(label: str, value: str, width: int, accent: str, accent_color: str) -> str:

    inner_width = width - 4

    accent_text = color(accent, accent_color)

    label_text = f"{label:<10}"

    value_width = max(8, inner_width - len(label_text) - 3)

    value_text = trim_middle(value, value_width)

    return f"│ {accent_text} {label_text} {pad_display(value_text, value_width)} │"





def print_header() -> None:
    width = terminal_width()
    rule = "─" * (width - 2)
    session_file = get_session_file(get_tui_scope())
    print(color(f"╭{rule}╮", THEME_SECONDARY))
    print(box_line_center("WeClaw TUI", width, THEME_PRIMARY_BOLD))
    print(box_line_center("Local Agent Console · multi-agent workspace", width, THEME_SECONDARY))
    print(panel_line("Provider", get_provider(), width, "●", THEME_PRIMARY_BOLD))
    print(panel_line("Workspace", display_path(WORKSPACE_DIR), width, "◆", THEME_PRIMARY))
    print(panel_line("Session", display_path(session_file), width, "◦", THEME_PRIMARY))
    print(panel_line("Images", display_path(TUI_OUTPUT_DIR), width, "■", THEME_PRIMARY))
    print(color(f"├{rule}┤", THEME_SECONDARY))
    print(box_line("Enter 发送 · /palette 命令面板 · /paste 多行输入 · /status 运行状态", width, THEME_MUTED))
    print(box_line("/skills 技能 · /tools 工具 · /workflows 工作流 · /grants 授权 · /exit 退出", width, THEME_MUTED))
    print(color(f"╰{rule}╯", THEME_SECONDARY))
    print()




def print_status_bar(state: TuiState) -> None:
    width = terminal_width()
    rule = "─" * (width - 2)
    instance_name = trim_middle(state.session_scope.split(":", 1)[-1], 20)
    grants = list_session_tool_grants(state.session_scope)
    latency = f"{state.last_latency_ms}ms" if state.last_latency_ms is not None else "-"
    state_color = THEME_WARN if state.is_busy else THEME_OK
    status_line = "  ".join(
        [
            f"mode={state.mode}",
            f"provider={state.provider}",
            f"model={get_effective_model(state.provider) or '-'}",
            f"grants={len(grants)}",
            f"latency={latency}",
        ]
    )
    print(color(f"╭{rule}╮", THEME_MUTED))
    print(box_line(f"● {status_line}", width, state_color))
    print(box_line(f"workspace={display_path(WORKSPACE_DIR)}  session={instance_name}", width, THEME_SECONDARY))
    print(color(f"╰{rule}╯", THEME_MUTED))




def print_message_block(title: str, text: str, subtitle: str | None = None, accent: str = THEME_PRIMARY) -> None:

    width = terminal_width()

    lines = text.rstrip().splitlines() if text.strip() else ["(empty)"]

    print()

    print(color("═" * width, accent))

    header = title if not subtitle else f"{title}  |  {subtitle}"

    print(color(header, f"{accent};1" if ";" not in accent else accent))

    print(color("─" * width, accent))

    for line in lines:

        print(line)

    print(color("═" * width, accent))

    print()





def print_turn_block(title: str, text: str, subtitle: str | None = None, accent: str = THEME_PRIMARY) -> None:

    print_message_block(title, text, subtitle=subtitle, accent=accent)





def build_meta_subtitle(*parts: str) -> str:

    return "  |  ".join(part.strip() for part in parts if part and part.strip())





def record_input_history(text: str) -> None:

    value = text.strip()

    if not value:

        return

    if INPUT_HISTORY and INPUT_HISTORY[-1] == value:

        return

    INPUT_HISTORY.append(value)

    if len(INPUT_HISTORY) > INPUT_HISTORY_LIMIT:

        del INPUT_HISTORY[:-INPUT_HISTORY_LIMIT]





def render_markdown_for_terminal(text: str) -> str:

    lines = text.rstrip().splitlines()

    if not lines:

        return ""

    rendered: list[str] = []

    in_code = False

    for line in lines:

        stripped = line.strip()

        if stripped.startswith("```"):

            if not in_code:

                label = stripped[3:].strip() or "code"

                rendered.append(color(f"┌─ {label} " + "─" * max(0, min(48, terminal_width()) - len(label) - 6), THEME_MUTED))

                in_code = True

            else:

                rendered.append(color("└" + "─" * max(0, min(48, terminal_width()) - 1), THEME_MUTED))

                in_code = False

            continue

        if in_code:

            rendered.append(color(line, THEME_SECONDARY))

            continue

        if stripped.startswith("### "):

            rendered.append(color(f"[ {stripped[4:]} ]", THEME_PRIMARY_BOLD))

            continue

        if stripped.startswith("## "):

            rendered.append(color(f"[ {stripped[3:]} ]", THEME_PRIMARY_BOLD))

            continue

        if stripped.startswith("# "):

            rendered.append(color(f"[ {stripped[2:]} ]", THEME_PRIMARY_BOLD))

            continue

        if stripped.startswith("> "):

            rendered.append(color(f"│ {stripped[2:]}", THEME_MUTED))

            continue

        if stripped.startswith("- ") or stripped.startswith("* "):

            rendered.append(f"  • {stripped[2:]}")

            continue

        rendered.append(line.replace("**", ""))

    return "\n".join(rendered)





def format_command_suggestions(prefix: str, selected_index: int) -> list[str]:
    matched = [command for command in COMMANDS if command.name.startswith(prefix)] if prefix else []

    if prefix == "/":

        matched = COMMANDS

    if not matched:

        return [color("  没有匹配的命令", THEME_MUTED)]

    name_width = max(display_width(command.name) for command in matched)

    selected_index = selected_index % len(matched)

    lines: list[str] = []

    for index, command in enumerate(matched):

        marker = ">" if index == selected_index else " "

        command_name = pad_display(command.name, name_width)

        if index == selected_index:

            lines.append(f"{color(marker, THEME_PRIMARY_BOLD)} {color(command_name, THEME_PRIMARY_BOLD)}  {command.description}")

        else:

            lines.append(f"{marker} {command_name}  {command.description}")

    return lines


def render_command_palette() -> str:
    command_by_name = {command.name: command for command in COMMANDS}
    lines: list[str] = [
        "输入命令名称即可执行；输入命令前缀时会自动补全。",
        "",
    ]
    for section, names in COMMAND_SECTIONS:
        lines.append(color(section, THEME_PRIMARY_BOLD))
        for name in names:
            command = command_by_name.get(name)
            if command is None:
                continue
            lines.append(f"  {pad_display(command.name, 14)} {command.description}")
        lines.append("")
    lines.append(color("Tips", THEME_PRIMARY_BOLD))
    lines.append("  Tab/方向键可选择补全项；/paste 支持多行输入；/grants 可查看本会话工具授权。")
    return "\n".join(lines).rstrip()


def read_prompt() -> str:
    return pt_prompt(

        ANSI(color(PROMPT, THEME_PRIMARY_BOLD)),

        completer=CommandCompleter(),

        complete_while_typing=True,

        complete_in_thread=True,

        history=PROMPT_HISTORY,

        auto_suggest=AutoSuggestFromHistory(),

        style=PROMPT_STYLE,

    )





def read_multiline() -> str:

    print_message_block(

        "Multiline",

        "进入多行输入模式。单独一行 `.` 或 `/send` 发送，`/preview` 预览，`/cancel` 放弃。",

        subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), TuiMode.MULTILINE),

        accent=THEME_PRIMARY,

    )

    lines: list[str] = []

    while True:

        line = input("... ")

        if line == "/cancel":

            return ""

        if line in {".", "/send"}:

            break

        if line == "/preview":

            preview = "\n".join(lines).strip()

            if not preview:

                print_message_block("Preview", "当前多行输入为空。", subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Multiline"), accent=THEME_MUTED)

            else:

                print_message_block("Preview", preview, subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), f"{len(lines)} lines"), accent=THEME_MUTED)

            continue

        lines.append(line)

    return "\n".join(lines).strip()





def print_help() -> None:
    print_message_block("Command Palette", render_command_palette(), subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "TUI commands"), accent=THEME_PRIMARY)




def print_status(state: TuiState) -> None:

    text = "\n".join([

        f"Provider: {state.provider}",

        f"Mode: {state.mode}",

        f"Session: {state.session_scope}",

        f"Workspace: {display_path(WORKSPACE_DIR)}",

        f"Images: {state.last_image_path or '-'}",

        f"Last latency: {state.last_latency_ms if state.last_latency_ms is not None else '-'} ms",

        f"Last error: {state.last_error or '-'}",

    ])

    print_message_block("Status", text, subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Runtime"), accent=THEME_SECONDARY)





def print_skills(name: str | None = None) -> None:

    if name:

        skill = get_skill(name.strip().lower())

        if skill is None:

            print_message_block("Skills", f"没有找到名为 {name} 的 skill。", subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Skill lookup"), accent=THEME_MUTED)

        elif not skill.file_path.exists():

            print_message_block("Skills", f"Skill '{skill.name}' 的文件暂时不存在。", subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Skill lookup"), accent=THEME_MUTED)

        else:

            detail = skill.file_path.read_text(encoding="utf-8").strip()

            print_message_block("Skills", f"Skill: {skill.name}\n标题: {skill.title}\n说明: {skill.description}\n\n{detail}", subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Skill detail"), accent=THEME_PRIMARY)

    else:

        lines = ["当前可用的 skills："]

        for skill in list_skills():

            lines.append(f"- {skill.name}: {skill.description}")

        lines.append("\n发送 /skills 技能名 查看详情。")

        print_message_block("Skills", "\n".join(lines), subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Skill catalog"), accent=THEME_PRIMARY)





def print_tools(name: str | None = None, provider: str | None = None) -> None:

    active_provider = provider or get_provider()

    ctx = ToolContext(sender=None, target_id="tui")

    if name:

        detail = build_tool_detail_text(name.strip(), provider=active_provider, context=ctx)

        if detail is None:

            print_message_block("Tools", f"没有找到名为 {name} 的工具，或当前 Provider 不可用。", subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), active_provider), accent=THEME_MUTED)

            return

        print_message_block("Tools", detail, subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), f"{active_provider} detail"), accent=THEME_PRIMARY)

        return

    catalog = build_tool_catalog_text(provider=active_provider, context=ctx)

    print_message_block("Tools", catalog, subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), f"{active_provider} catalog"), accent=THEME_PRIMARY)





def print_workflows(name: str | None = None) -> None:

    if name:

        detail = build_workflow_detail_text(name.strip())

        if detail is None:

            print_message_block("Workflows", f"没有找到名为 {name} 的 workflow。", subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Workflow lookup"), accent=THEME_MUTED)

            return

        print_message_block("Workflows", detail, subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Workflow detail"), accent=THEME_PRIMARY)

        return

    catalog = build_workflow_catalog_text()

    print_message_block("Workflows", catalog, subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Workflow catalog"), accent=THEME_PRIMARY)





def print_grants(session_scope: str) -> None:

    grants = list_session_tool_grants(session_scope)

    if not grants:

        print_message_block("Grants", "当前会话没有已授权自动执行的工具。", subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Session grants"), accent=THEME_MUTED)

        return

    lines = ["当前会话工具授权："]

    for grant in grants:

        lines.append(f"- {grant.tool_name} [{grant.risk_level}/{grant.category}] 授权于 {grant.granted_at}")

    print_message_block("Grants", "\n".join(lines), subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Session grants"), accent=THEME_PRIMARY)





async def print_plan(prompt: str, state: TuiState) -> None:

    if not prompt.strip():

        print_message_block("Plan", "用法：/plan 这里填写要分析的请求", subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Decision plan"), accent=THEME_MUTED)

        return

    plan = await build_decision_plan(

        prompt=prompt,

        provider=state.provider,

        session_scope=state.session_scope,

        channel="tui",

    )

    print_message_block("Plan", render_decision_plan_debug(plan), subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), state.provider), accent=THEME_PRIMARY)





def print_matched_skills() -> None:

    matched = get_last_matched_skills()

    if not matched:

        return

    names = ", ".join(s.name for s in matched)

    print(color(f"[Skills: {names}]", THEME_MUTED))





def save_reply_images(reply: AgentReply) -> list[Path]:

    saved_paths: list[Path] = []

    if not reply.images:

        return saved_paths

    TUI_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    instance_suffix = TUI_INSTANCE_ID.replace(":", "_")

    for index, image in enumerate(reply.images, 1):

        suffix = image.mime_type.removeprefix("image/") or "png"

        target = TUI_OUTPUT_DIR / f"generated_{instance_suffix}_{timestamp}_{index}.{suffix}"

        target.write_bytes(image.data)

        saved_paths.append(target)

    return saved_paths





async def run_thinking_indicator(stop_event: asyncio.Event, pause_event: asyncio.Event) -> None:

    frames = ["处理中", "处理中.", "处理中..", "处理中..."]

    index = 0

    while not stop_event.is_set():

        if pause_event.is_set():

            await asyncio.sleep(0.1)

            continue

        sys.stdout.write("\r\033[2K" + color(frames[index % len(frames)], THEME_MUTED))

        sys.stdout.flush()

        index += 1

        try:

            await asyncio.wait_for(stop_event.wait(), timeout=0.2)

        except asyncio.TimeoutError:

            continue

    sys.stdout.write("\r\033[2K")

    sys.stdout.flush()





def render_turn(provider: str, reply: AgentReply, saved_images: list[Path], elapsed_ms: int) -> None:

    rule = color("─" * terminal_width(), THEME_MUTED)

    print()

    print(render_markdown_for_terminal(reply.text.rstrip()) if reply.text.strip() else "(empty)")

    if saved_images:

        print()

        for path in saved_images:

            print(color(f"图片输出: {display_path(path)}", THEME_PRIMARY_BOLD))

    print_matched_skills()

    print(rule)

    print()





async def submit_prompt(prompt: str, bot: ConsoleBot, state: TuiState) -> None:

    state.mode = TuiMode.BUSY

    state.is_busy = True

    stop_event = asyncio.Event()

    pause_event = asyncio.Event()

    bot.indicator_pause = pause_event

    indicator_task = asyncio.create_task(run_thinking_indicator(stop_event, pause_event))

    started_at = perf_counter()

    try:

        reply = await run_agent_for_conversation(

            prompt=prompt,

            conversation=build_tui_conversation(get_tui_scope()),

            sender=bot,

            continue_session=True,

            record_text=f"[PowerShell TUI] {prompt}",

        )

    finally:

        stop_event.set()

        await indicator_task

        bot.indicator_pause = None

        state.is_busy = False

        state.mode = TuiMode.NORMAL

    elapsed_ms = int((perf_counter() - started_at) * 1000)

    saved_images = save_reply_images(reply)

    state.last_latency_ms = elapsed_ms

    state.last_user_input = prompt

    state.last_image_path = display_path(saved_images[-1]) if saved_images else None

    render_turn(get_provider().upper(), reply, saved_images, elapsed_ms)





async def run_tui() -> None:

    await init_task_db()

    await init_session_db()

    configure_stdio()

    print_header()

    state = TuiState(session_scope=get_tui_scope(), provider=get_provider())

    print_status_bar(state)

    bot = ConsoleBot()

    conversation = build_tui_conversation(state.session_scope)

    router = DeliveryRouter()

    router.register_conversation(conversation, bot)

    scheduler_runtime = start_background_scheduler(router)

    capability_watcher = start_background_capability_watcher()

    try:

        while True:

            try:

                prompt = await asyncio.to_thread(read_prompt)

            except EOFError:

                print()

                break

            prompt = prompt.strip()

            if not prompt:

                continue

            command = prompt.lower()

            if command in {"/exit", "/quit", "exit", "quit"}:
                break
            if command in {"/help", "/palette", "/commands"}:
                print_help()
                continue
            if command == "/clear":

                os.system("cls" if os.name == "nt" else "clear")

                print_header()

                print_status_bar(state)

                continue

            if command == "/status":

                print_status(state)

                continue

            if command == "/retry":

                if not state.last_user_input:

                    print_message_block("System", "还没有可以重发的上一条输入。", subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Retry"), accent=THEME_MUTED)

                    continue

                prompt = state.last_user_input

            if command == "/provider":
                state.provider = get_provider()
                text = "\n".join(
                    [
                        f"当前 Provider: {state.provider}",
                        f"接口类型: {get_provider_mode_label(state.provider)}",
                        f"当前模型: {get_effective_model(state.provider) or '(empty)'}",
                    ]
                )
                print_message_block("Provider", text, subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Runtime"), accent=THEME_PRIMARY)
                continue
            if command.startswith("/model"):
                parts = prompt.split(maxsplit=1)
                if len(parts) == 1:
                    print_message_block("Model", render_model_profiles(), subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Runtime"), accent=THEME_PRIMARY)
                    continue
                args = parts[1].strip().split()
                if args and args[0].lower() == "use":
                    args = args[1:]
                if not args:
                    print_message_block("Model", render_model_profiles(), subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Runtime"), accent=THEME_PRIMARY)
                    continue
                try:
                    profile_id = resolve_model_profile_selector(args[0]).id
                    profile = set_active_model_profile(profile_id, " ".join(args[1:]) if len(args) > 1 else None)
                except ValueError as exc:
                    print_message_block("Model", str(exc), subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Runtime"), accent=THEME_MUTED)
                    continue
                state.provider = profile.protocol
                print_message_block("Model", f"已切换到 {profile.id}\n当前模型: {profile.model or '(empty)'}", subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Runtime"), accent=THEME_PRIMARY)
                continue
            if command == "/reset":

                clear_session_id(state.session_scope)

                clear_session_context(state.session_scope)

                cleared_grants = clear_session_tool_grants(state.session_scope)

                suffix = f" 已清除 {cleared_grants} 个工具授权。" if cleared_grants else ""

                print_message_block("Session", f"TUI 连续会话已清空。{suffix}", subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Fresh session"), accent=THEME_PRIMARY)

                continue

            if command.startswith("/schedule") or command.startswith("/schedule_in") or command.startswith("/cancel") or command == "/tasks":

                result = await handle_task_command(conversation, prompt)

                if result.handled:

                    print_message_block("Schedule", result.message, subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "TUI task"), accent=THEME_PRIMARY)

                    continue

                continue

            if command == "/paste":

                state.mode = TuiMode.MULTILINE

                print_status_bar(state)

                prompt = await asyncio.to_thread(read_multiline)

                state.mode = TuiMode.NORMAL

                if not prompt:

                    print_message_block("System", "已取消多行输入。", subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), TuiMode.NORMAL), accent=THEME_MUTED)

                    continue

            if command.startswith("/skills"):

                parts = prompt.split(maxsplit=1)

                skill_name = parts[1].strip() if len(parts) > 1 else None

                print_skills(skill_name)

                continue

            if command.startswith("/tools"):

                parts = prompt.split(maxsplit=1)

                tool_name = parts[1].strip() if len(parts) > 1 else None

                print_tools(tool_name, provider=state.provider)

                continue

            if command.startswith("/workflows"):

                parts = prompt.split(maxsplit=1)

                workflow_name = parts[1].strip() if len(parts) > 1 else None

                print_workflows(workflow_name)

                continue

            if command.startswith("/plan"):

                parts = prompt.split(maxsplit=1)

                plan_prompt = parts[1].strip() if len(parts) > 1 else ""

                await print_plan(plan_prompt, state)

                continue

            if command == "/grants":

                print_grants(state.session_scope)

                continue

            if command.startswith("/revoke"):

                parts = prompt.split(maxsplit=1)

                if len(parts) == 1 or not parts[1].strip():

                    print_message_block("Grants", "用法：/revoke 工具名", subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Session grants"), accent=THEME_MUTED)

                else:

                    tool_name = parts[1].strip()

                    if revoke_session_tool_grant(state.session_scope, tool_name):

                        print_message_block("Grants", f"已撤销当前会话对工具 {tool_name} 的自动授权。", subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Session grants"), accent=THEME_PRIMARY)

                    else:

                        print_message_block("Grants", f"当前会话没有找到工具 {tool_name} 的授权记录。", subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Session grants"), accent=THEME_MUTED)

                continue



            result = await handle_task_command(conversation, prompt)

            if result.handled:

                print_message_block("Schedule", result.message, subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "TUI task"), accent=THEME_PRIMARY)

                continue



            memory_intent = detect_memory_intent(prompt)

            if memory_intent is not None:

                if should_auto_accept_memory_intent(memory_intent):

                    target = append_structured_long_term_memory(

                        memory_intent.content,

                        memory_intent.category,

                        memory_intent.slot,

                        create_memory_metadata(

                            category=memory_intent.category,

                            slot=memory_intent.slot,

                            reason=memory_intent.reason,

                            source="user_explicit",

                            confidence=memory_intent.confidence,

                        ),

                    )

                    print_message_block(

                        "Memory",

                        build_memory_intent_ack(memory_intent, True, SHOW_TOOL_TRACE, target.name),

                        subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Structured memory"),

                        accent=THEME_PRIMARY,

                    )

                else:

                    candidate_file = append_memory_candidate(

                        memory_intent.content,

                        memory_intent.category,

                        memory_intent.reason,

                        memory_intent.slot,

                        create_memory_metadata(

                            category=memory_intent.category,

                            slot=memory_intent.slot,

                            reason=memory_intent.reason,

                            source="user_candidate",

                            confidence=memory_intent.confidence,

                        ),

                    )

                    print_message_block(

                        "Memory",

                        build_memory_intent_ack(memory_intent, False, SHOW_TOOL_TRACE, candidate_file.name),

                        subtitle=build_meta_subtitle(datetime.now().strftime("%H:%M:%S"), "Candidate memory"),

                        accent=THEME_PRIMARY,

                    )

                continue

            try:

                record_input_history(prompt)

                await submit_prompt(prompt, bot, state)
                print_status_bar(state)
            except AgentServiceError as exc:
                state.last_error = str(exc)
                state.mode = TuiMode.NORMAL
                state.is_busy = False
                print(color(f"错误: {exc}", THEME_ERROR))

            except KeyboardInterrupt:

                print()

                break

    finally:

        stop_background_capability_watcher(capability_watcher)

        stop_background_scheduler(scheduler_runtime)

    print("TUI 已退出。")





def main() -> None:

    try:

        asyncio.run(run_tui())

    except KeyboardInterrupt:

        print("\nTUI 已退出。")





if __name__ == "__main__":

    main()
