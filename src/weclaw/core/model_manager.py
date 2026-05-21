from __future__ import annotations

from dataclasses import replace

from weclaw.core.interactive import SelectOption, prompt_secret, prompt_text, select_option, select_text_candidate
from weclaw.core.model_discovery import discover_models
from weclaw.core.model_profiles import (
    ModelProfile,
    delete_model_profile,
    get_active_model_profile,
    render_model_profiles,
    set_active_model_profile,
    update_profile_available_models,
    upsert_model_profile,
)
from weclaw.core.model_selector import (
    protocol_label,
    select_model_for_profile,
    select_model_manager_action,
    select_model_profile,
    select_protocol,
)
from weclaw.core.provider_state import normalize_provider


def _default_profile_name(protocol: str) -> str:
    return "openai-main" if protocol == "openai" else "claude-main"


def _default_model(protocol: str) -> str:
    return "gpt-4o-mini" if protocol == "openai" else ""


def _profile_summary(profile: ModelProfile) -> str:
    return "\n".join(
        [
            f"Provider: {profile.id}",
            f"接口分组: {protocol_label(profile.protocol)}",
            f"模型: {profile.model or '(empty)'}",
            f"Base URL: {profile.base_url or '(default endpoint)'}",
            f"候选模型: {len(profile.available_models)}",
        ]
    )


async def refresh_profile_models(profile: ModelProfile, *, choose_after_refresh: bool = True) -> str:
    result = await discover_models(profile)
    if not result.models:
        return f"未能刷新 {profile.id} 的模型列表：{result.error or 'provider did not return models'}"

    updated = update_profile_available_models(profile.id, result.models)
    lines = [
        f"已从 {result.endpoint} 检测到 {len(updated.available_models)} 个模型。",
        f"当前模型: {updated.model or '(empty)'}",
    ]

    if choose_after_refresh:
        model = select_model_for_profile(updated)
        if model:
            updated = set_active_model_profile(updated.id, model)
            lines.append(f"已切换到: {updated.id} / {updated.model or '(empty)'}")
    return "\n".join(lines)


async def prompt_model_profile_fields(existing: ModelProfile | None = None) -> ModelProfile | None:
    default_protocol = existing.protocol if existing else "openai"
    protocol = select_protocol(default_protocol)
    if not protocol:
        return None
    protocol = normalize_provider(protocol, default="openai")

    name = prompt_text(
        "Provider 名称/profile_id",
        default=existing.id if existing else _default_profile_name(protocol),
    )
    if name is None or not name.strip():
        return None

    api_key = prompt_secret("API Key", default=existing.api_key if existing else "")
    if api_key is None:
        return None

    base_url_hint = "Base URL（官方服务可留空；第三方兼容服务通常填写网关地址）"
    base_url = prompt_text(base_url_hint, default=existing.base_url if existing else "")
    if base_url is None:
        return None

    model = existing.model if existing else _default_model(protocol)
    candidate = ModelProfile(
        id=name.strip(),
        name=name.strip(),
        protocol=protocol,
        api_key=api_key.strip(),
        base_url=base_url.strip(),
        model=model.strip(),
        available_models=existing.available_models if existing and existing.protocol == protocol and existing.base_url == base_url.strip() else (),
    )

    available_models: tuple[str, ...] = ()
    if candidate.api_key:
        result = await discover_models(candidate)
        if result.models:
            available_models = tuple(result.models)
            selected_model = select_text_candidate(
                "选择模型",
                f"已从 {result.endpoint} 检测到 {len(result.models)} 个模型。",
                result.models,
                current=candidate.model,
                allow_manual=True,
                manual_label="手动输入 model id",
            )
            if selected_model is not None:
                model = selected_model
        else:
            print(f"未能自动检测模型列表，将保留手动输入。原因：{result.error}")

    if not available_models:
        manual_model = prompt_text("Model id（直接回车保留当前）", default=model)
        if manual_model is None:
            return None
        model = manual_model

    return replace(candidate, model=model.strip(), available_models=available_models)


async def add_model_profile() -> str:
    profile = await prompt_model_profile_fields()
    if profile is None:
        return "已取消新增 Provider。"
    saved = upsert_model_profile(profile, activate=True)
    return "已新增并切换模型 Provider：\n" + _profile_summary(saved)


async def edit_model_profile() -> str:
    profile = select_model_profile("选择要编辑的 Provider")
    if profile is None:
        return "已取消编辑。"
    updated = await prompt_model_profile_fields(profile)
    if updated is None:
        return "已取消编辑。"
    saved = upsert_model_profile(updated, activate=get_active_model_profile().id == profile.id)
    if saved.id != profile.id:
        delete_model_profile(profile.id)
    return "已更新模型 Provider：\n" + _profile_summary(saved)


def delete_model_profile_interactive() -> str:
    profile = select_model_profile("选择要删除的 Provider")
    if profile is None:
        return "已取消删除。"
    decision = select_option(
        "确认删除",
        f"删除 {profile.id}？这只会删除自定义 profile，不会清空 .env 里的默认配置。",
        [SelectOption("yes", "删除"), SelectOption("no", "取消")],
        default="no",
    )
    if decision != "yes":
        return "已取消删除。"
    if delete_model_profile(profile.id):
        return f"已删除 Provider：{profile.id}"
    return f"{profile.id} 来自 .env 默认配置，不能直接删除；可以编辑 .env 或新增同协议 Provider 覆盖使用。"


async def switch_model_profile_interactive() -> str:
    profile = select_model_profile()
    if profile is None:
        return "已取消切换。"
    model = select_model_for_profile(profile)
    selected = set_active_model_profile(profile.id, model)
    return "已切换模型 Provider：\n" + _profile_summary(selected)


async def refresh_model_profile_interactive() -> str:
    profile = select_model_profile("选择要刷新的 Provider")
    if profile is None:
        return "已取消刷新。"
    return await refresh_profile_models(profile)


async def run_model_manager_once() -> str:
    action = select_model_manager_action()
    if action in {None, "cancel"}:
        return "已退出模型管理。"
    if action == "switch":
        return await switch_model_profile_interactive()
    if action == "refresh":
        return await refresh_model_profile_interactive()
    if action == "add":
        return await add_model_profile()
    if action == "edit":
        return await edit_model_profile()
    if action == "delete":
        return delete_model_profile_interactive()
    if action == "list":
        return render_model_profiles()
    return "未知模型管理操作。"
