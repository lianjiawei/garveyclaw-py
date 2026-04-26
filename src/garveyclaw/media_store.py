from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from telegram import Message

from garveyclaw.config import UPLOAD_VOICES_DIR


@dataclass(slots=True)
class PhotoPayload:
    """图片内存载荷，直接交给后续模型处理。"""

    data: bytes
    mime_type: str


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
