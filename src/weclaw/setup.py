from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from weclaw.config import PROJECT_ROOT
from weclaw.core.model_discovery import discover_models
from weclaw.core.model_profiles import ModelProfile, get_active_model_profile, list_model_profiles, render_model_profiles, set_active_model_profile, update_profile_available_models, upsert_model_profile
from weclaw.core.provider_state import normalize_provider

ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE_FILE = PROJECT_ROOT / ".env.example"

PLACEHOLDER_MARKERS = (
    "your_",
    "your-",
    "_here",
    "example",
    "changeme",
    "replace_me",
)

DEFAULTS: dict[str, str] = {
    "AGENT_ROUTE": "openai",
    "AGENT_PROVIDER": "openai",
    "OPENAI_MODEL": "gpt-4o-mini",
    "WECLAW_DASHBOARD_HOST": "127.0.0.1",
    "WECLAW_DASHBOARD_PORT": "8765",
    "WORKSPACE_DIR": "./workspace",
    "SCHEDULER_INTERVAL_SECONDS": "5",
    "WECLAW_TUI_COLOR_MODE": "auto",
    "ASR_PROVIDER": "vosk",
    "ASR_MODELS_DIR": "./models/asr",
    "VOSK_MODEL_DIR": "./models/asr/vosk-model-small-cn-0.22",
    "SHOW_TOOL_TRACE": "0",
    "SESSION_TIMEOUT_SECONDS": "86400",
    "CAPABILITY_WATCHER_ENABLED": "1",
    "CAPABILITY_WATCHER_INTERVAL_SECONDS": "1.0",
    "AGENT_CLUSTER_ENABLED": "0",
    "AGENT_CLUSTER_REVIEW_ENABLED": "1",
    "AGENT_CLUSTER_ORCHESTRATOR_ENABLED": "0",
    "AGENT_CLUSTER_DYNAMIC_PLANNER_ENABLED": "0",
    "AGENT_CLUSTER_MAX_EVENTS": "40",
    "TAVILY_SEARCH_DEPTH": "basic",
    "TAVILY_MAX_RESULTS": "5",
    "TELEGRAM_CONNECT_TIMEOUT": "30",
    "TELEGRAM_READ_TIMEOUT": "30",
    "TELEGRAM_WRITE_TIMEOUT": "30",
    "TELEGRAM_POOL_TIMEOUT": "30",
    "TELEGRAM_POLLING_TIMEOUT": "30",
    "TELEGRAM_BOOTSTRAP_RETRIES": "5",
    "TELEGRAM_RESTART_DELAY_SECONDS": "10",
    "TELEGRAM_API_RETRIES": "2",
    "TELEGRAM_API_RETRY_DELAY_SECONDS": "1.5",
    "FEISHU_SESSION_SCOPE_PREFIX": "feishu",
    "FEISHU_REPLY_PROCESSING_MESSAGE": "1",
    "FEISHU_RESTART_DELAY_SECONDS": "10",
    "FEISHU_API_RETRIES": "2",
    "FEISHU_API_RETRY_DELAY_SECONDS": "1.5",
    "WEIXIN_BASE_URL": "https://ilinkai.weixin.qq.com",
    "WEIXIN_CDN_BASE_URL": "https://novac2c.cdn.weixin.qq.com/c2c",
    "WEIXIN_DM_POLICY": "open",
    "WEIXIN_GROUP_POLICY": "disabled",
    "WEIXIN_LONG_POLL_TIMEOUT_MS": "35000",
    "WEIXIN_RESTART_DELAY_SECONDS": "10",
    "WEIXIN_SEND_CHUNK_DELAY_SECONDS": "1.5",
    "WEIXIN_SEND_CHUNK_RETRIES": "4",
    "WEIXIN_SEND_CHUNK_RETRY_DELAY_SECONDS": "1.0",
}


@dataclass(frozen=True, slots=True)
class ConfigIssue:
    level: str
    code: str
    message: str
    hint: str = ""


def _is_windows() -> bool:
    return os.name == "nt"


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    if not key:
        return None
    return key, _unquote(value.strip())


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _quote(value: str) -> str:
    if value == "":
        return ""
    if any(char.isspace() for char in value) or "#" in value:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _resolve_config_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def load_env_values(path: Path | None = None) -> dict[str, str]:
    path = path or ENV_FILE
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(line)
        if parsed is not None:
            key, value = parsed
            values[key] = value
    return values


def ensure_env_file(copy_example: bool = True, path: Path | None = None, example_path: Path | None = None) -> bool:
    path = path or ENV_FILE
    example_path = example_path or ENV_EXAMPLE_FILE
    if path.exists():
        return False
    if copy_example and example_path.exists():
        shutil.copyfile(example_path, path)
    else:
        path.write_text("", encoding="utf-8")
    return True


def set_env_values(updates: dict[str, str], path: Path | None = None) -> None:
    path = path or ENV_FILE
    ensure_env_file(path=path)
    lines = path.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    updated_lines: list[str] = []

    for line in lines:
        parsed = _parse_env_line(line)
        if parsed is None:
            updated_lines.append(line)
            continue
        key, _value = parsed
        if key in updates:
            updated_lines.append(f"{key}={_quote(updates[key])}")
            seen.add(key)
        else:
            updated_lines.append(line)

    missing = [key for key in updates if key not in seen]
    if missing and updated_lines and updated_lines[-1].strip():
        updated_lines.append("")
    for key in missing:
        updated_lines.append(f"{key}={_quote(updates[key])}")

    path.write_text("\n".join(updated_lines).rstrip() + "\n", encoding="utf-8")


def _value(values: dict[str, str], key: str) -> str:
    return values.get(key, os.getenv(key, "")).strip()


