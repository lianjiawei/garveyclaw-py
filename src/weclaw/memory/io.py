from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

if os.name == "nt":
    import msvcrt
else:
    import fcntl


T = TypeVar("T")


def _lock_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.lock")


@contextlib.contextmanager
def locked_path(path: Path) -> Iterator[None]:
    """Serialize memory file access across channel handlers and scheduler jobs."""

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = _lock_path(path)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with lock_file.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_text_unlocked(path: Path, default: str = "", *, errors: str = "strict") -> str:
    if not path.exists():
        return default
    return path.read_text(encoding="utf-8", errors=errors)


def write_text_atomic_unlocked(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = f".{path.name}.{uuid4().hex}.tmp"
    temp_path = path.with_name(temp_name)
    try:
        with temp_path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp_path.unlink()


def read_text_locked(path: Path, default: str = "", *, errors: str = "strict") -> str:
    with locked_path(path):
        return read_text_unlocked(path, default, errors=errors)


def write_text_atomic(path: Path, text: str) -> None:
    with locked_path(path):
        write_text_atomic_unlocked(path, text)


def append_text_locked(path: Path, text: str) -> None:
    with locked_path(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())


def unlink_locked(path: Path) -> None:
    with locked_path(path):
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def read_json_locked(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    with locked_path(path):
        if not path.exists():
            return dict(fallback)
        try:
            data = json.loads(read_text_unlocked(path))
        except (OSError, json.JSONDecodeError):
            return dict(fallback)
    return data if isinstance(data, dict) else dict(fallback)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2))


def update_json_locked(path: Path, fallback: dict[str, Any], updater: Callable[[dict[str, Any]], tuple[dict[str, Any], T]]) -> T:
    with locked_path(path):
        if path.exists():
            try:
                current = json.loads(read_text_unlocked(path))
            except (OSError, json.JSONDecodeError):
                current = dict(fallback)
            if not isinstance(current, dict):
                current = dict(fallback)
        else:
            current = dict(fallback)
        next_payload, result = updater(current)
        write_text_atomic_unlocked(path, json.dumps(next_payload, ensure_ascii=False, indent=2))
        return result


def update_text_locked(path: Path, updater: Callable[[str], tuple[str | None, T]], default: str = "") -> T:
    with locked_path(path):
        current = read_text_unlocked(path, default)
        next_text, result = updater(current)
        if next_text is not None:
            write_text_atomic_unlocked(path, next_text)
        return result
