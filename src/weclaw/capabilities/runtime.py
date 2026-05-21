from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable

from weclaw.config import CAPABILITY_WATCHER_ENABLED, CAPABILITY_WATCHER_INTERVAL_SECONDS

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BackgroundCapabilityWatcherRuntime:
    thread: threading.Thread
    stop_event: threading.Event
    interval_seconds: float


def start_background_capability_watcher(
    *,
    enabled: bool | None = None,
    interval_seconds: float | None = None,
    refresh: Callable[[], object] | None = None,
) -> BackgroundCapabilityWatcherRuntime | None:
    if enabled is None:
        enabled = CAPABILITY_WATCHER_ENABLED
    if interval_seconds is None:
        interval_seconds = CAPABILITY_WATCHER_INTERVAL_SECONDS
    if not enabled or interval_seconds <= 0:
        return None

    refresh_fn = refresh
    if refresh_fn is None:
        from weclaw.capabilities.tools import refresh_tool_registry_if_needed

        refresh_fn = refresh_tool_registry_if_needed

    stop_event = threading.Event()

    def run_loop() -> None:
        logger.info("Capability watcher started: interval=%ss", interval_seconds)
        while not stop_event.wait(interval_seconds):
            try:
                refresh_fn()
            except Exception:
                logger.exception("Capability watcher refresh failed")
        logger.info("Capability watcher stopped")

    thread = threading.Thread(target=run_loop, daemon=True, name="weclaw-capability-watcher")
    thread.start()
    return BackgroundCapabilityWatcherRuntime(thread=thread, stop_event=stop_event, interval_seconds=float(interval_seconds))


def stop_background_capability_watcher(runtime: BackgroundCapabilityWatcherRuntime | None) -> None:
    if runtime is None:
        return
    runtime.stop_event.set()
    runtime.thread.join(timeout=max(1.0, runtime.interval_seconds + 1.0))
    if runtime.thread.is_alive():
        logger.warning("Capability watcher thread did not exit promptly")
