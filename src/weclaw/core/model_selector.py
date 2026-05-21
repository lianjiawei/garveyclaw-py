from __future__ import annotations

from weclaw.core.interactive import SelectOption, select_option, select_text_candidate
from weclaw.core.model_profiles import ModelProfile, get_active_model_profile, list_model_profiles


def protocol_label(protocol: str) -> str:
    return "OpenAI-compatible" if protocol == "openai" else "Anthropic-compatible"


def profile_option(profile: ModelProfile, *, active_id: str | None = None) -> SelectOption:
    marker = "当前" if profile.id == active_id else protocol_label(profile.protocol)
    model = profile.model or "(empty)"
    base_url = profile.base_url or "(default endpoint)"
    model_count = f"{len(profile.available_models)} models" if profile.available_models else "no cached models"
    return SelectOption(
        profile.id,
        f"{profile.id}  |  {model}",
        f"{marker}  |  {model_count}  |  {base_url}",
    )


def select_model_manager_action() -> str | None:
    return select_option(
        "模型管理",
        "选择要执行的操作。",
        [
            SelectOption("switch", "切换模型 Provider / Model", "选择已有配置并切换"),
            SelectOption("refresh", "刷新模型列表", "从当前服务商接口拉取候选模型"),
            SelectOption("add", "新增 Provider", "引导输入名称、Key、URL、模型"),
            SelectOption("edit", "编辑 Provider", "修改已有 Provider 的 Key、URL、模型"),
            SelectOption("delete", "删除 Provider", "仅删除自定义 Provider"),
            SelectOption("list", "查看当前列表", "打印所有已配置 Provider"),
            SelectOption("cancel", "退出", "不做修改"),
        ],
        default="switch",
    )


def select_protocol(default: str = "openai") -> str | None:
    return select_option(
        "选择接口协议",
        "OpenAI-compatible 覆盖 OpenAI、DeepSeek、通义千问等；Anthropic-compatible 覆盖 Claude 兼容网关。",
        [
            SelectOption("openai", "OpenAI-compatible", "OpenAI、DeepSeek、通义千问、OpenRouter 等"),
            SelectOption("claude", "Anthropic / Claude-compatible", "Anthropic 官方或 Claude 兼容网关"),
        ],
        default=default if default in {"openai", "claude"} else "openai",
    )


def select_model_profile(title: str = "选择模型 Provider") -> ModelProfile | None:
    profiles = list_model_profiles()
    if not profiles:
        return None
    active = get_active_model_profile()
    profile_id = select_option(
        title,
        "使用 ↑↓/Tab 选择，Enter 确认，Esc 取消。",
        [profile_option(profile, active_id=active.id) for profile in profiles],
        default=active.id,
    )
    if not profile_id:
        return None
    return next((profile for profile in profiles if profile.id == profile_id), None)


def select_model_for_profile(profile: ModelProfile) -> str | None:
    if not profile.available_models:
        return None
    return select_text_candidate(
        "选择模型",
        f"Provider: {profile.id} ({protocol_label(profile.protocol)})",
        profile.available_models,
        current=profile.model,
        allow_manual=True,
        manual_label="手动输入 model id",
    )


def select_model_profile_and_model() -> tuple[str, str | None] | None:
    selected = select_model_profile()
    if selected is None:
        return None
    model = select_model_for_profile(selected)
    return selected.id, model