def _has_value(values: dict[str, str], key: str) -> bool:
    value = _value(values, key)
    if not value:
        return False
    lowered = value.lower()
    return not any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    return bool(lowered) and any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def _parse_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def validate_env(values: dict[str, str] | None = None, *, require_channel: bool = True) -> list[ConfigIssue]:
    values = values or load_env_values()
    issues: list[ConfigIssue] = []

    for cert_var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        cert_path = os.environ.get(cert_var, "").strip()
        if cert_path and not Path(cert_path).exists():
            issues.append(
                ConfigIssue(
                    "error",
                    "missing_ca_bundle",
                    f"{cert_var} 指向的证书文件不存在：{cert_path}",
                    f"删除这个环境变量，或把 {cert_var} 改成系统可用的 CA bundle，例如 /etc/ssl/certs/ca-certificates.crt。",
                )
            )

    if not ENV_FILE.exists():
        issues.append(
            ConfigIssue(
                "error",
                "missing_env",
                f"未找到配置文件：{ENV_FILE}",
                "运行 `weclaw setup` 生成并填写 .env。",
            )
        )
        return issues

    provider = normalize_provider(_value(values, "AGENT_ROUTE") or _value(values, "AGENT_PROVIDER") or DEFAULTS["AGENT_PROVIDER"], default=DEFAULTS["AGENT_PROVIDER"])
    active_profile = get_active_model_profile()
    if active_profile.api_key or active_profile.base_url or active_profile.model:
        provider = active_profile.protocol
    if provider not in {"claude", "openai"}:
        issues.append(
            ConfigIssue(
                "error",
                "invalid_provider",
                "AGENT_ROUTE 无效。可用值：openai/openai_compatible/claude/anthropic_compatible。",
                "运行 `weclaw config set AGENT_ROUTE=openai`。",
            )
        )
    provider_key_ready = bool(active_profile.api_key) and active_profile.protocol == provider
    provider_model_ready = bool(active_profile.model) and active_profile.protocol == provider
    if provider == "openai" and not (_has_value(values, "OPENAI_API_KEY") or provider_key_ready):
        issues.append(
            ConfigIssue(
                "warning",
                "missing_openai_key",
                "当前模型 Provider 是 OpenAI-compatible，但 API Key 未配置；模型对话暂不可用。",
                "运行 `weclaw setup`，或用 `weclaw model add --protocol openai ...` 添加。",
            )
        )
    elif provider == "claude" and not (_has_value(values, "ANTHROPIC_API_KEY") or provider_key_ready):
        issues.append(
            ConfigIssue(
                "warning",
                "missing_claude_key",
                "当前模型 Provider 是 Anthropic-compatible，但 API Key 未配置；模型对话暂不可用。",
                "运行 `weclaw setup`，或用 `weclaw model add --protocol claude ...` 添加。",
            )
        )
    if provider == "openai" and not (_has_value(values, "OPENAI_MODEL") or provider_model_ready):
        issues.append(ConfigIssue("warning", "missing_openai_model", "OPENAI_MODEL 为空，将由服务端默认模型决定。", "建议显式填写 OPENAI_MODEL，降低兼容服务商行为差异。"))
    if provider == "claude" and not (_has_value(values, "ANTHROPIC_MODEL") or provider_model_ready):
        issues.append(ConfigIssue("warning", "missing_anthropic_model", "ANTHROPIC_MODEL 为空，将由服务端默认模型决定。", "建议显式填写 ANTHROPIC_MODEL，便于复现与排障。"))

    openai_base_url = _value(values, "OPENAI_BASE_URL")
    if openai_base_url and _looks_like_placeholder(openai_base_url):
        issues.append(ConfigIssue("warning", "placeholder_openai_base_url", "OPENAI_BASE_URL 仍是模板占位值。", "不用代理时请留空；使用兼容服务商时填写真实 /v1 地址。"))
    anthropic_base_url = _value(values, "ANTHROPIC_BASE_URL")
    if anthropic_base_url and _looks_like_placeholder(anthropic_base_url):
        issues.append(ConfigIssue("warning", "placeholder_anthropic_base_url", "ANTHROPIC_BASE_URL 仍是模板占位值。", "不用代理时请留空；使用兼容服务商时填写真实地址。"))
    image_base_url = _value(values, "OPENAI_IMAGE_BASE_URL")
    if image_base_url and _looks_like_placeholder(image_base_url):
        issues.append(ConfigIssue("warning", "placeholder_image_base_url", "OPENAI_IMAGE_BASE_URL 仍是模板占位值，图片工具会不可用。", "不用图片工具时请留空；需要图片能力时填写真实地址。"))

    telegram_ready = _has_value(values, "TELEGRAM_BOT_TOKEN") and _has_value(values, "OWNER_ID")
    feishu_ready = _has_value(values, "FEISHU_APP_ID") and _has_value(values, "FEISHU_APP_SECRET")
    weixin_ready = _has_value(values, "WEIXIN_ACCOUNT_ID") and _has_value(values, "WEIXIN_TOKEN")
    if require_channel and not telegram_ready and not feishu_ready and not weixin_ready:
        issues.append(
            ConfigIssue(
                "warning",
                "missing_channel",
                "未配置 Telegram / Feishu 消息通道；主程序会以 dashboard-only 模式启动。",
                "想本地聊天请运行 `weclaw-tui`；想接入机器人再配置 Telegram 或 Feishu。",
            )
        )

    owner_id = _value(values, "OWNER_ID")
    if owner_id and "your_" not in owner_id.lower() and not owner_id.isdigit():
        level = "error" if _has_value(values, "TELEGRAM_BOT_TOKEN") else "warning"
        issues.append(ConfigIssue(level, "invalid_owner_id", "OWNER_ID 应该是纯数字 Telegram user id。", "可以向 Telegram 的 @userinfobot 查询自己的 user id。"))

    port = _parse_int(_value(values, "WECLAW_DASHBOARD_PORT") or DEFAULTS["WECLAW_DASHBOARD_PORT"])
    if port <= 0 or port > 65535:
        issues.append(ConfigIssue("error", "invalid_dashboard_port", "WECLAW_DASHBOARD_PORT 不是有效端口。", "请设置为 1-65535 之间的端口，例如 8765。"))

    asr_provider = (_value(values, "ASR_PROVIDER") or DEFAULTS["ASR_PROVIDER"]).lower()
    if asr_provider not in {"none", "vosk"}:
        issues.append(ConfigIssue("error", "invalid_asr_provider", "ASR_PROVIDER 只能是 none 或 vosk。", "语音识别暂不用时设置 ASR_PROVIDER=none。"))
    if asr_provider == "vosk":
        model_dir = _value(values, "VOSK_MODEL_DIR") or DEFAULTS["VOSK_MODEL_DIR"]
        if not model_dir:
            issues.append(ConfigIssue("error", "missing_vosk_model", "ASR_PROVIDER=vosk 但 VOSK_MODEL_DIR 未配置。", "设置 VOSK_MODEL_DIR 为本地 Vosk 模型目录，或改为 ASR_PROVIDER=none。"))
        elif not _resolve_config_path(model_dir).exists():
            issues.append(
                ConfigIssue(
                    "error",
                    "missing_vosk_path",
                    f"VOSK_MODEL_DIR 不存在：{model_dir}",
                    "默认目录是 ./models/asr/vosk-model-small-cn-0.22。请把 vosk-model-small-cn-0.22 解压到该目录，或改为 ASR_PROVIDER=none。",
                )
            )
        if shutil.which("ffmpeg") is None:
            issues.append(ConfigIssue("warning", "missing_ffmpeg", "未检测到 ffmpeg；语音消息转写需要 ffmpeg。", "一键安装脚本会尝试安装 ffmpeg；手动部署请运行 `sudo apt install -y ffmpeg`。"))

    tavily = _value(values, "TAVILY_API_KEY")
    if not tavily:
        issues.append(
            ConfigIssue(
                "warning",
                "missing_tavily_key",
                "TAVILY_API_KEY 未配置；web_search 会回退到系统默认轻量搜索。",
                "默认搜索能力可能在质量、时效性和访问频率上受限制；需要更稳定效果时再配置 TAVILY_API_KEY。",
            )
        )
    elif any(marker in tavily.lower() for marker in PLACEHOLDER_MARKERS):
        issues.append(
            ConfigIssue(
                "warning",
                "placeholder_tavily",
                "TAVILY_API_KEY 仍是模板占位值；web_search 会回退到系统默认轻量搜索。",
                "不需要 Tavily 增强搜索可以留空；需要更稳定效果时填写真实 Tavily API Key。",
            )
        )

    return issues


