from __future__ import annotations

import json
from typing import Any, Protocol


class ClusterTaskResultLike(Protocol):
    agent_name: str
    text: str
    success: bool
    error: str


class ClusterResultLike(Protocol):
    success: bool
    task_results: tuple[ClusterTaskResultLike, ...]
    error: str


def render_cluster_orchestration_reply(result: ClusterResultLike) -> str:
    if not result.task_results:
        return result.error or "Cluster orchestration finished without task output."
    lines: list[str] = []
    for task_result in result.task_results:
        if task_result.success:
            lines.append(f"[{task_result.agent_name}] {task_result.text}".strip())
        else:
            lines.append(f"[{task_result.agent_name}] 执行失败：{task_result.error}")
    if result.error and result.success is False:
        lines.append(f"Cluster stopped: {result.error}")
    return "\n\n".join(line for line in lines if line).strip()


def render_cluster_user_reply(result: ClusterResultLike) -> str:
    if not result.task_results:
        return result.error or "Cluster orchestration finished without task output."
    if not result.success:
        failed = next((item for item in reversed(result.task_results) if not item.success), None)
        return (failed.error if failed else result.error) or "Cluster stopped before producing a final answer."

    final_result = _select_user_facing_result(result.task_results)
    text = _strip_role_prefix(final_result.text).strip()
    return _render_structured_user_text(text) or text


def _select_user_facing_result(results: tuple[ClusterTaskResultLike, ...]) -> ClusterTaskResultLike:
    non_review_results = [
        item for item in results
        if item.success and item.agent_name.lower() not in {"planner", "reviewer"}
    ]
    if non_review_results:
        return non_review_results[-1]
    successful_results = [item for item in results if item.success]
    return successful_results[-1] if successful_results else results[-1]


def _strip_role_prefix(text: str) -> str:
    stripped = text.strip()
    for role in ("planner", "executor", "reviewer"):
        prefix = f"[{role}]"
        if stripped.lower().startswith(prefix):
            return stripped[len(prefix):].strip()
    return stripped


def _render_structured_user_text(text: str) -> str:
    payload = _load_json_object(text)
    if not isinstance(payload, dict):
        return ""
    weather = _render_weather_payload(payload)
    if weather:
        return weather
    return _render_generic_json_payload(payload)


def _load_json_object(text: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None


def _render_weather_payload(payload: dict[str, Any]) -> str:
    city = payload.get("city")
    weather_date = payload.get("weather_date") or payload.get("date")
    forecast = payload.get("forecast")
    if not city or not isinstance(forecast, dict):
        return ""

    title = f"{city}{weather_date}天气：" if weather_date else f"{city}天气："
    lines = [title]
    for key, value in forecast.items():
        if isinstance(value, (dict, list)):
            continue
        lines.append(f"- {key}：{value}")
    warning = payload.get("risk_warning") or payload.get("warning") or payload.get("tips")
    if warning:
        lines.append("")
        lines.append(str(warning))
    source = payload.get("data_source")
    if source:
        lines.append("")
        lines.append(f"数据来源：{source}")
    return "\n".join(lines).strip()

def _render_generic_json_payload(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, value in payload.items():
        if isinstance(value, dict):
            lines.append(f"{key}：")
            for child_key, child_value in value.items():
                if isinstance(child_value, (dict, list)):
                    continue
                lines.append(f"- {child_key}：{child_value}")
        elif isinstance(value, list):
            if not value:
                continue
            lines.append(f"{key}：")
            for item in value[:8]:
                lines.append(f"- {item}")
        else:
            lines.append(f"{key}：{value}")
    return "\n".join(lines).strip()
