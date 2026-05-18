from __future__ import annotations

from weclaw.capabilities.tools import ToolContext, ToolSpec, get_tool_registry_status, get_tool_spec, list_tool_specs
from weclaw.capabilities import workflows as workflow_store

CATEGORY_LABELS = {
    "system": "System",
    "workspace": "Workspace",
    "research": "Research",
    "communication": "Communication",
    "media": "Media",
    "tasks": "Tasks",
    "skills": "Skills",
    "workflows": "Workflows",
    "general": "General",
}

RISK_LABELS = {
    "normal": "低",
    "write": "中",
    "execute": "高",
    "external": "高",
    "destructive": "高",
}


def _category_label(value: str) -> str:
    return CATEGORY_LABELS.get(value, value.title())


def _risk_label(value: str) -> str:
    return RISK_LABELS.get(value, value)


def _format_parameters(spec: ToolSpec) -> list[str]:
    properties = spec.parameters.get("properties", {})
    required = set(spec.parameters.get("required", []))
    if not properties:
        return ["- 无"]
    lines: list[str] = []
    for name, meta in properties.items():
        type_name = str(meta.get("type") or "string")
        description = str(meta.get("description") or "")
        suffix = "必填" if name in required else "可选"
        lines.append(f"- {name} ({type_name}, {suffix}){('：' + description) if description else ''}")
    return lines


def build_tool_catalog_text(provider: str | None = None, context: ToolContext | None = None) -> str:
    specs = list_tool_specs(provider=provider, context=context)
    if not specs:
        return "当前没有可展示的工具。"
    status = get_tool_registry_status()
    grouped: dict[str, list[ToolSpec]] = {}
    for spec in specs:
        grouped.setdefault(spec.category, []).append(spec)

    lines = ["当前可用工具：", f"加载状态：{status.state}", f"工作流目录：{status.workflow_directory}"]
    if status.loaded_workflow_files:
        lines.append(f"已加载工作流文件：{', '.join(status.loaded_workflow_files)}")
    if status.last_error:
        lines.append(f"最近一次热加载失败：{status.last_error}")
    for category in sorted(grouped.keys(), key=lambda item: (_category_label(item), item)):
        lines.append("")
        lines.append(f"[{_category_label(category)}]")
        for spec in sorted(grouped[category], key=lambda item: item.name):
            confirmation = " | 需确认" if spec.requires_confirmation() else ""
            lines.append(f"- {spec.name} [{_risk_label(spec.risk_level)}风险{confirmation}]：{spec.description}")
    lines.append("")
    lines.append("发送 /tools 工具名 查看详情。")
    return "\n".join(lines)


def build_tool_detail_text(name: str, provider: str | None = None, context: ToolContext | None = None) -> str | None:
    spec = get_tool_spec(name.strip())
    if spec is None:
        return None
    if provider is not None and spec not in list_tool_specs(provider=provider, context=context):
        return None
    lines = [
        f"Tool: {spec.name}",
        f"类别：{_category_label(spec.category)}",
        f"支持 Provider：{', '.join(sorted(spec.providers))}",
        f"风险级别：{_risk_label(spec.risk_level)}",
        f"执行前确认：{'是' if spec.requires_confirmation() else '否'}",
        f"来源文件：{spec.source_path if spec.source_path else '内置运行时代码'}",
        f"说明：{spec.description}",
        "",
        "参数：",
        *_format_parameters(spec),
    ]
    confirmation_prompt = spec.build_confirmation_prompt({})
    if confirmation_prompt:
        lines.extend(["", f"确认提示模板：{confirmation_prompt}"])
    return "\n".join(lines)


def build_workflow_catalog_text() -> str:
    workflows = workflow_store.list_workflows()
    if not workflows:
        return "当前没有可展示的 workflow。"
    lines = ["当前可用 workflow："]
    for workflow in workflows:
        source = workflow.source_path.name if workflow.source_path is not None else "runtime"
        lines.append(f"- {workflow.name}：{workflow.description}（{source}）")
    lines.append("")
    lines.append("发送 /workflows 名称 查看详情。")
    return "\n".join(lines)


def build_workflow_detail_text(name: str) -> str | None:
    workflow = workflow_store.get_workflow(name)
    if workflow is None:
        return None
    source = workflow.source_path.name if workflow.source_path is not None else "runtime"
    lines = [
        f"Workflow: {workflow.name}",
        f"来源文件：{source}",
        f"说明：{workflow.description}",
        f"风险级别：{workflow.risk_level}",
        f"支持 Provider：{', '.join(sorted(workflow.providers))}",
        "",
        "步骤：",
    ]
    for index, step in enumerate(workflow.steps, 1):
        lines.append(f"{index}. {step.name} -> {step.tool_name}")
    lines.append("")
    lines.append("参数：")
    parameters = workflow.parameters.get("properties", {})
    required = set(workflow.parameters.get("required", []))
    if not parameters:
        lines.append("- 无")
    else:
        for param_name, meta in parameters.items():
            type_name = str(meta.get("type") or "string")
            description = str(meta.get("description") or "")
            suffix = "必填" if param_name in required else "可选"
            lines.append(f"- {param_name} ({type_name}, {suffix}){('：' + description) if description else ''}")
    return "\n".join(lines)