def print_doctor_report(issues: list[ConfigIssue], *, quiet: bool = False) -> None:
    if quiet:
        return
    print("WeClaw 配置检查")
    print(f"- 项目目录: {PROJECT_ROOT}")
    print(f"- 配置文件: {ENV_FILE}")
    if not issues:
        print("状态: 通过，可以启动。")
        return
    print("状态: 发现配置问题")
    for issue in issues:
        prefix = "ERROR" if issue.level == "error" else "WARN"
        print(f"- [{prefix}] {issue.message}")
        if issue.hint:
            print(f"  修复建议: {issue.hint}")


class _DoctorMessageSender:
    async def send_text(self, target_id: str, text: str) -> None:
        return None

    async def send_file(self, target_id: str, file_data: bytes, file_name: str) -> None:
        return None


async def _run_provider_check() -> tuple[bool, str]:
    from weclaw.agents.router import run_agent
    from weclaw.core.provider_state import get_provider

    provider = get_provider()
    reply = await run_agent(
        prompt="请只回复 WECLAW_OK，用于检查当前模型链路是否可用。",
        sender=_DoctorMessageSender(),
        target_id="doctor",
        continue_session=False,
        record_text="[doctor] provider check",
        session_scope="doctor:provider-check",
        channel="doctor",
    )
    text = reply.text.strip()
    if not text:
        return False, f"{provider}: 模型返回为空。"
    return True, f"{provider}: 模型链路可用，回复预览：{text[:80]}"


def run_provider_check() -> int:
    print("")
    print("Provider 链路检查")
    try:
        ok, message = asyncio.run(_run_provider_check())
    except Exception as exc:
        print(f"- [ERROR] 模型链路调用失败：{exc}")
        return 1
    prefix = "OK" if ok else "ERROR"
    print(f"- [{prefix}] {message}")
    return 0 if ok else 1


def _masked_input(prompt: str) -> str:
    if not sys.stdin.isatty():
        import getpass

        return getpass.getpass(prompt)

    def finish_line() -> None:
        # Raw terminal mode does not translate "\n" to carriage-return + newline.
        # Use CRLF explicitly so the next prompt starts at column 0 after paste.
        sys.stdout.write("\r\n")
        sys.stdout.flush()

    if _is_windows():
        import msvcrt

        sys.stdout.write(prompt)
        sys.stdout.flush()
        chars: list[str] = []
        while True:
            char = msvcrt.getwch()
            if char in {"\r", "\n"}:
                finish_line()
                return "".join(chars)
            if char == "\x03":
                finish_line()
                raise KeyboardInterrupt
            if char == "\x1a":
                finish_line()
                raise EOFError
            if char in {"\b", "\x7f"}:
                if chars:
                    chars.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue
            chars.append(char)
            sys.stdout.write("*")
            sys.stdout.flush()

    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    sys.stdout.write(prompt)
    sys.stdout.flush()
    chars: list[str] = []
    try:
        tty.setraw(fd)
        while True:
            char = sys.stdin.read(1)
            if char in {"\r", "\n"}:
                finish_line()
                return "".join(chars)
            if char == "\x03":
                finish_line()
                raise KeyboardInterrupt
            if char == "\x04":
                finish_line()
                raise EOFError
            if char in {"\b", "\x7f"}:
                if chars:
                    chars.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue
            chars.append(char)
            sys.stdout.write("*")
            sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _prompt(label: str, default: str = "", *, secret: bool = False) -> str:
    if secret:
        suffix = " [已配置，回车跳过/保留；输入会显示 *]" if default else " [可跳过，直接回车；输入会显示 *]"
        value = _masked_input(f"{label}{suffix}: ").strip()
    else:
        suffix = f" [当前/默认: {default}，回车跳过/保留]" if default else " [可跳过，直接回车]"
        value = input(f"{label}{suffix}: ").strip()
    return value or default


