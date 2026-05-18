from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import weclaw.config as config

WorkflowExecutor = Callable[[str, dict[str, Any], Any], Awaitable[Any]]
WorkflowArgumentBuilder = Callable[[dict[str, Any], dict[str, Any], Any], dict[str, Any]]
WorkflowFinalizer = Callable[[dict[str, Any], list[tuple[str, Any]], Any], Any]


@dataclass(frozen=True, slots=True)
class WorkflowArgumentBinding:
    source: str
    value: Any = None
    step: str | None = None
    path: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowStepDefinition:
    name: str
    tool_name: str
    build_arguments: WorkflowArgumentBuilder


@dataclass(frozen=True, slots=True)
class WorkflowSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    steps: tuple[WorkflowStepDefinition, ...]
    finalize: WorkflowFinalizer
    providers: frozenset[str] = field(default_factory=lambda: frozenset({"claude", "openai"}))
    risk_level: str = "normal"
    category: str = "workflows"
    source_path: Path | None = None


class WorkflowDefinitionError(ValueError):
    pass


def validate_workflow_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise WorkflowDefinitionError("Workflow name cannot be empty.")
    if not normalized.replace("_", "").replace("-", "").isalnum():
        raise WorkflowDefinitionError("Workflow name can only contain letters, numbers, underscores, and hyphens.")
    if not normalized[0].isalpha():
        raise WorkflowDefinitionError("Workflow name must start with a letter.")
    return normalized


def _extract_identifier_list(text: str) -> list[str]:
    return [match.group(0) for match in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", text)]


def _extract_input_parameters(request_text: str) -> list[str]:
    match = re.search(r"输入参数[:：]?\s*([^。\n]+)", request_text)
    if not match:
        return []
    names = _extract_identifier_list(match.group(1))
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _extract_step_argument_names(segment: str) -> list[str]:
    match = re.search(r"(?:参数(?:使用)?|传入|使用)\s*([^。\n]+)", segment)
    if not match:
        return []
    names = _extract_identifier_list(match.group(1))
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _available_workflow_tool_names() -> list[str]:
    from weclaw.capabilities.tools import get_tool_registry

    names: list[str] = []
    for spec in get_tool_registry().list():
        if spec.category == "workflows":
            continue
        names.append(spec.name)
    names.sort(key=len, reverse=True)
    return names


def _find_tool_name(segment: str, tool_names: list[str]) -> str | None:
    for tool_name in tool_names:
        if tool_name in segment:
            return tool_name
    return None


def _normalize_workflow_parameters(parameter_names: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            name: {"type": "string", "description": f"输入参数 {name}"}
            for name in parameter_names
        },
        "required": parameter_names,
    }


@dataclass(frozen=True, slots=True)
class WorkflowDirectorySnapshot:
    directory: Path
    entries: tuple[tuple[str, int, int], ...]


@dataclass(frozen=True, slots=True)
class WorkflowLoadReport:
    directory: Path
    snapshot: WorkflowDirectorySnapshot
    specs: tuple[WorkflowSpec, ...]

    @property
    def loaded_files(self) -> tuple[str, ...]:
        return tuple(spec.source_path.name for spec in self.specs if spec.source_path is not None)


def _copy_input_arguments(fields: list[str]) -> WorkflowArgumentBuilder:
    def _builder(args: dict[str, Any], _outputs: dict[str, Any], _ctx: Any) -> dict[str, Any]:
        return {field: args.get(field, "") for field in fields}

    return _builder


def _read_result_path(result: Any, path: str | None) -> Any:
    if not path:
        return result
    current = result
    for segment in path.split("."):
        if segment == "text" and hasattr(current, "to_text"):
            current = current.to_text()
            continue
        if isinstance(current, dict):
            current = current.get(segment)
        elif isinstance(current, list):
            if not segment.isdigit():
                return None
            index = int(segment)
            if index < 0 or index >= len(current):
                return None
            current = current[index]
        else:
            current = getattr(current, segment, None)
        if current is None:
            return None
    return current


