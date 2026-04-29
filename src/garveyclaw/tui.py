from __future__ import annotations

import asyncio
import os
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from garveyclaw.agent_client import AgentServiceError, run_agent
from garveyclaw.agent_response import AgentReply
from garveyclaw.config import AGENT_PROVIDER, PROJECT_ROOT, TUI_OUTPUT_DIR, WORKSPACE_DIR
from garveyclaw.session_store import clear_session_id, get_session_file


TUI_SESSION_SCOPE = "tui"
TUI_CHAT_ID = 0
MIN_PANEL_WIDTH = 72
PROMPT = "claw> "


@dataclass(frozen=True, slots=True)
class CommandInfo:
    name: str
    description: str


COMMANDS = [
    CommandInfo("/help", "查看帮助"),
    CommandInfo("/reset", "清空 TUI 独立连续会话"),
    CommandInfo("/provider", "查看当前 Agent Provider"),
    CommandInfo("/paste", "进入多行输入，单独一行 . 结束"),
    CommandInfo("/exit", "退出"),
]


@dataclass(slots=True)
class ConsoleBot:
    """把 Agent 的 send_message 工具适配成本地终端输出。"""

    async def send_message(self, chat_id: int, text: str) -> None:
        print_block("Agent message", text)


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
    """启动页只展示相对路径，避免把用户目录等隐私信息暴露在终端截图里。"""

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


def trim_left(text: str, max_width: int) -> str:
    result: list[str] = []
    width = 0
    for char in reversed(text):
        char_width = 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
        if width + char_width > max_width:
            break
        result.append(char)
        width += char_width
    return "".join(reversed(result))


def trim_middle(text: str, max_width: int) -> str:
    if display_width(text) <= max_width:
        return text
    if max_width <= 3:
        return trim_right(text, max_width)
    keep_left = max_width // 2 - 1
    keep_right = max_width - keep_left - 3
    return f"{trim_right(text, keep_left)}...{trim_left(text, keep_right)}"


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


def panel_line(label: str, value: str, width: int) -> str:
    inner_width = width - 4
    label_text = f"{label:<10}"
    value_width = max(8, inner_width - len(label_text) - 1)
    value_text = trim_middle(value, value_width)
    return box_line(f"{label_text} {value_text}", width)


def print_header() -> None:
    width = terminal_width()
    rule = "─" * (width - 2)
    session_file = get_session_file(TUI_SESSION_SCOPE)

    logo = [
        "  ____                         ____ _               ",
        " / ___| __ _ _ ____   _____ _ / ___| | __ ___      __",
        "| |  _ / _` | '__\\ \\ / / _ \\ | |   | |/ _` \\ \\ /\\ / /",
        "| |_| | (_| | |   \\ V /  __/ | |___| | (_| |\\ V  V / ",
        " \\____|\\__,_|_|    \\_/ \\___|  \\____|_|\\__,_| \\_/\\_/  ",
    ]

    print(color(f"╭{rule}╮", "36"))
    for line in logo:
        print(box_line(line, width, "36;1"))
    print(color(f"├{rule}┤", "36"))
    print(panel_line("Provider", AGENT_PROVIDER, width))
    print(panel_line("Workspace", display_path(WORKSPACE_DIR), width))
    print(panel_line("Session", display_path(session_file), width))
    print(panel_line("Images", display_path(TUI_OUTPUT_DIR), width))
    print(color(f"├{rule}┤", "36"))
    hints = [
        "输入问题直接聊天；输入 / 会在底部显示命令提示",
        "/paste 多行输入；/reset 清空会话；/help 查看命令；/exit 退出",
    ]
    for line in hints:
        print(box_line(line, width))
    print(color(f"╰{rule}╯", "36"))
    print()


def format_command_suggestions(prefix: str) -> list[str]:
    return format_command_suggestions_with_selection(prefix, 0)


def matched_commands(prefix: str) -> list[CommandInfo]:
    if not prefix.startswith("/"):
        return []

    matched = [command for command in COMMANDS if command.name.startswith(prefix)]
    if not matched and prefix == "/":
        matched = COMMANDS
    return matched


def format_command_suggestions_with_selection(prefix: str, selected_index: int) -> list[str]:
    matched = matched_commands(prefix)
    if not prefix.startswith("/"):
        return []
    if not matched:
        return [color("  没有匹配的命令", "90")]

    name_width = max(display_width(command.name) for command in matched)
    selected_index = selected_index % len(matched)
    lines: list[str] = []
    for index, command in enumerate(matched):
        marker = ">" if index == selected_index else " "
        command_name = pad_display(command.name, name_width)
        if index == selected_index:
            lines.append(f"{color(marker, '33;1')} {color(command_name, '36;1')}  {command.description}")
        else:
            lines.append(f"{marker} {command_name}  {command.description}")
    return lines


