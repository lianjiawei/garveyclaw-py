from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from weclaw.config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_BASE_URL,
    ANTHROPIC_MODEL,
    AGENT_PROVIDER,
    DATA_DIR,
    MODEL_PROFILE_NAME,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
    PROJECT_ROOT,
)
from weclaw.core.provider_state import normalize_provider

MODEL_PROFILES_FILE = DATA_DIR / "model_profiles.json"
ENV_FILE = PROJECT_ROOT / ".env"


@dataclass(frozen=True, slots=True)
class ModelProfile:
    id: str
    name: str
    protocol: str
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    available_models: tuple[str, ...] = ()


def _slugify(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-")
    return text or "default"


def _env_profiles() -> dict[str, ModelProfile]:
    profiles: dict[str, ModelProfile] = {}
    env_route = normalize_provider(AGENT_PROVIDER, default="openai")
    if env_route == "openai" or OPENAI_API_KEY or OPENAI_BASE_URL:
        profiles["openai-default"] = ModelProfile(
            id="openai-default",
            name="OpenAI compatible",
            protocol="openai",
            api_key=OPENAI_API_KEY or "",
            base_url=OPENAI_BASE_URL or "",
            model=OPENAI_MODEL or "gpt-4o-mini",
        )
    if env_route == "claude" or ANTHROPIC_API_KEY or ANTHROPIC_BASE_URL or ANTHROPIC_MODEL:
        profiles["claude-default"] = ModelProfile(
            id="claude-default",
            name="Anthropic compatible",
            protocol="claude",
            api_key=ANTHROPIC_API_KEY or "",
            base_url=ANTHROPIC_BASE_URL or "",
            model=ANTHROPIC_MODEL or "",
        )
    return profiles


def _load_raw() -> dict:
    if not MODEL_PROFILES_FILE.exists():
        return {}
    try:
        data = json.loads(MODEL_PROFILES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_raw(data: dict) -> None:
    MODEL_PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODEL_PROFILES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _sync_active_profile_to_env(profile: ModelProfile) -> None:
    if not ENV_FILE.exists():
        return
    try:
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    updates = {
        "MODEL_PROFILE_NAME": profile.id,
        "AGENT_ROUTE": profile.protocol,
        "AGENT_PROVIDER": profile.protocol,
    }
    if profile.protocol == "openai":
        updates.update({"OPENAI_API_KEY": profile.api_key, "OPENAI_BASE_URL": profile.base_url, "OPENAI_MODEL": profile.model})
    else:
        updates.update({"ANTHROPIC_API_KEY": profile.api_key, "ANTHROPIC_BASE_URL": profile.base_url, "ANTHROPIC_MODEL": profile.model})

    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue
        key, _value = line.split("=", 1)
        key = key.strip()
        if key in updates:
            output.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={value}")
    try:
        ENV_FILE.write_text("\n".join(output) + "\n", encoding="utf-8")
    except OSError:
        return


def list_model_profiles() -> list[ModelProfile]:
    profiles = _env_profiles()
    raw = _load_raw()
    custom_profiles: list[ModelProfile] = []
    for item in raw.get("profiles") or []:
        if not isinstance(item, dict):
            continue
        protocol = normalize_provider(item.get("protocol"), default="")
        if protocol not in {"openai", "claude"}:
            continue
        profile_id = str(item.get("id") or "").strip() or _slugify(str(item.get("name") or protocol))
        profiles[profile_id] = ModelProfile(
            id=profile_id,
            name=str(item.get("name") or profile_id).strip(),
            protocol=protocol,
            api_key=str(item.get("api_key") or "").strip(),
            base_url=str(item.get("base_url") or "").strip(),
            model=str(item.get("model") or "").strip(),
            available_models=tuple(str(model).strip() for model in item.get("available_models") or () if str(model).strip()),
        )
        custom_profiles.append(profiles[profile_id])

    for default_id in ("openai-default", "claude-default"):
        default_profile = profiles.get(default_id)
        if default_profile is None:
            continue
        if any(
            custom.id != default_id
            and custom.protocol == default_profile.protocol
            and custom.api_key == default_profile.api_key
            and custom.base_url == default_profile.base_url
            and custom.model == default_profile.model
            for custom in custom_profiles
        ):
            profiles.pop(default_id, None)
    return list(profiles.values())


def get_active_profile_id() -> str:
    raw = _load_raw()
    active = str(raw.get("active_profile") or "").strip()
    env_active = str(MODEL_PROFILE_NAME or "").strip()
    profiles = {profile.id: profile for profile in list_model_profiles()}
    if active in profiles:
        return active
    if env_active in profiles:
        return env_active
    if "openai-default" in profiles:
        return "openai-default"
    if profiles:
        return next(iter(profiles))
    return "openai-default"


def get_active_model_profile() -> ModelProfile:
    profiles = {profile.id: profile for profile in list_model_profiles()}
    active_id = get_active_profile_id()
    if active_id in profiles:
        return profiles[active_id]
    return ModelProfile(id="openai-default", name="OpenAI compatible", protocol="openai", model="gpt-4o-mini")


def set_active_model_profile(profile_id: str, model: str | None = None) -> ModelProfile:
    profiles = {profile.id: profile for profile in list_model_profiles()}
    if profile_id not in profiles:
        raise ValueError(f"Unknown model profile: {profile_id}")
    selected = profiles[profile_id]
    if model is not None and model.strip():
        selected = ModelProfile(
            id=selected.id,
            name=selected.name,
            protocol=selected.protocol,
            api_key=selected.api_key,
            base_url=selected.base_url,
            model=model.strip(),
            available_models=selected.available_models,
        )
        upsert_model_profile(selected, activate=False)
    raw = _load_raw()
    raw["active_profile"] = selected.id
    _save_raw(raw)
    _sync_active_profile_to_env(selected)
    return selected


def upsert_model_profile(profile: ModelProfile, *, activate: bool = True) -> ModelProfile:
    profile_id = _slugify(profile.id or profile.name)
    normalized = ModelProfile(
        id=profile_id,
        name=profile.name.strip() or profile_id,
        protocol=normalize_provider(profile.protocol, default="openai"),
        api_key=profile.api_key.strip(),
        base_url=profile.base_url.strip(),
        model=profile.model.strip(),
        available_models=tuple(dict.fromkeys(model.strip() for model in profile.available_models if model.strip())),
    )
    raw = _load_raw()
    custom = [item for item in raw.get("profiles") or [] if isinstance(item, dict) and item.get("id") != normalized.id]
    custom.append(asdict(normalized))
    raw["profiles"] = custom
    if activate:
        raw["active_profile"] = normalized.id
    _save_raw(raw)
    if activate:
        _sync_active_profile_to_env(normalized)
    return normalized


def find_profile_by_protocol(protocol: str) -> ModelProfile | None:
    normalized = normalize_provider(protocol, default="")
    active = get_active_model_profile()
    if active.protocol == normalized:
        return active
    for profile in list_model_profiles():
        if profile.protocol == normalized:
            return profile
    return None


def update_profile_available_models(profile_id: str, models: list[str]) -> ModelProfile:
    profiles = {profile.id: profile for profile in list_model_profiles()}
    if profile_id not in profiles:
        raise ValueError(f"Unknown model profile: {profile_id}")
    profile = profiles[profile_id]
    updated = ModelProfile(
        id=profile.id,
        name=profile.name,
        protocol=profile.protocol,
        api_key=profile.api_key,
        base_url=profile.base_url,
        model=profile.model,
        available_models=tuple(dict.fromkeys(model.strip() for model in models if model.strip())),
    )
    upsert_model_profile(updated, activate=get_active_profile_id() == profile_id)
    return updated


def _render_model_profiles_legacy() -> str:
    active = get_active_model_profile()
    lines = [
        f"当前配置: {active.id}",
        f"接口分组: {'OpenAI-compatible' if active.protocol == 'openai' else 'Anthropic-compatible'}",
        f"当前模型: {active.model or '(empty)'}",
        "",
        "OpenAI-compatible:",
    ]
    for profile in list_model_profiles():
        if profile.protocol != "openai":
            continue
        marker = "*" if profile.id == active.id else "-"
        lines.append(f"{marker} {profile.id}: {profile.name} | {profile.model or '(empty)'} | {profile.base_url or '(default endpoint)'}")
        if profile.available_models:
            suffix = " ..." if len(profile.available_models) > 12 else ""
            lines.append(f"  可选模型: {', '.join(profile.available_models[:12])}{suffix}")
    lines.append("")
    lines.append("Anthropic-compatible:")
    for profile in list_model_profiles():
        if profile.protocol != "claude":
            continue
        marker = "*" if profile.id == active.id else "-"
        lines.append(f"{marker} {profile.id}: {profile.name} | {profile.model or '(empty)'} | {profile.base_url or '(default endpoint)'}")
        if profile.available_models:
            suffix = " ..." if len(profile.available_models) > 12 else ""
            lines.append(f"  可选模型: {', '.join(profile.available_models[:12])}{suffix}")
    lines.append("")
    lines.append("用法: /model use <profile_id> [model]")
    lines.append("新增: weclaw model add --protocol openai --name deepseek --api-key xxx --base-url https://.../v1 --model deepseek-chat")
    lines.append("刷新模型列表: weclaw model refresh [profile_id]")
    return "\n".join(lines)


def resolve_model_profile_selector(selector: str) -> ModelProfile:
    value = selector.strip()
    profiles = list_model_profiles()
    if value.isdigit():
        index = int(value)
        if 1 <= index <= len(profiles):
            return profiles[index - 1]
    for profile in profiles:
        if profile.id == value:
            return profile
    raise ValueError(f"Unknown model profile: {selector}")


def render_model_profiles() -> str:
    active = get_active_model_profile()
    profiles = list_model_profiles()
    lines = [
        f"当前配置: {active.id}",
        f"接口分组: {'OpenAI-compatible' if active.protocol == 'openai' else 'Anthropic-compatible'}",
        f"当前模型: {active.model or '(empty)'}",
        "",
        "回复编号即可切换，例如: /model 1",
        "也可以指定模型: /model 1 qwen3.6-plus",
        "",
        "可选配置:",
    ]
    for index, profile in enumerate(profiles, 1):
        marker = "*" if profile.id == active.id else " "
        protocol_label = "OpenAI-compatible" if profile.protocol == "openai" else "Anthropic-compatible"
        lines.append(
            f"{marker} {index}. {profile.id} | {protocol_label} | "
            f"{profile.model or '(empty)'} | {profile.base_url or '(default endpoint)'}"
        )
        if profile.available_models:
            suffix = " ..." if len(profile.available_models) > 12 else ""
            lines.append(f"   可选模型: {', '.join(profile.available_models[:12])}{suffix}")
    lines.append("")
    lines.append("命令:")
    lines.append("- /model 查看并选择")
    lines.append("- /model <编号或profile_id> [model] 切换")
    lines.append(f"- weclaw model refresh {active.id} 刷新当前配置的模型列表")
    return "\n".join(lines)