def _build_argument_mapping(arguments: dict[str, WorkflowArgumentBinding]) -> WorkflowArgumentBuilder:
    def _builder(args: dict[str, Any], outputs: dict[str, Any], _ctx: Any) -> dict[str, Any]:
        built: dict[str, Any] = {}
        for key, binding in arguments.items():
            if binding.source == "input":
                built[key] = args.get(str(binding.value), "")
                continue
            if binding.source == "constant":
                built[key] = binding.value
                continue
            if binding.source == "step_output":
                if not binding.step:
                    raise WorkflowDefinitionError(f"Step output binding for '{key}' is missing step.")
                step_result = outputs.get(binding.step)
                built[key] = _read_result_path(step_result, binding.path)
                continue
            raise WorkflowDefinitionError(f"Unsupported argument binding source: {binding.source}")
        return built

    return _builder


def _finalize_skill_file_preview(args: dict[str, Any], results: list[tuple[str, Any]], _ctx: Any) -> Any:
    action_name = args.get("name", "")
    action_result = results[0][1]
    preview_result = results[-1][1]
    if getattr(action_result, "is_error", False):
        return action_result
    if getattr(preview_result, "is_error", False):
        return preview_result
    text = getattr(preview_result, "to_text")()
    try:
        from weclaw.skills import store as skill_store

        skill = skill_store.get_skill(str(action_name))
        if skill is not None and skill.file_path.exists():
            text = skill.file_path.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return preview_result.__class__(
        content=[
            {
                "type": "text",
                "text": f"工作流执行完成：{action_name}\n\n{text}",
            }
        ],
        is_error=False,
    )


FINALIZER_BUILDERS: dict[str, WorkflowFinalizer] = {
    "skill_file_preview": _finalize_skill_file_preview,
}


def _finalize_last_step_text(args: dict[str, Any], results: list[tuple[str, Any]], _ctx: Any) -> Any:
    workflow_name = args.get("name", "workflow")
    last_result = results[-1][1]
    if getattr(last_result, "is_error", False):
        return last_result
    text = getattr(last_result, "to_text")()
    return last_result.__class__(
        content=[
            {
                "type": "text",
                "text": f"工作流执行完成：{workflow_name}\n\n{text}",
            }
        ],
        is_error=False,
    )


FINALIZER_BUILDERS["last_step_text"] = _finalize_last_step_text


def build_workflow_handler(spec: WorkflowSpec, executor: WorkflowExecutor):
    async def _handler(args: dict[str, Any], ctx: Any):
        outputs: dict[str, Any] = {}
        results: list[tuple[str, Any]] = []
        for step in spec.steps:
            step_args = step.build_arguments(args, outputs, ctx)
            result = await executor(step.tool_name, step_args, ctx)
            outputs[step.name] = result
            results.append((step.name, result))
            if getattr(result, "is_error", False):
                return result
        return spec.finalize(args, results, ctx)

    return _handler


