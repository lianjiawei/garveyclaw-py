from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from weclaw.config import DATA_DIR
from weclaw.decision.models import CapabilityCandidate, DecisionPlan, ExecutionOutcome

WORKFLOW_CANDIDATES_DIR = DATA_DIR / "learning" / "workflow_candidates"


def _normalize_name(seed: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "_" for char in seed)
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    if not cleaned:
        cleaned = "workflow_candidate"
    if cleaned[0].isdigit():
        cleaned = f"workflow_{cleaned}"
    return cleaned[:64]


def _candidate_path(candidate_id: str) -> Path:
    WORKFLOW_CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    return WORKFLOW_CANDIDATES_DIR / f"{candidate_id}.json"


def _extract_input_keys(steps: list[dict[str, Any]]) -> list[str]:
    keys: list[str] = []
    for step in steps:
        for key in step.get("input_keys") or []:
            if key not in keys:
                keys.append(key)
    return keys


def _build_step_payloads(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for index, step in enumerate(steps, 1):
        payloads.append(
            {
                "name": f"step_{index}_{step['name']}",
                "tool_name": step["name"],
                "arguments_from_input": list(step.get("input_keys") or []),
            }
        )
    return payloads


def _is_candidate_eligible(outcome: ExecutionOutcome, trace_details: dict[str, Any]) -> tuple[bool, str]:
    if not outcome.success or outcome.waiting_for_user:
        return False, "本轮未形成稳定成功执行"
    if outcome.used_workflows:
        return False, "已经使用了正式 workflow"
    steps = trace_details.get("steps") or []
    if len(steps) < 2:
        return False, "工具链过短，不足以沉淀 workflow"
    if any(str(step.get("risk_level") or "") == "destructive" for step in steps):
        return False, "包含 destructive 工具，禁止自动沉淀"
    if any(not step.get("success") for step in steps):
        return False, "存在失败步骤，不适合沉淀"
    return True, "满足候选 workflow 生成条件"


def _build_candidate_payload(
    *,
    session_scope: str | None,
    provider: str,
    prompt: str,
    plan: DecisionPlan,
    outcome: ExecutionOutcome,
    trace_details: dict[str, Any],
) -> dict[str, Any]:
    steps = trace_details.get("steps") or []
    normalized_steps: list[dict[str, Any]] = []
    for step in steps:
        touched_files = [str(item).strip() for item in step.get("touched_files") or [] if str(item).strip()]
        step_name = str(step.get("name") or "").strip()
        input_keys: list[str] = []
        if step_name in ("read_workspace_file", "write_workspace_file", "edit_workspace_file", "glob_workspace_files", "grep_workspace_content", "send_file"):
            input_keys.append("path")
        if step_name == "bash":
            input_keys.append("workdir")
        if touched_files and "path" not in input_keys:
            input_keys.append("path")
        normalized_steps.append(
            {
                "name": str(step.get("name") or "").strip(),
                "category": str(step.get("category") or "").strip(),
                "risk_level": str(step.get("risk_level") or "").strip(),
                "summary": str(step.get("summary") or "").strip(),
                "input_keys": input_keys,
            }
        )

    seed = f"{plan.intent_type}_{'_'.join(item['name'] for item in normalized_steps[:3])}"
    base_name = _normalize_name(seed)
    identity_text = f"{plan.intent_type}|{plan.strategy}|{'|'.join(item['name'] for item in normalized_steps)}"
    candidate_id = hashlib.sha1(identity_text.encode("utf-8")).hexdigest()[:12]
    input_keys = _extract_input_keys(normalized_steps)
    parameter_properties = {key: {"type": "string", "description": f"从执行轨迹推断的输入参数 {key}"} for key in input_keys}

    return {
        "id": candidate_id,
        "name": f"candidate_{base_name}",
        "description": f"从成功执行轨迹沉淀的候选 workflow：{plan.task_intent.goal[:80]}",
        "status": "draft",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "session_scope": session_scope or "",
        "provider": provider,
        "intent_type": plan.intent_type,
        "strategy": plan.strategy,
        "source_prompt": prompt,
        "goal": plan.task_intent.goal,
        "tool_sequence": list(outcome.tool_sequence),
        "used_tools": list(outcome.used_tools),
        "touched_files": list(outcome.touched_files),
        "success_count": 1,
        "confidence": round(max(plan.task_intent.confidence, 0.55), 2),
        "workflow_definition": {
            "name": f"candidate_{base_name}",
            "description": f"候选 workflow：{plan.task_intent.goal[:80]}",
            "category": "workflows",
            "providers": [provider],
            "risk_level": "normal",
            "parameters": {
                "type": "object",
                "properties": parameter_properties,
                "required": input_keys,
            },
            "steps": _build_step_payloads(normalized_steps),
            "finalizer": "last_step_text",
        },
    }


def persist_workflow_candidate(
    *,
    session_scope: str | None,
    provider: str,
    prompt: str,
    plan: DecisionPlan,
    outcome: ExecutionOutcome,
    trace_details: dict[str, Any],
) -> str | None:
    eligible, _reason = _is_candidate_eligible(outcome, trace_details)
    if not eligible:
        return None
    payload = _build_candidate_payload(
        session_scope=session_scope,
        provider=provider,
        prompt=prompt,
        plan=plan,
        outcome=outcome,
        trace_details=trace_details,
    )
    path = _candidate_path(str(payload["id"]))
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        payload["success_count"] = int(existing.get("success_count") or 1) + 1
        payload["created_at"] = str(existing.get("created_at") or payload["created_at"])
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def load_workflow_candidates(provider: str | None = None) -> list[dict[str, Any]]:
    if not WORKFLOW_CANDIDATES_DIR.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(WORKFLOW_CANDIDATES_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        candidate_provider = str(payload.get("provider") or "").strip().lower()
        if provider and candidate_provider and candidate_provider != provider:
            continue
        items.append(payload)
    return items


def load_workflow_candidate_capabilities(provider: str | None = None) -> list[CapabilityCandidate]:
    candidates: list[CapabilityCandidate] = []
    for payload in load_workflow_candidates(provider):
        workflow_definition = payload.get("workflow_definition") or {}
        name = str(payload.get("name") or workflow_definition.get("name") or "").strip()
        description = str(payload.get("description") or workflow_definition.get("description") or "").strip()
        if not name or not description:
            continue
        confidence = float(payload.get("confidence") or 0.0)
        success_count = int(payload.get("success_count") or 1)
        score = round(confidence * 4 + min(success_count, 5), 2)
        reasons = [f"candidate_confidence:{confidence:.2f}", f"candidate_success_count:{success_count}"]
        candidates.append(
            CapabilityCandidate(
                kind="workflow",
                name=name,
                title=name,
                description=description,
                category="workflow_candidates",
                risk_level=str(workflow_definition.get("risk_level") or "normal"),
                provider_support=frozenset({str(payload.get('provider') or provider or 'openai').strip().lower()}),
                source=str(payload.get("id") or name),
                score=score,
                reasons=tuple(reasons),
            )
        )
    return candidates
