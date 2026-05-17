from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from hiclaw.config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_BASE_URL,
    ANTHROPIC_MODEL,
    AGENT_PROVIDER,
    DATA_DIR,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
)
from hiclaw.core.provider_state import normalize_provider

MODEL_PROFILES_FILE = DATA_DIR / "model_profiles.json"


@dataclass(frozen=True, slots=True)
class ModelProfile:
    id: str
    name: str
    protocol: str
    api_key: str = ""
    base_url: str = ""
    model: str = ""


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


def list_model_profiles() -> list[ModelProfile]:
    profiles = _env_profiles()
    raw = _load_raw()
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
        )
    return list(profiles.values())


def get_active_profile_id() -> str:
    raw = _load_raw()
    active = str(raw.get("active_profile") or "").strip()
    profiles = {profile.id: profile for profile in list_model_profiles()}
    if active in profiles:
        return active
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
        )
        upsert_model_profile(selected, activate=False)
    raw = _load_raw()
    raw["active_profile"] = selected.id
    _save_raw(raw)
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
    )
    raw = _load_raw()
    custom = [item for item in raw.get("profiles") or [] if isinstance(item, dict) and item.get("id") != normalized.id]
    custom.append(asdict(normalized))
    raw["profiles"] = custom
    if activate:
        raw["active_profile"] = normalized.id
    _save_raw(raw)
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


def render_model_profiles() -> str:
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
    lines.append("")
    lines.append("Anthropic-compatible:")
    for profile in list_model_profiles():
        if profile.protocol != "claude":
            continue
        marker = "*" if profile.id == active.id else "-"
        lines.append(f"{marker} {profile.id}: {profile.name} | {profile.model or '(empty)'} | {profile.base_url or '(default endpoint)'}")
    lines.append("")
    lines.append("用法: /model use <profile_id> [model]")
    lines.append("新增: python -m hiclaw model add --protocol openai --name deepseek --api-key xxx --base-url https://.../v1 --model deepseek-chat")
    return "\n".join(lines)