def read_prompt() -> str:
    if os.name == "nt" and sys.stdin.isatty():
        return read_prompt_windows()
    return input(color(PROMPT, "36;1"))


def read_prompt_windows() -> str:
    import msvcrt

    buffer: list[str] = []
    selected_index = 0

    def current_text() -> str:
        return "".join(buffer)

    def render() -> None:
        text = current_text()
        suggestions = format_command_suggestions_with_selection(text, selected_index)
        sys.stdout.write("\r\033[J")
        sys.stdout.write(color(PROMPT, "36;1") + text)
        if suggestions:
            sys.stdout.write("\n" + "\n".join(suggestions))
            sys.stdout.write(f"\033[{len(suggestions)}A")
            sys.stdout.write("\r\033[2K" + color(PROMPT, "36;1") + text)
        sys.stdout.flush()

    sys.stdout.write(color(PROMPT, "36;1"))
    sys.stdout.flush()

    while True:
        char = msvcrt.getwch()
        if char in {"\r", "\n"}:
            sys.stdout.write("\r\033[J")
            sys.stdout.write(color(PROMPT, "36;1") + current_text() + "\n")
            sys.stdout.flush()
            return current_text()
        if char == "\x03":
            raise KeyboardInterrupt
        if char == "\t":
            matches = matched_commands(current_text())
            if matches:
                selected = matches[selected_index % len(matches)]
                buffer[:] = list(selected.name)
                selected_index = 0
                render()
            continue
        if char == "\x08":
            if buffer:
                buffer.pop()
                selected_index = 0
                render()
            continue
        if char in {"\x00", "\xe0"}:
            key = msvcrt.getwch()
            matches = matched_commands(current_text())
            if matches and key in {"H", "P"}:
                if key == "H":
                    selected_index = (selected_index - 1) % len(matches)
                else:
                    selected_index = (selected_index + 1) % len(matches)
                render()
            continue
        if char.isprintable():
            buffer.append(char)
            selected_index = 0
            render()


def print_help() -> None:
    lines = [f"{command.name:<10} {command.description}" for command in COMMANDS]
    print_block("Commands", "\n".join(lines))


def print_block(title: str, text: str) -> None:
    print()
    print(color(f"[{title}]", "36;1"))
    print(text.rstrip() if text.strip() else "(empty)")
    print()


def read_multiline() -> str:
    print("进入多行输入模式，单独一行 . 结束。")
    lines: list[str] = []
    while True:
        line = input("... ")
        if line == ".":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def save_reply_images(reply: AgentReply) -> list[Path]:
    saved_paths: list[Path] = []
    if not reply.images:
        return saved_paths

    TUI_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for index, image in enumerate(reply.images, 1):
        suffix = image.mime_type.removeprefix("image/") or "png"
        target = TUI_OUTPUT_DIR / f"generated_{timestamp}_{index}.{suffix}"
        target.write_bytes(image.data)
        saved_paths.append(target)
    return saved_paths


async def submit_prompt(prompt: str, bot: ConsoleBot) -> None:
    print(color("Agent 正在处理...", "33"))
    reply = await run_agent(
        prompt=prompt,
        bot=bot,
        chat_id=TUI_CHAT_ID,
        continue_session=True,
        record_text=f"[PowerShell TUI] {prompt}",
        session_scope=TUI_SESSION_SCOPE,
    )

    saved_images = save_reply_images(reply)
    if saved_images:
        print_block("Images", "\n".join(display_path(path) for path in saved_images))

    if reply.text.strip():
        print_block("Agent", reply.text)


async def run_tui() -> None:
    configure_stdio()
    print_header()
    bot = ConsoleBot()

    while True:
        try:
            raw = read_prompt()
        except EOFError:
            print()
            break

        prompt = raw.strip()
        if not prompt:
            continue

        command = prompt.lower()
        if command in {"/exit", "/quit", "exit", "quit"}:
            break
        if command == "/help":
            print_help()
            continue
        if command == "/provider":
            print(f"当前 Provider: {AGENT_PROVIDER}")
            continue
        if command == "/reset":
            clear_session_id(TUI_SESSION_SCOPE)
            print("TUI 连续会话已清空。")
            continue
        if command == "/paste":
            prompt = read_multiline()
            if not prompt:
                continue

        try:
            await submit_prompt(prompt, bot)
        except AgentServiceError as exc:
            print_block("Error", str(exc))
        except KeyboardInterrupt:
            print()
            break

    print("TUI 已退出。")


def main() -> None:
    try:
        asyncio.run(run_tui())
    except KeyboardInterrupt:
        print("\nTUI 已退出。")


if __name__ == "__main__":
    main()
