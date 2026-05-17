from __future__ import annotations

from hiclaw.config import ANTHROPIC_MODEL, OPENAI_MODEL
from hiclaw.core.model_profiles import find_profile_by_protocol, get_active_model_profile
from hiclaw.core.provider_state import normalize_provider


def get_default_model(provider: str) -> str:
    normalized = normalize_provider(provider, default="openai")
    profile = find_profile_by_protocol(normalized)
    if profile is not None and profile.model:
        return profile.model
    if normalized == "claude":
        return (ANTHROPIC_MODEL or "").strip()
    return (OPENAI_MODEL or "gpt-4o-mini").strip()


def get_effective_model(provider: str) -> str:
    normalized = normalize_provider(provider, default="openai")
    return get_default_model(normalized)


def get_provider_mode_label(provider: str) -> str:
    normalized = normalize_provider(provider, default="openai")
    if normalized == "claude":
        return "anthropic-compatible"
    return "openai-compatible"


def get_effective_api_key(provider: str) -> str:
    normalized = normalize_provider(provider, default="openai")
    profile = find_profile_by_protocol(normalized)
    if profile is not None and profile.api_key:
        return profile.api_key
    if normalized == "claude":
        from hiclaw.config import ANTHROPIC_API_KEY

        return ANTHROPIC_API_KEY or ""
    from hiclaw.config import OPENAI_API_KEY

    return OPENAI_API_KEY or ""


def get_effective_base_url(provider: str) -> str:
    normalized = normalize_provider(provider, default="openai")
    profile = find_profile_by_protocol(normalized)
    if profile is not None and profile.base_url:
        return profile.base_url
    if normalized == "claude":
        from hiclaw.config import ANTHROPIC_BASE_URL

        return ANTHROPIC_BASE_URL or ""
    from hiclaw.config import OPENAI_BASE_URL

    return OPENAI_BASE_URL or "https://api.openai.com/v1"


def get_active_provider_model() -> tuple[str, str]:
    profile = get_active_model_profile()
    return profile.protocol, profile.model
