from __future__ import annotations

import sys
from dataclasses import dataclass
from contextlib import contextmanager
from shutil import get_terminal_size
from typing import Sequence


@dataclass(frozen=True, slots=True)
class SelectOption:
    value: str
    label: str
    description: str = ""


def is_interactive_terminal() -> bool:
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def _display_label(option: SelectOption) -> str:
    if option.description:
        return f"{option.label}  -  {option.description}"
    return option.label


@contextmanager
def _raw_terminal():
    if sys.platform == "win32":
        yield
        return

    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _read_key() -> str:
    if sys.platform == "win32":
        import msvcrt

        char = msvcrt.getwch()
        if char in {"\x00", "\xe0"}:
            extended = msvcrt.getwch()
            return {"H": "up", "P": "down"}.get(extended, "")
    else:
        import select

        char = sys.stdin.read(1)
        if char == "\x1b":
            sequence = ""
            while select.select([sys.stdin], [], [], 0.03)[0] and len(sequence) < 12:
                sequence += sys.stdin.read(1)
                if sequence.endswith(("~", "A", "B", "C", "D", "M")):
                    break
            if not sequence:
                return "escape"
            if sequence.endswith("A"):
                return "up"
            if sequence.endswith("B"):
                return "down"
            if sequence.endswith("M"):
                return "enter"
            return ""

    if char in {"\r", "\n"}:
        return "enter"
    if char == "\t":
        return "down"
    if char == "\x1b":
        return "escape"
    if char == "\x03":
        raise KeyboardInterrupt
    return char


def _fit_line(text: str) -> str:
    width = max(get_terminal_size((100, 24)).columns - 1, 40)
    return text if len(text) <= width else f"{text[: max(width - 3, 0)]}..."


def _render_menu(lines: list[str], *, previous_lines: int) -> int:
    if previous_lines:
        sys.stdout.write(f"\x1b[{previous_lines}A")
    for line in lines:
        sys.stdout.write("\r\x1b[2K" + _fit_line(line) + "\n")
    sys.stdout.flush()
    return len(lines)


def select_option(
    title: str,
    text: str,
    options: Sequence[SelectOption],
    *,
    default: str | None = None,
    cancel_value: str | None = None,
    error_value: str | None = None,
) -> str | None:
    if not options or not is_interactive_terminal():
        return None

    option_by_value = {option.value: option for option in options}
    selected_default = default if default in option_by_value else options[0].value

    selected = next((index for index, option in enumerate(options) if option.value == selected_default), 0)
    offset = 0
    visible_count = min(10, len(options))
    previous_lines = 0

    def build_lines() -> list[str]:
        nonlocal offset
        if selected < offset:
            offset = selected
        if selected >= offset + visible_count:
            offset = selected - visible_count + 1
        visible = options[offset : offset + visible_count]
        lines = [
            f"{title} - {text}",
            "[↑↓/Tab 选择，Enter 确认，Esc/Ctrl+C 取消]",
        ]
        for index, option in enumerate(visible, offset + 1):
            marker = "(*)" if index - 1 == selected else "( )"
            lines.append(f"{marker} {index}. {_display_label(option)}")
        if len(options) > visible_count:
            lines.append(f"显示 {offset + 1}-{offset + len(visible)} / {len(options)}")
        return lines

    try:
        with _raw_terminal():
            previous_lines = _render_menu(build_lines(), previous_lines=previous_lines)
            while True:
                key = _read_key()
                if key == "up":
                    selected = (selected - 1) % len(options)
                elif key == "down":
                    selected = (selected + 1) % len(options)
                elif key == "enter":
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    return options[selected].value
                elif key == "escape":
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    return cancel_value
                elif key.isdigit():
                    index = int(key)
                    if 1 <= index <= min(len(options), 9):
                        sys.stdout.write("\n")
                        sys.stdout.flush()
                        return options[index - 1].value
                previous_lines = _render_menu(build_lines(), previous_lines=previous_lines)
    except (EOFError, KeyboardInterrupt):
        sys.stdout.write("\n")
        sys.stdout.flush()
        return cancel_value
    except Exception:
        return error_value


def prompt_text(
    message: str,
    *,
    default: str = "",
    candidates: Sequence[str] = (),
) -> str | None:
    if not is_interactive_terminal():
        return None
    try:
        suffix = f" [当前/默认: {default}，回车保留]" if default else ""
        value = input(f"{message}{suffix}: ")
        return value.strip() or default
    except (EOFError, KeyboardInterrupt):
        return None
    except Exception:
        return None


def prompt_secret(message: str, *, default: str = "") -> str | None:
    if not is_interactive_terminal():
        return None
    suffix = " [已配置，回车保留；输入会显示 *]" if default else " [可跳过；输入会显示 *]"
    prompt = f"{message}{suffix}: "
    sys.stdout.write(prompt)
    sys.stdout.flush()
    chars: list[str] = []
    try:
        with _raw_terminal():
            while True:
                key = _read_key()
                if key == "enter":
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    return "".join(chars).strip() or default
                if key == "escape":
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    return None
                if key == "\b" or key == "\x7f":
                    if chars:
                        chars.pop()
                        sys.stdout.write("\b \b")
                        sys.stdout.flush()
                    continue
                if len(key) == 1 and key.isprintable():
                    chars.append(key)
                    sys.stdout.write("*")
                    sys.stdout.flush()
    except (EOFError, KeyboardInterrupt):
        sys.stdout.write("\n")
        sys.stdout.flush()
        return None
    except Exception:
        return None


def select_text_candidate(
    title: str,
    text: str,
    candidates: Sequence[str],
    *,
    current: str = "",
    allow_manual: bool = True,
    manual_label: str = "手动输入",
) -> str | None:
    seen: set[str] = set()
    options: list[SelectOption] = []

    if current:
        seen.add(current)
        options.append(SelectOption(current, f"保留当前: {current}"))

    for candidate in candidates:
        value = candidate.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        options.append(SelectOption(value, value))

    manual_value = "__manual__"
    if allow_manual:
        options.append(SelectOption(manual_value, manual_label, "列表里没有时选择这个"))

    selected = select_option(title, text, options, default=current or (options[0].value if options else None))
    if selected == manual_value:
        return prompt_text(manual_label, default=current, candidates=candidates)
    return selected
