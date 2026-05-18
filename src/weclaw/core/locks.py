from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import threading
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RuntimeLockEntry:
    lock: threading.Lock
    holders: int = 0
    waiting: int = 0
    last_used_monotonic: float = 0.0


_LOCKS_GUARD = threading.Lock()
_RUNTIME_LOCKS: dict[str, RuntimeLockEntry] = {}


def _normalize_lock_key(session_scope: str | None) -> str:
    return session_scope or "global"


def _get_runtime_lock_entry(lock_key: str) -> RuntimeLockEntry:
    with _LOCKS_GUARD:
        entry = _RUNTIME_LOCKS.get(lock_key)
        if entry is None:
            entry = RuntimeLockEntry(lock=threading.Lock(), last_used_monotonic=time.monotonic())
            _RUNTIME_LOCKS[lock_key] = entry
        return entry


def _mark_waiting(lock_key: str) -> RuntimeLockEntry:
    with _LOCKS_GUARD:
        entry = _RUNTIME_LOCKS.get(lock_key)
        if entry is None:
            entry = RuntimeLockEntry(lock=threading.Lock(), last_used_monotonic=time.monotonic())
            _RUNTIME_LOCKS[lock_key] = entry
        entry.waiting += 1
        entry.last_used_monotonic = time.monotonic()
        return entry


def _mark_acquired(lock_key: str) -> None:
    with _LOCKS_GUARD:
        entry = _RUNTIME_LOCKS[lock_key]
        entry.waiting -= 1
        entry.holders += 1
        entry.last_used_monotonic = time.monotonic()


def _release_runtime_lock(lock_key: str) -> None:
    with _LOCKS_GUARD:
        entry = _RUNTIME_LOCKS[lock_key]
        entry.holders -= 1
        entry.last_used_monotonic = time.monotonic()
        entry.lock.release()
        if entry.holders == 0 and entry.waiting == 0 and not entry.lock.locked():
            _RUNTIME_LOCKS.pop(lock_key, None)


def get_runtime_lock_stats() -> dict[str, dict[str, float | int | bool]]:
    with _LOCKS_GUARD:
        return {
            key: {
                "holders": entry.holders,
                "waiting": entry.waiting,
                "locked": entry.lock.locked(),
                "last_used_monotonic": entry.last_used_monotonic,
            }
            for key, entry in _RUNTIME_LOCKS.items()
        }


@asynccontextmanager
async def acquire_runtime_lock(session_scope: str | None, operation: str) -> AsyncIterator[None]:
    lock_key = _normalize_lock_key(session_scope)
    entry = _mark_waiting(lock_key)
    wait_started = time.perf_counter()
    logger.info("Waiting for runtime lock: operation=%s key=%s", operation, lock_key)
    await asyncio.to_thread(entry.lock.acquire)
    _mark_acquired(lock_key)
    waited = time.perf_counter() - wait_started
    logger.info("Acquired runtime lock: operation=%s key=%s wait_seconds=%.3f", operation, lock_key, waited)
    try:
        yield
    finally:
        _release_runtime_lock(lock_key)
        logger.info("Released runtime lock: operation=%s key=%s", operation, lock_key)