def compile_workflow_definition_from_request(
    *,
    name: str,
    request_text: str,
    description: str | None = None,
    providers: list[str] | None = None,
    risk_level: str = "normal",
) -> str:
    workflow_name = validate_workflow_name(name)
    text = request_text.strip()
    if not text:
        raise WorkflowDefinitionError("Workflow request_text cannot be empty.")

    tool_names = _available_workflow_tool_names()
    segments = [segment.strip() for segment in re.split(r"[。\n]+", text) if segment.strip()]
    steps: list[dict[str, Any]] = []
    input_parameters = _extract_input_parameters(text)

    for index, segment in enumerate(segments, 1):
        tool_name = _find_tool_name(segment, tool_names)
        if tool_name is None:
            continue
        argument_names = _extract_step_argument_names(segment)
        if not argument_names:
            argument_names = ["name"] if tool_name == "read_skill" else []
        for argument_name in argument_names:
            if argument_name not in input_parameters:
                input_parameters.append(argument_name)
        steps.append(
            {
                "name": f"step_{index}_{tool_name}",
                "tool_name": tool_name,
                "arguments_from_input": argument_names,
            }
        )

    if not steps:
        raise WorkflowDefinitionError("未能从请求中识别任何工具步骤。请明确写出要调用的工具名，例如 update_skill、read_skill。")
    if not input_parameters:
        raise WorkflowDefinitionError("未能从请求中识别输入参数。请显式写出“输入参数 name, description”或“参数使用 name”。")

    finalizer = "last_step_text"
    if "read_skill" in {step["tool_name"] for step in steps} and any(keyword in text for keyword in ["预览", "完整内容", "最新内容"]):
        finalizer = "skill_file_preview"

    payload = {
        "name": workflow_name,
        "description": description or f"根据用户请求生成的 workflow：{workflow_name}",
        "category": "workflows",
        "providers": providers or ["claude", "openai"],
        "risk_level": risk_level,
        "parameters": _normalize_workflow_parameters(input_parameters),
        "steps": steps,
        "finalizer": finalizer,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _load_json_definition(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkflowDefinitionError(f"Invalid JSON in workflow definition '{path.name}': {exc}") from exc
    if not isinstance(raw, dict):
        raise WorkflowDefinitionError(f"Workflow definition '{path.name}' must be a JSON object.")
    return raw


def _parse_workflow_payload(raw: dict[str, Any], path: Path | None = None) -> WorkflowSpec:
    path_label = path.name if path is not None else "<memory>"
    name = validate_workflow_name(str(raw.get("name") or "").strip())
    description = str(raw.get("description") or "").strip()
    parameters = raw.get("parameters")
    finalizer_name = str(raw.get("finalizer") or "").strip()
    providers_raw = raw.get("providers") or ["claude", "openai"]
    risk_level = str(raw.get("risk_level") or "normal").strip() or "normal"
    category = str(raw.get("category") or "workflows").strip() or "workflows"
    steps_raw = raw.get("steps")

    if not description or not finalizer_name:
        raise WorkflowDefinitionError(f"Workflow '{path_label}' must define description and finalizer.")
    if not isinstance(parameters, dict):
        raise WorkflowDefinitionError(f"Workflow '{path_label}' must define parameters as an object.")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise WorkflowDefinitionError(f"Workflow '{path_label}' must define a non-empty steps list.")
    if not isinstance(providers_raw, list) or not all(isinstance(item, str) for item in providers_raw):
        raise WorkflowDefinitionError(f"Workflow '{path_label}' has invalid providers.")

    finalizer = FINALIZER_BUILDERS.get(finalizer_name)
    if finalizer is None:
        raise WorkflowDefinitionError(f"Workflow '{path_label}' uses unsupported finalizer '{finalizer_name}'.")

    steps = tuple(_parse_step(path or Path(path_label), step) for step in steps_raw)
    return WorkflowSpec(
        name=name,
        description=description,
        parameters=parameters,
        steps=steps,
        finalize=finalizer,
        providers=frozenset(provider.strip() for provider in providers_raw if provider.strip()),
        risk_level=risk_level,
        category=category,
        source_path=path,
    )


def parse_workflow_definition_text(text: str, source_path: Path | None = None) -> WorkflowSpec:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WorkflowDefinitionError(f"Invalid workflow JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorkflowDefinitionError("Workflow definition must be a JSON object.")
    return _parse_workflow_payload(raw, source_path)


def snapshot_workflow_definitions(directory: Path | None = None) -> WorkflowDirectorySnapshot:
    base_dir = directory or config.WORKFLOW_DEFINITIONS_DIR
    if not base_dir.exists():
        return WorkflowDirectorySnapshot(directory=base_dir, entries=())
    entries: list[tuple[str, int, int]] = []
    for path in sorted(base_dir.glob("*.json")):
        stat = path.stat()
        entries.append((path.name, stat.st_mtime_ns, stat.st_size))
    return WorkflowDirectorySnapshot(directory=base_dir, entries=tuple(entries))


def _parse_step(definition_path: Path, payload: dict[str, Any]) -> WorkflowStepDefinition:
    if not isinstance(payload, dict):
        raise WorkflowDefinitionError(f"Workflow '{definition_path.name}' has an invalid step entry.")
    name = str(payload.get("name") or "").strip()
    tool_name = str(payload.get("tool_name") or "").strip()
    fields = payload.get("arguments_from_input")
    arguments = payload.get("arguments")
    if not name or not tool_name:
        raise WorkflowDefinitionError(f"Workflow '{definition_path.name}' requires step name and tool_name.")
    if arguments is not None:
        if not isinstance(arguments, dict):
            raise WorkflowDefinitionError(f"Workflow '{definition_path.name}' step '{name}' has invalid arguments mapping.")
        parsed: dict[str, WorkflowArgumentBinding] = {}
        for arg_name, config in arguments.items():
            if not isinstance(arg_name, str) or not isinstance(config, dict):
                raise WorkflowDefinitionError(f"Workflow '{definition_path.name}' step '{name}' has invalid argument entry.")
            source = str(config.get("source") or "").strip()
            if source == "input":
                input_name = str(config.get("name") or "").strip()
                if not input_name:
                    raise WorkflowDefinitionError(f"Workflow '{definition_path.name}' step '{name}' input binding requires name.")
                parsed[arg_name] = WorkflowArgumentBinding(source="input", value=input_name)
            elif source == "constant":
                parsed[arg_name] = WorkflowArgumentBinding(source="constant", value=config.get("value"))
            elif source == "step_output":
                step_name = str(config.get("step") or "").strip()
                if not step_name:
                    raise WorkflowDefinitionError(f"Workflow '{definition_path.name}' step '{name}' step_output binding requires step.")
                path = str(config.get("path") or "").strip() or None
                parsed[arg_name] = WorkflowArgumentBinding(source="step_output", step=step_name, path=path)
            else:
                raise WorkflowDefinitionError(f"Workflow '{definition_path.name}' step '{name}' has unsupported argument source '{source}'.")
        return WorkflowStepDefinition(name=name, tool_name=tool_name, build_arguments=_build_argument_mapping(parsed))
    if fields is None:
        fields = []
    if not isinstance(fields, list) or not all(isinstance(item, str) for item in fields):
        raise WorkflowDefinitionError(f"Workflow '{definition_path.name}' step '{name}' has invalid arguments_from_input.")
    return WorkflowStepDefinition(name=name, tool_name=tool_name, build_arguments=_copy_input_arguments(list(fields)))


def load_workflow_spec_from_path(path: Path) -> WorkflowSpec:
    raw = _load_json_definition(path)
    return _parse_workflow_payload(raw, path)


def load_workflow_specs(directory: Path | None = None) -> list[WorkflowSpec]:
    base_dir = directory or config.WORKFLOW_DEFINITIONS_DIR
    if not base_dir.exists():
        return []
    specs: list[WorkflowSpec] = []
    seen_names: set[str] = set()
    for path in sorted(base_dir.glob("*.json")):
        spec = load_workflow_spec_from_path(path)
        if spec.name in seen_names:
            raise WorkflowDefinitionError(f"Duplicate workflow name '{spec.name}' in {base_dir}.")
        seen_names.add(spec.name)
        specs.append(spec)
    return specs


def load_workflow_report(directory: Path | None = None) -> WorkflowLoadReport:
    base_dir = directory or config.WORKFLOW_DEFINITIONS_DIR
    snapshot = snapshot_workflow_definitions(base_dir)
    specs = tuple(load_workflow_specs(base_dir))
    return WorkflowLoadReport(directory=base_dir, snapshot=snapshot, specs=specs)


def workflow_definition_path(name: str, directory: Path | None = None) -> Path:
    base_dir = directory or config.WORKFLOW_DEFINITIONS_DIR
    return base_dir / f"{validate_workflow_name(name)}.json"


def list_workflows(directory: Path | None = None) -> list[WorkflowSpec]:
    return load_workflow_specs(directory)


def get_workflow(name: str, directory: Path | None = None) -> WorkflowSpec | None:
    normalized = validate_workflow_name(name)
    for workflow in load_workflow_specs(directory):
        if workflow.name == normalized:
            return workflow
    return None


def read_workflow_definition(name: str, directory: Path | None = None) -> str:
    path = workflow_definition_path(name, directory)
    if not path.exists():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")


def write_workflow_definition_text(
    definition_text: str,
    *,
    allow_overwrite: bool,
    directory: Path | None = None,
) -> WorkflowSpec:
    spec = parse_workflow_definition_text(definition_text)
    path = workflow_definition_path(spec.name, directory)
    if path.exists() and not allow_overwrite:
        raise WorkflowDefinitionError(f"Workflow '{spec.name}' already exists.")
    path.parent.mkdir(parents=True, exist_ok=True)
    pretty = json.dumps(json.loads(definition_text), ensure_ascii=False, indent=2) + "\n"
    path.write_text(pretty, encoding="utf-8")
    return load_workflow_spec_from_path(path)


def delete_workflow_definition(name: str, directory: Path | None = None) -> Path:
    path = workflow_definition_path(name, directory)
    if not path.exists():
        raise FileNotFoundError(path)
    path.unlink()
    return path
