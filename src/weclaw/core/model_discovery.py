from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from weclaw.core.model_profiles import ModelProfile
from weclaw.core.provider_state import normalize_provider


@dataclass(frozen=True, slots=True)
class ModelDiscoveryResult:
    models: list[str]
    endpoint: str = ""
    error: str = ""


def _extract_model_ids(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") or payload.get("models") or payload.get("model")
    if isinstance(data, dict):
        data = data.get("data") or data.get("models") or list(data.values())
    if not isinstance(data, list):
        return []
    models: list[str] = []
    for item in data:
        if isinstance(item, str):
            models.append(item.strip())
            continue
        if not isinstance(item, dict):
            continue
        model_id = item.get("id") or item.get("model") or item.get("name")
        if model_id:
            models.append(str(model_id).strip())
    return sorted(dict.fromkeys(model for model in models if model))


def _candidate_model_urls(profile: ModelProfile) -> list[str]:
    protocol = normalize_provider(profile.protocol, default="openai")
    if protocol == "openai":
        base_url = (profile.base_url or "https://api.openai.com/v1").rstrip("/")
        return [f"{base_url}/models"]
    base_url = (profile.base_url or "https://api.anthropic.com").rstrip("/")
    urls = [f"{base_url}/models"]
    if base_url.endswith("/v1"):
        urls.append(f"{base_url.removesuffix('/v1')}/v1/models")
    else:
        urls.append(f"{base_url}/v1/models")
    return list(dict.fromkeys(urls))


def _headers(profile: ModelProfile) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    protocol = normalize_provider(profile.protocol, default="openai")
    if protocol == "openai":
        if profile.api_key:
            headers["Authorization"] = f"Bearer {profile.api_key}"
        return headers
    if profile.api_key:
        headers["x-api-key"] = profile.api_key
        headers["Authorization"] = f"Bearer {profile.api_key}"
    headers["anthropic-version"] = "2023-06-01"
    return headers


async def discover_models(profile: ModelProfile, *, timeout: float = 20.0) -> ModelDiscoveryResult:
    if not profile.api_key:
        return ModelDiscoveryResult([], error="API key is empty; cannot query model list.")

    last_error = ""
    async with httpx.AsyncClient(timeout=timeout) as client:
        for url in _candidate_model_urls(profile):
            try:
                response = await client.get(url, headers=_headers(profile))
            except httpx.HTTPError as exc:
                last_error = str(exc)
                continue
            if response.status_code >= 400:
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                continue
            try:
                payload = response.json()
            except ValueError:
                last_error = "response is not JSON"
                continue
            models = _extract_model_ids(payload)
            if models:
                return ModelDiscoveryResult(models, endpoint=url)
            last_error = "response did not contain model ids"
    return ModelDiscoveryResult([], error=last_error or "model discovery is not supported by this provider.")