def _choose(label: str, options: list[tuple[str, str]], default: str) -> str:
    normalized_default = default.strip().lower()
    default_index = 1
    for index, (value, description) in enumerate(options, 1):
        if value == normalized_default:
            default_index = index

    print(label)
    for index, (value, description) in enumerate(options, 1):
        marker = " *" if value == normalized_default else ""
        print(f"  {index}. {description}{marker}")
    answer = input(f"请选择编号，或直接回车跳过/保留当前选项 [{default_index}]: ").strip().lower()
    if not answer:
        return options[default_index - 1][0]
    if answer.isdigit():
        index = int(answer)
        if 1 <= index <= len(options):
            return options[index - 1][0]
    for value, _description in options:
        if answer == value:
            return value
    print(f"无法识别选择，已使用默认项：{options[default_index - 1][1]}")
    return options[default_index - 1][0]


def _yes_no(label: str, default: bool = False) -> bool:
    marker = "Y/n" if default else "y/N"
    value = input(f"{label} [{marker}，回车跳过/保留默认]: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "1", "true", "是"}


def _run_weixin_qr_login(args: argparse.Namespace) -> dict[str, str] | None:
    from weclaw.channels.weixin.bot import qr_login

    print("Opening browser QR login for Weixin...")
    return asyncio.run(
        qr_login(
            bot_type=getattr(args, "weixin_bot_type", "3"),
            timeout_seconds=getattr(args, "weixin_login_timeout", 480),
            write_env=False,
            open_browser=not getattr(args, "weixin_no_open_browser", False),
        )
    )


def _default_channel(values: dict[str, str]) -> str:
    if _has_value(values, "TELEGRAM_BOT_TOKEN"):
        return "telegram"
    if _has_value(values, "FEISHU_APP_ID") or _has_value(values, "FEISHU_APP_SECRET"):
        return "feishu"
    if _has_value(values, "WEIXIN_ACCOUNT_ID") or _has_value(values, "WEIXIN_TOKEN"):
        return "weixin"
    return "tui"


def _default_profile_for_protocol(protocol: str) -> ModelProfile | None:
    active = get_active_model_profile()
    if active.protocol == protocol:
        return active

    profiles = [profile for profile in list_model_profiles() if profile.protocol == protocol]
    non_default = [profile for profile in profiles if not profile.id.endswith("-default")]
    if non_default:
        return non_default[0]
    return profiles[0] if profiles else None


def _select_discovered_model(current_model: str, models: list[str], *, non_interactive: bool) -> str:
    if not models or non_interactive:
        return current_model
    print("")
    print("检测到服务商可用模型：")
    for index, model_id in enumerate(models[:30], 1):
        marker = " (当前)" if model_id == current_model else ""
        print(f"{index}. {model_id}{marker}")
    if len(models) > 30:
        print(f"... 还有 {len(models) - 30} 个模型未显示，可后续用 `weclaw model list` 查看缓存。")
    choice = _prompt("选择模型序号，或直接输入 model id；回车保留当前值", current_model)
    if choice.isdigit():
        index = int(choice)
        if 1 <= index <= min(len(models), 30):
            return models[index - 1]
    return choice or current_model


def _discover_models_for_setup(profile: ModelProfile, *, non_interactive: bool) -> list[str]:
    if not profile.api_key:
        return []
    print("正在尝试检测可用模型列表...")
    result = asyncio.run(discover_models(profile))
    if result.models:
        print(f"已从 {result.endpoint} 检测到 {len(result.models)} 个模型。")
        return result.models
    if not non_interactive:
        print(f"未能自动检测模型列表，将保留手动输入。原因：{result.error}")
    return []


def run_setup(args: argparse.Namespace) -> int:
    _configure_stdio()
    created = ensure_env_file()
    values = load_env_values()
    updates: dict[str, str] = {}

    print("WeClaw 初始化向导")
    print(f"- 配置文件: {ENV_FILE}")
    if created:
        print("- 已根据 .env.example 创建 .env")
    else:
        print("- 检测到已有 .env；已有值或默认值可直接回车跳过/保留")
    print("")
    print("第一步：配置模型 Provider")
    print("这里选择的是接口协议，不是只能用官方模型。")

    provider_default = args.provider or _value(values, "AGENT_ROUTE") or _value(values, "AGENT_PROVIDER") or DEFAULTS["AGENT_ROUTE"]
    provider = normalize_provider(provider_default, default=DEFAULTS["AGENT_PROVIDER"])
    if provider not in {"claude", "openai"}:
        provider = normalize_provider(DEFAULTS["AGENT_PROVIDER"], default="openai")
    if not args.non_interactive:
        provider = _choose(
            "请选择你手里的 API 属于哪一类（已有配置可直接回车跳过）：",
            [
                ("openai", "OpenAI 兼容接口，例如 OpenAI、DeepSeek、通义千问、硅基流动、OpenRouter"),
                ("claude", "Anthropic / Claude 兼容接口，例如 Anthropic 官方或 Claude 兼容网关"),
            ],
            provider,
        )
    updates["AGENT_PROVIDER"] = provider
    updates["AGENT_ROUTE"] = provider
    existing_profile = _default_profile_for_protocol(provider)

    if provider == "openai":
        profile_name = getattr(args, "profile_name", None) or (existing_profile.id if existing_profile else "") or _value(values, "MODEL_PROFILE_NAME") or "openai-default"
        if not args.non_interactive:
            profile_name = _prompt("给这个模型服务起个名字，例如 deepseek、qwen、openai-main", profile_name)
        key = args.openai_api_key or (existing_profile.api_key if existing_profile else "") or _value(values, "OPENAI_API_KEY")
        if not args.non_interactive:
            key = _prompt("OPENAI_API_KEY", key, secret=True)
        if key:
            updates["OPENAI_API_KEY"] = key
        base_url = args.openai_base_url if args.openai_base_url is not None else ((existing_profile.base_url if existing_profile else "") or _value(values, "OPENAI_BASE_URL"))
        if not args.non_interactive:
            base_url = _prompt("OPENAI_BASE_URL（官方 OpenAI 可留空；第三方兼容服务通常填写 /v1 地址）", base_url)
        updates["OPENAI_BASE_URL"] = base_url
        model = args.openai_model or (existing_profile.model if existing_profile else "") or _value(values, "OPENAI_MODEL") or DEFAULTS["OPENAI_MODEL"]
        available_models: list[str] = []
        if not getattr(args, "skip_model_discovery", False):
            available_models = _discover_models_for_setup(
                ModelProfile(
                    id=profile_name.strip() or "openai-default",
                    name=profile_name.strip() or "OpenAI compatible",
                    protocol="openai",
                    api_key=key or "",
                    base_url=base_url or "",
                    model=model or "",
                ),
                non_interactive=args.non_interactive,
            )
            model = _select_discovered_model(model, available_models, non_interactive=args.non_interactive)
        if not args.non_interactive:
            model = _prompt("模型名（填写服务商给你的 model id，例如 gpt-4o-mini、deepseek-chat、qwen-plus）", model)
        updates["OPENAI_MODEL"] = model
        profile = upsert_model_profile(
            ModelProfile(
                id=profile_name.strip() or "openai-default",
                name=profile_name.strip() or "OpenAI compatible",
                protocol="openai",
                api_key=key or "",
                base_url=base_url or "",
                model=model or "",
                available_models=tuple(available_models),
            )
        )
        updates["MODEL_PROFILE_NAME"] = profile.id
    else:
        profile_name = getattr(args, "profile_name", None) or (existing_profile.id if existing_profile else "") or _value(values, "MODEL_PROFILE_NAME") or "claude-default"
        if not args.non_interactive:
            profile_name = _prompt("给这个模型服务起个名字，例如 claude、qwen-anthropic、my-gateway", profile_name)
        key = args.anthropic_api_key or (existing_profile.api_key if existing_profile else "") or _value(values, "ANTHROPIC_API_KEY")
        if not args.non_interactive:
            key = _prompt("ANTHROPIC_API_KEY", key, secret=True)
        if key:
            updates["ANTHROPIC_API_KEY"] = key
        base_url = (
            args.anthropic_base_url
            if args.anthropic_base_url is not None
            else ((existing_profile.base_url if existing_profile else "") or _value(values, "ANTHROPIC_BASE_URL"))
        )
        if not args.non_interactive:
            base_url = _prompt("ANTHROPIC_BASE_URL（官方 Anthropic 可留空；第三方兼容网关请填写）", base_url)
        updates["ANTHROPIC_BASE_URL"] = base_url
        model = args.anthropic_model or (existing_profile.model if existing_profile else "") or _value(values, "ANTHROPIC_MODEL")
        available_models = []
        if not getattr(args, "skip_model_discovery", False):
            available_models = _discover_models_for_setup(
                ModelProfile(
                    id=profile_name.strip() or "claude-default",
                    name=profile_name.strip() or "Anthropic compatible",
                    protocol="claude",
                    api_key=key or "",
                    base_url=base_url or "",
                    model=model or "",
                ),
                non_interactive=args.non_interactive,
            )
            model = _select_discovered_model(model, available_models, non_interactive=args.non_interactive)
        if not args.non_interactive:
            model = _prompt("模型名（填写服务商给你的 model id）", model)
        updates["ANTHROPIC_MODEL"] = model
        profile = upsert_model_profile(
            ModelProfile(
                id=profile_name.strip() or "claude-default",
                name=profile_name.strip() or "Anthropic compatible",
                protocol="claude",
                api_key=key or "",
                base_url=base_url or "",
                model=model or "",
                available_models=tuple(available_models),
            )
        )
        updates["MODEL_PROFILE_NAME"] = profile.id

    channel = args.channel
    print("")
    print("第二步：选择使用入口")
    if not channel and not args.non_interactive:
        channel_default = _default_channel(values)
        channel = _choose(
            "请选择使用入口（已有配置可直接回车跳过）：",
            [
                ("tui", "本地 TUI（推荐先用这个测试模型）"),
                ("telegram", "Telegram Bot"),
                ("weixin", "Weixin personal account"),
                ("feishu", "Feishu 机器人"),
                ("none", "暂不配置入口，只启动 dashboard"),
            ],
            channel_default,
        )
    channel = (channel or "none").lower()
    if channel == "tui":
        channel = "none"
    if channel == "telegram":
        token = args.telegram_bot_token or _value(values, "TELEGRAM_BOT_TOKEN")
        owner = args.owner_id or _value(values, "OWNER_ID")
        if not args.non_interactive:
            token = _prompt("TELEGRAM_BOT_TOKEN", "" if not _has_value(values, "TELEGRAM_BOT_TOKEN") else token, secret=True)
            owner = _prompt("OWNER_ID (Telegram user id)", "" if not _has_value(values, "OWNER_ID") else owner)
        if token:
            updates["TELEGRAM_BOT_TOKEN"] = token
        if owner:
            updates["OWNER_ID"] = owner
    elif channel == "feishu":
        app_id = args.feishu_app_id or _value(values, "FEISHU_APP_ID")
        app_secret = args.feishu_app_secret or _value(values, "FEISHU_APP_SECRET")
        if not args.non_interactive:
            app_id = _prompt("FEISHU_APP_ID", "" if not _has_value(values, "FEISHU_APP_ID") else app_id)
            app_secret = _prompt("FEISHU_APP_SECRET", "" if not _has_value(values, "FEISHU_APP_SECRET") else app_secret, secret=True)
        if app_id:
            updates["FEISHU_APP_ID"] = app_id
        if app_secret:
            updates["FEISHU_APP_SECRET"] = app_secret
    elif channel == "weixin":
        if args.non_interactive:
            account_id = args.weixin_account_id or _value(values, "WEIXIN_ACCOUNT_ID")
            token = args.weixin_token or _value(values, "WEIXIN_TOKEN")
            if account_id:
                updates["WEIXIN_ACCOUNT_ID"] = account_id
            if token:
                updates["WEIXIN_TOKEN"] = token
        else:
            credentials = _run_weixin_qr_login(args)
            if credentials:
                updates["WEIXIN_ACCOUNT_ID"] = credentials["account_id"]
                updates["WEIXIN_TOKEN"] = credentials["token"]
                updates["WEIXIN_BASE_URL"] = credentials["base_url"]
    elif channel == "none":
        for key in ("TELEGRAM_BOT_TOKEN", "OWNER_ID", "FEISHU_APP_ID", "FEISHU_APP_SECRET", "WEIXIN_ACCOUNT_ID", "WEIXIN_TOKEN"):
            if not _has_value(values, key):
                updates[key] = ""

    tavily_key = args.tavily_api_key or _value(values, "TAVILY_API_KEY")
    print("")
    print("第三步：高级配置（都可以先跳过）")
    if not args.non_interactive:
        tavily_key = _prompt(
            "TAVILY_API_KEY（可选；跳过会使用系统默认轻量搜索，能力可能有限）",
            "" if not _has_value(values, "TAVILY_API_KEY") else tavily_key,
            secret=True,
        )
        if not tavily_key:
            print("已跳过 Tavily：联网搜索会回退到系统默认轻量搜索，结果质量和稳定性可能有限。")
    if tavily_key:
        updates["TAVILY_API_KEY"] = tavily_key

    dashboard_host = args.dashboard_host or _value(values, "WECLAW_DASHBOARD_HOST") or DEFAULTS["WECLAW_DASHBOARD_HOST"]
    if not args.non_interactive:
        if _has_value(values, "WECLAW_DASHBOARD_HOST"):
            default_host = dashboard_host
        else:
            default_host = "127.0.0.1" if _is_windows() else dashboard_host
            if _yes_no("Dashboard 是否允许公网/局域网访问", default=False):
                default_host = "0.0.0.0"
        dashboard_host = _prompt("WECLAW_DASHBOARD_HOST", default_host)
    updates["WECLAW_DASHBOARD_HOST"] = dashboard_host
    updates["WECLAW_DASHBOARD_PORT"] = str(args.dashboard_port or _value(values, "WECLAW_DASHBOARD_PORT") or DEFAULTS["WECLAW_DASHBOARD_PORT"])

    for key, value in DEFAULTS.items():
        existing = _value(values, key)
        updates.setdefault(key, existing if existing and _has_value(values, key) else value)

    set_env_values(updates)
    issues = validate_env(load_env_values(), require_channel=channel != "none")
    print("")
    print_doctor_report(issues)
    print("")
    print("常用命令:")
    print("- 前台启动: weclaw run")
    print("- 后台启动: weclaw start")
    print("- 停止后台: weclaw stop")
    print("- 本地 TUI: weclaw-tui")
    print("- 检查配置: weclaw doctor")
    return 1 if any(issue.level == "error" for issue in issues) else 0


def run_doctor(args: argparse.Namespace) -> int:
    _configure_stdio()
    issues = validate_env(require_channel=not args.tui_only)
    print_doctor_report(issues, quiet=args.quiet)
    status = 1 if any(issue.level == "error" for issue in issues) else 0
    if getattr(args, "provider_check", False):
        provider_status = run_provider_check()
        status = max(status, provider_status)
    return status


def run_config_set(pairs: Iterable[str]) -> int:
    _configure_stdio()
    updates: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            print(f"无效配置项：{pair}，请使用 KEY=VALUE 格式。")
            return 2
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key:
            print(f"无效配置项：{pair}")
            return 2
        updates[key] = value.strip()
    if not updates:
        print("请提供至少一个 KEY=VALUE。")
        return 2
    ensure_env_file()
    set_env_values(updates)
    print("已更新 .env:")
    for key in updates:
        print(f"- {key}")
    return 0


def _is_secret_key(key: str) -> bool:
    upper = key.upper()
    return any(marker in upper for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD"))


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "******"
    return f"{value[:4]}...{value[-4:]}"


def run_config_get(keys: Iterable[str], *, show_secrets: bool = False) -> int:
    _configure_stdio()
    values = load_env_values()
    selected = list(keys) or sorted(values)
    for key in selected:
        value = values.get(key, "")
        if not show_secrets and _is_secret_key(key):
            value = _mask_secret(value)
        print(f"{key}={value}")
    return 0


def run_model_list(args: argparse.Namespace) -> int:
    _configure_stdio()
    print(render_model_profiles())
    return 0


def run_model_refresh(args: argparse.Namespace) -> int:
    _configure_stdio()
    profile_id = args.profile or get_active_model_profile().id
    profiles = {profile.id: profile for profile in list_model_profiles()}
    if profile_id not in profiles:
        print(f"Unknown model profile: {profile_id}")
        return 2
    profile = profiles[profile_id]
    print(f"正在刷新模型列表: {profile.id}")
    result = asyncio.run(discover_models(profile))
    if not result.models:
        print(f"未能自动检测模型列表：{result.error}")
        return 1
    updated = update_profile_available_models(profile.id, result.models)
    print(f"已检测到 {len(updated.available_models)} 个模型。")
    print(f"- 来源: {result.endpoint}")
    print(f"- 当前模型: {updated.model or '(empty)'}")
    print("- 可选模型:")
    for model in updated.available_models[:50]:
        marker = " *" if model == updated.model else ""
        print(f"  - {model}{marker}")
    if len(updated.available_models) > 50:
        print(f"  ... 还有 {len(updated.available_models) - 50} 个")
    return 0


def run_model_add(args: argparse.Namespace) -> int:
    _configure_stdio()
    ensure_env_file()
    interactive = not all((args.protocol, args.name, args.api_key, args.model))
    if interactive:
        protocol = normalize_provider(
            _choose(
                "请选择接口协议",
                [("openai", "OpenAI-compatible"), ("claude", "Anthropic / Claude-compatible")],
                normalize_provider(args.protocol, default="openai"),
            ),
            default="openai",
        )
        name = _prompt("Provider 名称，也就是 profile_id，例如 bailian、deepseek、alibaba", args.name or ("openai-main" if protocol == "openai" else "claude-main"))
        api_key = args.api_key or _prompt("API Key", "", secret=True)
        if protocol == "openai":
            base_url = _prompt("Base URL（官方 OpenAI 可留空；第三方通常填写 /v1 地址）", args.base_url or "")
            model = args.model or DEFAULTS["OPENAI_MODEL"]
        else:
            base_url = _prompt("Base URL（官方 Anthropic 可留空；第三方 Claude-compatible 网关请填写）", args.base_url or "")
            model = args.model or ""
    else:
        protocol = normalize_provider(args.protocol, default="openai")
        name = args.name
        api_key = args.api_key
        base_url = args.base_url or ""
        model = args.model or ""
    available_models: list[str] = []
    if not getattr(args, "skip_model_discovery", False):
        available_models = _discover_models_for_setup(
            ModelProfile(
                id=name,
                name=name,
                protocol=protocol,
                api_key=api_key or "",
                base_url=base_url or "",
                model=model or "",
            ),
            non_interactive=not interactive,
        )
        model = _select_discovered_model(model, available_models, non_interactive=not interactive)
    if interactive:
        model = _prompt("模型名（可直接回车使用上面选择的模型，或手动输入）", model)
    profile = upsert_model_profile(
        ModelProfile(
            id=name,
            name=name,
            protocol=protocol,
            api_key=api_key or "",
            base_url=base_url or "",
            model=model or "",
            available_models=tuple(available_models),
        ),
        activate=not args.no_activate,
    )
    if not args.no_activate:
        updates = {"MODEL_PROFILE_NAME": profile.id, "AGENT_PROVIDER": profile.protocol, "AGENT_ROUTE": profile.protocol}
        if profile.protocol == "openai":
            updates.update({"OPENAI_API_KEY": profile.api_key, "OPENAI_BASE_URL": profile.base_url, "OPENAI_MODEL": profile.model})
        else:
            updates.update({"ANTHROPIC_API_KEY": profile.api_key, "ANTHROPIC_BASE_URL": profile.base_url, "ANTHROPIC_MODEL": profile.model})
        set_env_values(updates)
    print(f"已添加模型 Provider: {profile.id}")
    print(f"- 接口分组: {profile.protocol}")
    print(f"- 模型: {profile.model or '(empty)'}")
    return 0


def run_model_use(args: argparse.Namespace) -> int:
    _configure_stdio()
    try:
        profile = set_active_model_profile(args.profile, args.model)
    except ValueError as exc:
        print(str(exc))
        return 2
    ensure_env_file()
    updates = {"MODEL_PROFILE_NAME": profile.id, "AGENT_PROVIDER": profile.protocol, "AGENT_ROUTE": profile.protocol}
    if profile.protocol == "openai":
        updates.update({"OPENAI_API_KEY": profile.api_key, "OPENAI_BASE_URL": profile.base_url, "OPENAI_MODEL": profile.model})
    else:
        updates.update({"ANTHROPIC_API_KEY": profile.api_key, "ANTHROPIC_BASE_URL": profile.base_url, "ANTHROPIC_MODEL": profile.model})
    set_env_values(updates)
    print(f"已切换模型 Provider: {profile.id}")
    print(f"- 接口分组: {profile.protocol}")
    print(f"- 模型: {profile.model or '(empty)'}")
    return 0


def run_channel_setup(args: argparse.Namespace) -> int:
    _configure_stdio()
    ensure_env_file()
    values = load_env_values()
    updates: dict[str, str] = {}
    channel = args.channel

    if channel == "telegram":
        token = args.telegram_bot_token or _value(values, "TELEGRAM_BOT_TOKEN")
        owner = args.owner_id or _value(values, "OWNER_ID")
        if not args.non_interactive:
            token = _prompt("TELEGRAM_BOT_TOKEN", "" if not _has_value(values, "TELEGRAM_BOT_TOKEN") else token, secret=True)
            owner = _prompt("OWNER_ID (Telegram user id)", "" if not _has_value(values, "OWNER_ID") else owner)
        updates["TELEGRAM_BOT_TOKEN"] = token
        updates["OWNER_ID"] = owner
        print("已更新 Telegram 通道配置。")
    elif channel == "feishu":
        app_id = args.feishu_app_id or _value(values, "FEISHU_APP_ID")
        app_secret = args.feishu_app_secret or _value(values, "FEISHU_APP_SECRET")
        if not args.non_interactive:
            app_id = _prompt("FEISHU_APP_ID", "" if not _has_value(values, "FEISHU_APP_ID") else app_id)
            app_secret = _prompt("FEISHU_APP_SECRET", "" if not _has_value(values, "FEISHU_APP_SECRET") else app_secret, secret=True)
        updates["FEISHU_APP_ID"] = app_id
        updates["FEISHU_APP_SECRET"] = app_secret
        print("已更新 Feishu 通道配置。")
    elif channel == "weixin":
        if args.non_interactive:
            account_id = args.weixin_account_id or _value(values, "WEIXIN_ACCOUNT_ID")
            token = args.weixin_token or _value(values, "WEIXIN_TOKEN")
            updates["WEIXIN_ACCOUNT_ID"] = account_id
            updates["WEIXIN_TOKEN"] = token
        else:
            credentials = _run_weixin_qr_login(args)
            if not credentials:
                print("Weixin QR login did not complete.")
                return 1
            updates["WEIXIN_ACCOUNT_ID"] = credentials["account_id"]
            updates["WEIXIN_TOKEN"] = credentials["token"]
            updates["WEIXIN_BASE_URL"] = credentials["base_url"]
        print("Updated Weixin channel configuration.")
    elif channel == "none":
        updates.update({"TELEGRAM_BOT_TOKEN": "", "OWNER_ID": "", "FEISHU_APP_ID": "", "FEISHU_APP_SECRET": "", "WEIXIN_ACCOUNT_ID": "", "WEIXIN_TOKEN": ""})
        print("已关闭 Telegram / Feishu 通道配置。")

    set_env_values(updates)
    print("检查配置可运行: weclaw doctor")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="weclaw", description="WeClaw service and setup tools")
    subparsers = parser.add_subparsers(dest="command")

    setup_parser = subparsers.add_parser("setup", help="交互式生成/更新 .env 配置")
    setup_parser.add_argument("--non-interactive", action="store_true", help="不提问，只根据参数和默认值写入 .env")
    setup_parser.add_argument("--provider", choices=["openai", "openai_compatible", "claude", "anthropic", "anthropic_compatible"])
    setup_parser.add_argument("--profile-name")
    setup_parser.add_argument("--channel", choices=["tui", "telegram", "feishu", "weixin", "none"])
    setup_parser.add_argument("--openai-api-key")
    setup_parser.add_argument("--openai-base-url")
    setup_parser.add_argument("--openai-model")
    setup_parser.add_argument("--anthropic-api-key")
    setup_parser.add_argument("--anthropic-base-url")
    setup_parser.add_argument("--anthropic-model")
    setup_parser.add_argument("--skip-model-discovery", action="store_true")
    setup_parser.add_argument("--telegram-bot-token")
    setup_parser.add_argument("--owner-id")
    setup_parser.add_argument("--feishu-app-id")
    setup_parser.add_argument("--feishu-app-secret")
    setup_parser.add_argument("--weixin-account-id")
    setup_parser.add_argument("--weixin-token")
    setup_parser.add_argument("--weixin-bot-type", default="3")
    setup_parser.add_argument("--weixin-login-timeout", type=int, default=480)
    setup_parser.add_argument("--weixin-no-open-browser", action="store_true")
    setup_parser.add_argument("--tavily-api-key")
    setup_parser.add_argument("--dashboard-host")
    setup_parser.add_argument("--dashboard-port")

    doctor_parser = subparsers.add_parser("doctor", help="检查当前 .env 是否具备启动条件")
    doctor_parser.add_argument("--quiet", action="store_true", help="只返回退出码，不输出报告")
    doctor_parser.add_argument("--tui-only", action="store_true", help="只检查本地 TUI 所需配置，不要求消息通道")
    doctor_parser.add_argument("--provider-check", action="store_true", help="实际调用当前模型 Provider，检查文本 Agent 链路是否可用")

    config_parser = subparsers.add_parser("config", help="命令行读取/更新 .env")
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    set_parser = config_subparsers.add_parser("set", help="写入 KEY=VALUE 配置")
    set_parser.add_argument("pairs", nargs="+")
    get_parser = config_subparsers.add_parser("get", help="读取配置")
    get_parser.add_argument("keys", nargs="*")
    get_parser.add_argument("--show-secrets", action="store_true", help="显示完整密钥值；默认会隐藏 KEY/TOKEN/SECRET/PASSWORD")

    model_parser = subparsers.add_parser("model", help="管理模型 Provider 与模型选择")
    model_subparsers = model_parser.add_subparsers(dest="model_command")
    model_subparsers.add_parser("list", help="列出当前可用模型 Provider")
    model_add_parser = model_subparsers.add_parser("add", help="新增模型 Provider")
    model_add_parser.add_argument("--protocol", choices=["openai", "openai_compatible", "claude", "anthropic_compatible"])
    model_add_parser.add_argument("--name")
    model_add_parser.add_argument("--api-key")
    model_add_parser.add_argument("--base-url", default="")
    model_add_parser.add_argument("--model", default="")
    model_add_parser.add_argument("--no-activate", action="store_true")
    model_add_parser.add_argument("--skip-model-discovery", action="store_true")
    model_refresh_parser = model_subparsers.add_parser("refresh", help="从服务商接口刷新可选模型列表")
    model_refresh_parser.add_argument("profile", nargs="?")
    model_use_parser = model_subparsers.add_parser("use", help="切换当前模型 Provider")
    model_use_parser.add_argument("profile")
    model_use_parser.add_argument("model", nargs="?")

    channel_parser = subparsers.add_parser("channel", help="快速配置 Telegram / Feishu 通道")
    channel_subparsers = channel_parser.add_subparsers(dest="channel_command")
    channel_setup_parser = channel_subparsers.add_parser("setup", help="配置或关闭一个消息通道")
    channel_setup_parser.add_argument("channel", choices=["telegram", "feishu", "weixin", "none"])
    channel_setup_parser.add_argument("--non-interactive", action="store_true")
    channel_setup_parser.add_argument("--telegram-bot-token")
    channel_setup_parser.add_argument("--owner-id")
    channel_setup_parser.add_argument("--feishu-app-id")
    channel_setup_parser.add_argument("--feishu-app-secret")
    channel_setup_parser.add_argument("--weixin-account-id")
    channel_setup_parser.add_argument("--weixin-token")
    channel_setup_parser.add_argument("--weixin-bot-type", default="3")
    channel_setup_parser.add_argument("--weixin-login-timeout", type=int, default=480)
    channel_setup_parser.add_argument("--weixin-no-open-browser", action="store_true")

    subparsers.add_parser("run", help="前台启动 WeClaw 服务，适合本地调试或进程管理器托管")
    subparsers.add_parser("start", help="后台启动 WeClaw 服务，等价于 scripts/start.sh")
    subparsers.add_parser("stop", help="停止后台 WeClaw 服务，等价于 scripts/stop.sh")
    subparsers.add_parser("status", help="查看后台 WeClaw 服务状态、日志位置和 Dashboard 地址")
    logs_parser = subparsers.add_parser("logs", help="查看后台 WeClaw 日志")
    logs_parser.add_argument("-n", "--lines", type=int, default=80, help="显示最近多少行日志，默认 80")
    logs_parser.add_argument("-f", "--follow", action="store_true", help="持续跟随日志输出")
    return parser
