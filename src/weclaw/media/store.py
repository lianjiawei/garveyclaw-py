from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from telegram import Message

from weclaw.config import UPLOAD_FILES_DIR, UPLOAD_MAX_FILE_SIZE_BYTES, UPLOAD_VOICES_DIR


@dataclass(slots=True)
class PhotoPayload:
    """图片内存载荷，直接交给后续模型处理。"""

    data: bytes
    mime_type: str


@dataclass(slots=True)
class FilePayload:
    """通用文件载荷，保存到磁盘供 Agent 读取。"""

    data: bytes
    file_name: str
    mime_type: str
    saved_path: Path


def _build_upload_name(prefix: str, suffix: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{timestamp}_{uuid4().hex[:8]}{suffix}"


async def load_photo_message(message: Message) -> PhotoPayload:
    """把 Telegram 图片下载到内存，避免先保存到磁盘。"""

    if not message.photo:
        raise ValueError("Message does not contain a photo.")

    photo = message.photo[-1]
    telegram_file = await photo.get_file()
    data = await telegram_file.download_as_bytearray()
    return PhotoPayload(data=bytes(data), mime_type="image/jpeg")


async def save_voice_message(message: Message) -> Path:
    """语音消息暂时仍然落盘，供 ffmpeg 和 ASR 后续处理。"""

    if not message.voice:
        raise ValueError("Message does not contain a voice.")

    telegram_file = await message.voice.get_file()
    file_path = UPLOAD_VOICES_DIR / _build_upload_name("voice", ".ogg")
    await telegram_file.download_to_drive(custom_path=str(file_path))
    return file_path


def save_voice_bytes(raw_data: bytes, suffix: str = ".ogg") -> Path:
    """将语音字节保存到语音目录，返回文件路径。"""

    file_path = UPLOAD_VOICES_DIR / _build_upload_name("voice", suffix)
    file_path.write_bytes(raw_data)
    return file_path


def _sanitize_filename(name: str) -> str:
    """去除路径分隔符，保留扩展名。"""
    base = os.path.basename(name).strip() or "file"
    return base.replace("\\", "_").replace("/", "_")


def save_uploaded_file(raw_data: bytes, original_name: str, mime_type: str) -> FilePayload:
    """将上传文件保存到上传目录，返回载荷。"""

    if len(raw_data) > UPLOAD_MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"文件大小 {len(raw_data)} 字节超过限制 {UPLOAD_MAX_FILE_SIZE_BYTES} 字节"
        )

    safe_name = _sanitize_filename(original_name)
    stem, _, ext = safe_name.rpartition(".")
    unique_name = _build_upload_name(stem, f".{ext}" if ext else "")
    saved_path = UPLOAD_FILES_DIR / unique_name
    saved_path.write_bytes(raw_data)

    return FilePayload(
        data=raw_data,
        file_name=original_name,
        mime_type=mime_type,
        saved_path=saved_path,
    )
