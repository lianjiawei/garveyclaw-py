from __future__ import annotations

import os
import subprocess
import sys
import time
from collections import deque

from hiclaw.config import PROJECT_ROOT
from hiclaw.setup import (
    build_parser,
    load_env_values,
    run_config_get,
    run_config_set,
    run_doctor,
    run_channel_setup,
    run_model_add,
    run_model_list,
    run_model_use,
    run_setup,
    validate_env,
)


PID_FILE = PROJECT_ROOT / "data" / "hiclaw.pid"
LOG_FILE = PROJECT_ROOT / "data" / "hiclaw.log"


def _is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _run_shell_script(name: str) -> int:
    script_path = PROJECT_ROOT / "scripts" / name
    if not script_path.exists():
        print(f"找不到脚本：{script_path}")
        return 2
    if os.name == "nt":
        print(f"`hiclaw {name.removesuffix('.sh')}` 当前用于 Linux / macOS / WSL2 后台运行。")
        print("Windows 原生环境请使用前台模式：`hiclaw run`，或在 WSL2 中使用后台命令。")
        return 2
    env = os.environ.copy()
    env.setdefault("HICLAW_PYTHON", sys.executable)
    return subprocess.call(["bash", str(script_path)], cwd=str(PROJECT_ROOT), env=env)


def run_status() -> int:
    values = load_env_values()
    host = values.get("HICLAW_DASHBOARD_HOST") or "127.0.0.1"
    port = values.get("HICLAW_DASHBOARD_PORT") or "8765"
    display_host = "127.0.0.1" if host == "0.0.0.0" else host
    dashboard = f"http://{display_host}:{port}"

    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        except ValueError:
            print(f"HiClaw PID 文件格式异常：{PID_FILE}")
            return 1
        if _is_process_alive(pid):
            print(f"HiClaw is running. PID: {pid}")
            print(f"Log: {LOG_FILE}")
            print(f"Dashboard: {dashboard} | {dashboard}/v2 | {dashboard}/core")
            print("View logs: hiclaw logs")
            print("Stop: hiclaw stop")
            return 0
        print(f"HiClaw PID file exists but process is not running. PID: {pid}")
        print(f"Remove stale PID file or run: hiclaw stop")
        return 1
    print("HiClaw is not running in background mode.")
    print("Start background mode: hiclaw start")
    print("Run foreground mode: hiclaw run")
    return 1


def run_logs(lines: int = 80, follow: bool = False) -> int:
    if not LOG_FILE.exists():
        print(f"日志文件不存在：{LOG_FILE}")
        print("如果你还没有后台启动，请先运行：hiclaw start")
        return 1

    def print_tail() -> None:
        with LOG_FILE.open("r", encoding="utf-8", errors="replace") as file:
            for line in deque(file, maxlen=max(lines, 1)):
                print(line.rstrip("\n"))

    print_tail()
    if not follow:
        return 0

    print("")
    print("Following logs. Press Ctrl+C to stop.")
    try:
        with LOG_FILE.open("r", encoding="utf-8", errors="replace") as file:
            file.seek(0, os.SEEK_END)
            while True:
                line = file.readline()
                if line:
                    print(line.rstrip("\n"))
                else:
                    time.sleep(0.5)
    except KeyboardInterrupt:
        return 0


def _print_startup_preflight_error() -> None:
    issues = validate_env()
    if not any(issue.level == "error" for issue in issues):
        return
    print("HiClaw 启动前配置检查未通过：")
    for issue in issues:
        if issue.level != "error":
            continue
        print(f"- {issue.message}")
        if issue.hint:
            print(f"  修复建议: {issue.hint}")
    print("")
    print("你可以运行 `python -m hiclaw setup` 进入初始化向导，或运行 `python -m hiclaw doctor` 查看完整诊断。")
    raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv:
        _print_startup_preflight_error()
        from hiclaw.app import main as run_app

        run_app()
        return 0

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "setup":
        return run_setup(args)
    if args.command == "doctor":
        return run_doctor(args)
    if args.command == "config":
        if args.config_command == "set":
            return run_config_set(args.pairs)
        if args.config_command == "get":
            return run_config_get(args.keys, show_secrets=args.show_secrets)
        parser.error("config 需要子命令：set 或 get")
    if args.command == "model":
        if args.model_command in {None, "list"}:
            return run_model_list(args)
        if args.model_command == "add":
            return run_model_add(args)
        if args.model_command == "use":
            return run_model_use(args)
        parser.error("model 需要子命令：list、add 或 use")
    if args.command == "channel":
        if args.channel_command == "setup":
            return run_channel_setup(args)
        parser.error("channel 需要子命令：setup")
    if args.command == "run":
        _print_startup_preflight_error()
        from hiclaw.app import main as run_app

        run_app()
        return 0
    if args.command == "start":
        return _run_shell_script("start.sh")
    if args.command == "stop":
        return _run_shell_script("stop.sh")
    if args.command == "status":
        return run_status()
    if args.command == "logs":
        return run_logs(lines=args.lines, follow=args.follow)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
