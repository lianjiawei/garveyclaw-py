from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ToolConfirmationRequest:
    tool_name: str
    summary: str
    prompt: str
    risk_level: str
    category: str
    session_scope: str = ""
    allow_session_grant: bool = False


@dataclass(frozen=True, slots=True)
class SessionToolGrant:
    session_scope: str
    tool_name: str
    risk_level: str
    category: str
    granted_at: str


@dataclass(slots=True)
class PendingToolConfirmation:
    target_id: str
    request: ToolConfirmationRequest
    future: asyncio.Future[bool]


class ToolConfirmationHandler(Protocol):
    async def confirm_tool_use(self, target_id: str, request: ToolConfirmationRequest) -> bool: ...


_PENDING_CONFIRMATIONS: dict[str, PendingToolConfirmation] = {}
_SESSION_TOOL_GRANTS: dict[str, dict[str, SessionToolGrant]] = {}
_GRANT_LOCK = Lock()


def normalize_confirmation_reply(text: str) -> str | None:
    normalized = text.strip().lower()
    if normalized.startswith(("本会话允许", "会话允许", "session allow", "allow session", "session yes", "session:y", "session y")):
        return "approve_session"
    if normalized in {"y", "yes", "ok", "true", "1", "确认", "同意", "允许", "是", "好的", "继续", "执行"}:
        return "approve_once"
    if normalized in {"n", "no", "false", "0", "取消", "拒绝", "不", "否", "不用", "停止"}:
        return "reject"
    return None


def has_session_tool_grant(session_scope: str | None, tool_name: str) -> bool:
    if not session_scope:
        return False
    with _GRANT_LOCK:
        return tool_name in _SESSION_TOOL_GRANTS.get(str(session_scope), {})


def grant_session_tool_access(session_scope: str | None, request: ToolConfirmationRequest) -> bool:
    if not session_scope or not request.allow_session_grant:
        return False
    grant = SessionToolGrant(
        session_scope=str(session_scope),
        tool_name=request.tool_name,
        risk_level=request.risk_level,
        category=request.category,
        granted_at=datetime.now().isoformat(timespec="seconds"),
    )
    with _GRANT_LOCK:
        bucket = _SESSION_TOOL_GRANTS.setdefault(grant.session_scope, {})
        bucket[grant.tool_name] = grant
    return True


def list_session_tool_grants(session_scope: str | None) -> list[SessionToolGrant]:
    if not session_scope:
        return []
    with _GRANT_LOCK:
        grants = list(_SESSION_TOOL_GRANTS.get(str(session_scope), {}).values())
    return sorted(grants, key=lambda item: (item.category, item.tool_name))


def revoke_session_tool_grant(session_scope: str | None, tool_name: str) -> bool:
    if not session_scope or not tool_name:
        return False
    with _GRANT_LOCK:
        bucket = _SESSION_TOOL_GRANTS.get(str(session_scope))
        if not bucket or tool_name not in bucket:
            return False
        bucket.pop(tool_name, None)
        if not bucket:
            _SESSION_TOOL_GRANTS.pop(str(session_scope), None)
    return True


def clear_session_tool_grants(session_scope: str | None) -> int:
    if not session_scope:
        return 0
    with _GRANT_LOCK:
        bucket = _SESSION_TOOL_GRANTS.pop(str(session_scope), None)
    return len(bucket or {})


def get_pending_confirmation(target_id: str | int) -> PendingToolConfirmation | None:
    return _PENDING_CONFIRMATIONS.get(str(target_id))


def register_pending_confirmation(target_id: str | int, request: ToolConfirmationRequest) -> asyncio.Future[bool]:
    key = str(target_id)
    existing = _PENDING_CONFIRMATIONS.get(key)
    if existing is not None and not existing.future.done():
        raise RuntimeError(f"A tool confirmation is already pending for target {key}.")
    future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
    _PENDING_CONFIRMATIONS[key] = PendingToolConfirmation(target_id=key, request=request, future=future)
    return future


def resolve_pending_confirmation(target_id: str | int, approved: bool) -> bool:
    key = str(target_id)
    pending = _PENDING_CONFIRMATIONS.pop(key, None)
    if pending is None:
        return False
    if not pending.future.done():
        pending.future.set_result(bool(approved))
    return True


def cancel_pending_confirmation(target_id: str | int) -> bool:
    key = str(target_id)
    pending = _PENDING_CONFIRMATIONS.pop(key, None)
    if pending is None:
        return False
    if not pending.future.done():
        pending.future.cancel()
    return True


async def wait_for_pending_confirmation(target_id: str | int, timeout_seconds: float = 300.0) -> bool:
    key = str(target_id)
    pending = _PENDING_CONFIRMATIONS.get(key)
    if pending is None:
        raise RuntimeError(f"No pending tool confirmation for target {key}.")
    try:
        return bool(await asyncio.wait_for(pending.future, timeout=timeout_seconds))
    except asyncio.TimeoutError:
        cancel_pending_confirmation(key)
        raise
    finally:
        if pending.future.done() and _PENDING_CONFIRMATIONS.get(key) is pending:
            _PENDING_CONFIRMATIONS.pop(key, None)


async def request_tool_confirmation(handler: object, target_id: str | int, request: ToolConfirmationRequest) -> bool | None:
    confirm = getattr(handler, "confirm_tool_use", None)
    if confirm is None:
        return None
    return bool(await confirm(str(target_id), request))
