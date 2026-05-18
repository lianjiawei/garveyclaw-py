from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import weclaw.config as config
from weclaw.agentspec.models import AgentExecutionMode, AgentMemoryScope, AgentRole, AgentSpec

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,63}$")
_ROLES: set[str] = {"planner", "executor", "reviewer", "researcher", "coder", "custom"}
_MEMORY_SCOPES: set[str] = {"session", "shared", "private"}
_EXECUTION_MODES: set[str] = {"single", "collaborative", "both"}


class AgentSpecError(ValueError):
    pass


def validate_agent_name(name: str) -> str:
    normalized = name.strip()
    if not _NAME_RE.match(normalized):
        raise AgentSpecError("Agent name must start with a letter and contain only letters, numbers, underscores, or hyphens.")
    return normalized


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AgentSpecError(f"Agent field '{field_name}' must be a list of strings.")
    return tuple(item.strip() for item in value if item.strip())


def _parse_literal(value: Any, field_name: str, allowed: set[str], default: str) -> str:
    text = str(value or default).strip() or default
    if text not in allowed:
        raise AgentSpecError(f"Agent field '{field_name}' must be one of: {', '.join(sorted(allowed))}.")
    return text


def agent_spec_from_dict(raw: dict[str, Any], *, source: str = "memory") -> AgentSpec:
    if not isinstance(raw, dict):
        raise AgentSpecError("Agent definition must be a JSON object.")
    name = validate_agent_name(str(raw.get("name") or ""))
    title = str(raw.get("title") or name).strip()
    description = str(raw.get("description") or "").strip()
    system_prompt = str(raw.get("system_prompt") or "").strip()
    if not title:
        raise AgentSpecError(f"Agent '{name}' must define title.")
    if not description:
        raise AgentSpecError(f"Agent '{name}' must define description.")
    if not system_prompt:
        raise AgentSpecError(f"Agent '{name}' must define system_prompt.")

    role = _parse_literal(raw.get("role"), "role", _ROLES, "custom")
    memory_scope = _parse_literal(raw.get("memory_scope"), "memory_scope", _MEMORY_SCOPES, "session")
    execution_mode = _parse_literal(raw.get("execution_mode"), "execution_mode", _EXECUTION_MODES, "both")
    default_provider = str(raw.get("default_provider") or "inherit").strip() or "inherit"

    return AgentSpec(
        name=name,
        title=title,
        role=role,  # type: ignore[arg-type]
        description=description,
        system_prompt=system_prompt,
        default_provider=default_provider,
        execution_mode=execution_mode,  # type: ignore[arg-type]
        memory_scope=memory_scope,  # type: ignore[arg-type]
        allowed_tools=_string_tuple(raw.get("allowed_tools"), "allowed_tools"),
        allowed_workflows=_string_tuple(raw.get("allowed_workflows"), "allowed_workflows"),
        allowed_skills=_string_tuple(raw.get("allowed_skills"), "allowed_skills"),
        can_delegate=bool(raw.get("can_delegate", False)),
        can_review=bool(raw.get("can_review", False)),
        source=source,
    )


def agent_spec_to_dict(spec: AgentSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "title": spec.title,
        "role": spec.role,
        "description": spec.description,
        "system_prompt": spec.system_prompt,
        "default_provider": spec.default_provider,
        "execution_mode": spec.execution_mode,
        "memory_scope": spec.memory_scope,
        "allowed_tools": list(spec.allowed_tools),
        "allowed_workflows": list(spec.allowed_workflows),
        "allowed_skills": list(spec.allowed_skills),
        "can_delegate": spec.can_delegate,
        "can_review": spec.can_review,
    }


def load_agent_spec_from_path(path: Path) -> AgentSpec:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AgentSpecError(f"Invalid JSON in agent definition '{path.name}': {exc}") from exc
    return agent_spec_from_dict(raw, source=str(path.name))


def load_agent_specs(directory: Path | None = None) -> list[AgentSpec]:
    base_dir = directory or config.AGENT_DEFINITIONS_DIR
    if not base_dir.exists():
        return []
    specs: list[AgentSpec] = []
    seen: set[str] = set()
    for path in sorted(base_dir.glob("*.json")):
        spec = load_agent_spec_from_path(path)
        if spec.name in seen:
            raise AgentSpecError(f"Duplicate agent name '{spec.name}' in {base_dir}.")
        seen.add(spec.name)
        specs.append(spec)
    return specs


def agent_definition_path(name: str, directory: Path | None = None) -> Path:
    base_dir = directory or config.AGENT_DEFINITIONS_DIR
    return base_dir / f"{validate_agent_name(name)}.json"


def write_agent_spec(spec: AgentSpec, *, allow_overwrite: bool = False, directory: Path | None = None) -> Path:
    path = agent_definition_path(spec.name, directory)
    if path.exists() and not allow_overwrite:
        raise AgentSpecError(f"Agent '{spec.name}' already exists.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(agent_spec_to_dict(spec), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
