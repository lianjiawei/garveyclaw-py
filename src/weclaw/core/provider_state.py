from __future__ import annotations



import json
from weclaw.config import DATA_DIR, AGENT_PROVIDER


PROVIDER_STATE_FILE = DATA_DIR / "agent_provider.json"
PROVIDER_ALIASES: dict[str, str] = {
    "openai": "openai",
    "openai_compatible": "openai",
    "claude": "claude",
    "anthropic": "claude",
    "anthropic_compatible": "claude",
}


def normalize_provider(provider: str | None, default: str = "openai") -> str:
    raw = str(provider or "").strip().lower()
    if not raw:
        return default
    return PROVIDER_ALIASES.get(raw, raw)


def get_provider() -> str:
    fallback = normalize_provider(AGENT_PROVIDER, default="openai")
    try:
        from weclaw.core.model_profiles import get_active_model_profile

        return normalize_provider(get_active_model_profile().protocol, default=fallback)
    except Exception:
        pass
    if not PROVIDER_STATE_FILE.exists():
        return fallback
    try:
        data = json.loads(PROVIDER_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    provider = normalize_provider(data.get("provider"), default=fallback)
    return provider or fallback


def set_provider(provider: str) -> str:
    normalized = normalize_provider(provider, default=normalize_provider(AGENT_PROVIDER, default="openai"))
    try:
        from weclaw.core.model_profiles import find_profile_by_protocol, set_active_model_profile

        profile = find_profile_by_protocol(normalized)
        if profile is not None:
            set_active_model_profile(profile.id)
    except Exception:
        pass
    PROVIDER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROVIDER_STATE_FILE.write_text(json.dumps({"provider": normalized}, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized
