from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
CHECK_SUFFIXES = {".py", ".md", ".toml", ".example", ".env"}
CHECK_NAMES = {".env", ".env.example", ".editorconfig", ".gitignore"}
SKIP_DIRS = {".git", ".venv", "__pycache__", "data", "workspace", "workspace_course"}

# 这些模式通常表示中文已经被错误代码页污染，或者被替换成了问号。
BAD_PATTERNS = [
    "????",
    "??",
    "锛",
    "涓",
    "歿",
    "俓",
    "�",
]


def should_check(path: Path) -> bool:
    if path.resolve() == SELF:
        return False
    if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
        return False
    return path.name in CHECK_NAMES or path.suffix in CHECK_SUFFIXES


def scan_file(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return [f"{path}: 不是合法 UTF-8：{exc}"]

    findings: list[str] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for pattern in BAD_PATTERNS:
            if pattern in line:
                findings.append(f"{path}:{line_number}: 发现疑似乱码 `{pattern}`：{line.strip()}")
                break
    return findings


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    findings: list[str] = []
    for path in ROOT.rglob("*"):
        if path.is_file() and should_check(path):
            findings.extend(scan_file(path))

    if findings:
        print("发现疑似编码问题：")
        for finding in findings:
            print(f"- {finding}")
        return 1

    print("文本编码检查通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
