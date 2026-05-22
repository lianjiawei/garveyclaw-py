from __future__ import annotations

import asyncio
import argparse
import base64
import hashlib
import json
import logging
import mimetypes
import secrets
import struct
import time
import tempfile
import uuid
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import aiohttp
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from weclaw.agents.router import AgentServiceError, build_weixin_conversation
from weclaw.agents.runtime import run_agent_for_conversation
from weclaw.config import (
    DATA_DIR,
    PROJECT_ROOT,
    WEIXIN_ACCOUNT_ID,
    WEIXIN_ALLOWED_USERS,
    WEIXIN_BASE_URL,
    WEIXIN_CDN_BASE_URL,
    WEIXIN_DM_POLICY,
    WEIXIN_GROUP_ALLOWED_USERS,
    WEIXIN_GROUP_POLICY,
    WEIXIN_LONG_POLL_TIMEOUT_MS,
    WEIXIN_RESTART_DELAY_SECONDS,
    WEIXIN_SEND_CHUNK_DELAY_SECONDS,
    WEIXIN_SEND_CHUNK_RETRIES,
    WEIXIN_SEND_CHUNK_RETRY_DELAY_SECONDS,
    WEIXIN_TOKEN,
)
from weclaw.core.confirmation import (
    ToolConfirmationRequest,
    get_pending_confirmation,
    grant_session_tool_access,
    normalize_confirmation_reply,
    register_pending_confirmation,
    resolve_pending_confirmation,
    wait_for_pending_confirmation,
)
from weclaw.core.response import AgentReply
from weclaw.media.speech import SpeechRecognitionError, transcribe_voice
from weclaw.media.store import FilePayload, PhotoPayload, save_uploaded_file, save_voice_bytes
from weclaw.memory.session import clear_session_id
from weclaw.tasks.service import handle_task_command

logger = logging.getLogger(__name__)

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
ILINK_APP_ID = "bot"
ILINK_APP_CLIENT_VERSION = (2 << 16) | (2 << 8) | 0
CHANNEL_VERSION = "2.2.0"
CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"

EP_GET_UPDATES = "ilink/bot/getupdates"
EP_SEND_MESSAGE = "ilink/bot/sendmessage"
EP_GET_UPLOAD_URL = "ilink/bot/getuploadurl"
EP_GET_BOT_QR = "ilink/bot/get_bot_qrcode"
EP_GET_QR_STATUS = "ilink/bot/get_qrcode_status"

API_TIMEOUT_MS = 15_000
QR_TIMEOUT_MS = 35_000
MAX_MESSAGE_LENGTH = 2000
MESSAGE_DEDUP_TTL_SECONDS = 300
SESSION_EXPIRED_ERRCODE = -14
RATE_LIMIT_ERRCODE = -2

ITEM_TEXT = 1
ITEM_IMAGE = 2
ITEM_VOICE = 3
ITEM_FILE = 4
ITEM_VIDEO = 5
MEDIA_IMAGE = 1
MEDIA_VIDEO = 2
MEDIA_FILE = 3
MEDIA_VOICE = 4
MSG_TYPE_BOT = 2
MSG_STATE_FINISH = 2


def _json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _random_wechat_uin() -> str:
    value = struct.unpack(">I", secrets.token_bytes(4))[0]
    return base64.b64encode(str(value).encode("utf-8")).decode("ascii")


def _headers(token: str | None, body: str) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Content-Length": str(len(body.encode("utf-8"))),
        "X-WECHAT-UIN": _random_wechat_uin(),
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def _api_post(
    session: aiohttp.ClientSession,
    *,
    base_url: str,
    endpoint: str,
    payload: dict[str, Any],
    token: str | None,
    timeout_ms: int = API_TIMEOUT_MS,
) -> dict[str, Any]:
    body = _json_dumps({**payload, "base_info": {"channel_version": CHANNEL_VERSION}})
    url = f"{base_url.rstrip('/')}/{endpoint}"
    timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000)
    async with session.post(url, data=body, headers=_headers(token, body), timeout=timeout) as response:
        raw = await response.text()
        if not response.ok:
            raise RuntimeError(f"Weixin iLink POST {endpoint} HTTP {response.status}: {raw[:200]}")
        return json.loads(raw) if raw else {}


async def _api_get(
    session: aiohttp.ClientSession,
    *,
    base_url: str,
    endpoint: str,
    timeout_ms: int,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{endpoint}"
    headers = {
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }
    timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000)
    async with session.get(url, headers=headers, timeout=timeout) as response:
        raw = await response.text()
        if not response.ok:
            raise RuntimeError(f"Weixin iLink GET {endpoint} HTTP {response.status}: {raw[:200]}")
        return json.loads(raw) if raw else {}


async def _get_updates(
    session: aiohttp.ClientSession,
    *,
    base_url: str,
    token: str,
    sync_buf: str,
    timeout_ms: int,
) -> dict[str, Any]:
    try:
        return await _api_post(
            session,
            base_url=base_url,
            endpoint=EP_GET_UPDATES,
            payload={"get_updates_buf": sync_buf},
            token=token,
            timeout_ms=timeout_ms,
        )
    except asyncio.TimeoutError:
        return {"ret": 0, "msgs": [], "get_updates_buf": sync_buf}


async def _send_message(
    session: aiohttp.ClientSession,
    *,
    base_url: str,
    token: str,
    to_user_id: str,
    text: str,
    context_token: str | None,
    client_id: str,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "from_user_id": "",
        "to_user_id": to_user_id,
        "client_id": client_id,
        "message_type": MSG_TYPE_BOT,
        "message_state": MSG_STATE_FINISH,
        "item_list": [{"type": ITEM_TEXT, "text_item": {"text": text}}],
    }
    if context_token:
        message["context_token"] = context_token
    return await _api_post(
        session,
        base_url=base_url,
        endpoint=EP_SEND_MESSAGE,
        payload={"msg": message},
        token=token,
    )


def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def _aes128_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    encryptor = cipher.encryptor()
    return encryptor.update(_pkcs7_pad(plaintext)) + encryptor.finalize()


def _aes128_ecb_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    if not padded:
        return padded
    pad_len = padded[-1]
    if 1 <= pad_len <= 16 and padded.endswith(bytes([pad_len]) * pad_len):
        return padded[:-pad_len]
    return padded


def _aes_padded_size(size: int) -> int:
    return ((size + 1 + 15) // 16) * 16


def _cdn_download_url(cdn_base_url: str, encrypted_query_param: str) -> str:
    return f"{cdn_base_url.rstrip('/')}/download?encrypted_query_param={quote(encrypted_query_param, safe='')}"


def _cdn_upload_url(cdn_base_url: str, upload_param: str, filekey: str) -> str:
    return (
        f"{cdn_base_url.rstrip('/')}/upload"
        f"?encrypted_query_param={quote(upload_param, safe='')}"
        f"&filekey={quote(filekey, safe='')}"
    )


async def _upload_ciphertext(session: aiohttp.ClientSession, *, ciphertext: bytes, upload_url: str) -> str:
    async with session.post(upload_url, data=ciphertext, headers={"Content-Type": "application/octet-stream"}) as response:
        if response.status == 200:
            encrypted_param = response.headers.get("x-encrypted-param")
            if encrypted_param:
                await response.read()
                return encrypted_param
        raw = await response.text()
        raise RuntimeError(f"Weixin CDN upload failed HTTP {response.status}: {raw[:200]}")


async def _download_bytes(session: aiohttp.ClientSession, *, url: str, timeout_seconds: float = 60.0) -> bytes:
    async def do_download() -> bytes:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.read()

    return await asyncio.wait_for(do_download(), timeout=timeout_seconds)


WEIXIN_MEDIA_HOST_ALLOWLIST: frozenset[str] = frozenset(
    {
        "novac2c.cdn.weixin.qq.com",
        "ilinkai.weixin.qq.com",
        "wx.qlogo.cn",
        "thirdwx.qlogo.cn",
        "res.wx.qq.com",
        "mmbiz.qpic.cn",
        "mmbiz.qlogo.cn",
    }
)


def _assert_weixin_media_url(url: str) -> None:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    host = parsed.hostname or ""
    if scheme not in {"http", "https"}:
        raise ValueError(f"Weixin media URL has unsupported scheme: {scheme}")
    if host not in WEIXIN_MEDIA_HOST_ALLOWLIST:
        raise ValueError(f"Weixin media URL host is not allowed: {host}")


def _parse_aes_key(aes_key_value: str) -> bytes:
    value = str(aes_key_value or "").strip()
    if not value:
        raise ValueError("empty aes_key")
    if len(value) == 32 and all(ch in "0123456789abcdefABCDEF" for ch in value):
        return bytes.fromhex(value)
    decoded = base64.b64decode(value)
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        text = decoded.decode("ascii", errors="ignore")
        if text and all(ch in "0123456789abcdefABCDEF" for ch in text):
            return bytes.fromhex(text)
    raise ValueError(f"unexpected aes_key format: {len(decoded)} decoded bytes")


def _media_reference(item: dict[str, Any], item_key: str) -> dict[str, Any]:
    return (item.get(item_key) or {}).get("media") or {}


def _has_downloadable_media(media: dict[str, Any]) -> bool:
    return bool(
        media.get("encrypt_query_param")
        or media.get("encrypted_query_param")
        or media.get("full_url")
        or media.get("url")
    )


def _media_encrypt_query_param(media: dict[str, Any]) -> str | None:
    value = media.get("encrypt_query_param") or media.get("encrypted_query_param")
    return str(value) if value else None


def _media_full_url(media: dict[str, Any]) -> str | None:
    value = media.get("full_url") or media.get("url")
    return str(value) if value else None


def _media_suffix(*values: Any, default: str) -> str:
    for value in values:
        text = str(value or "").strip().lower()
        if not text:
            continue
        if text.startswith(".") and 1 < len(text) <= 8:
            return text
        guessed = mimetypes.guess_extension(text)
        if guessed:
            return guessed
        if "/" in text:
            subtype = text.rsplit("/", 1)[-1]
            if subtype in {"silk", "amr", "ogg", "opus", "mp3", "wav", "m4a"}:
                return f".{subtype}"
        if text in {"silk", "amr", "ogg", "opus", "mp3", "wav", "m4a"}:
            return f".{text}"
    return default


async def _download_and_decrypt_media(
    session: aiohttp.ClientSession,
    *,
    cdn_base_url: str,
    encrypted_query_param: str | None,
    aes_key: str | None,
    full_url: str | None,
    timeout_seconds: float,
) -> bytes:
    if encrypted_query_param:
        raw = await _download_bytes(
            session,
            url=_cdn_download_url(cdn_base_url, encrypted_query_param),
            timeout_seconds=timeout_seconds,
        )
    elif full_url:
        _assert_weixin_media_url(full_url)
        raw = await _download_bytes(session, url=full_url, timeout_seconds=timeout_seconds)
    else:
        raise RuntimeError("Weixin media item had neither encrypt_query_param nor full_url.")
    if aes_key:
        raw = _aes128_ecb_decrypt(raw, _parse_aes_key(aes_key))
    return raw


def _is_stale_session_ret(ret: Any, errcode: Any, errmsg: Any) -> bool:
    if ret != RATE_LIMIT_ERRCODE and errcode != RATE_LIMIT_ERRCODE:
        return False
    return str(errmsg or "").lower() == "unknown error"


def _is_success_response(response: dict[str, Any]) -> bool:
    ret = response.get("ret", 0)
    errcode = response.get("errcode", 0)
    return ret in {0, None} and errcode in {0, None}


def _extract_text(item_list: list[dict[str, Any]]) -> str:
    for item in item_list:
        if item.get("type") == ITEM_TEXT:
            text = str((item.get("text_item") or {}).get("text") or "")
            ref = item.get("ref_msg") or {}
            ref_item = ref.get("message_item") or {}
            if ref_item:
                ref_text = _extract_text([ref_item])
                if ref_text:
                    return f"[引用: {ref_text}]\n{text}".strip()
            return text
    for item in item_list:
        if item.get("type") == ITEM_VOICE:
            voice_text = str((item.get("voice_item") or {}).get("text") or "")
            if voice_text:
                return voice_text
    return ""


def _guess_chat_type(message: dict[str, Any], account_id: str) -> tuple[str, str]:
    room_id = str(message.get("room_id") or message.get("chat_room_id") or "").strip()
    to_user_id = str(message.get("to_user_id") or "").strip()
    is_group = bool(room_id) or (to_user_id and account_id and to_user_id != account_id and message.get("msg_type") == 1)
    if is_group:
        return "group", room_id or to_user_id or str(message.get("from_user_id") or "")
    return "dm", str(message.get("from_user_id") or "")


def _split_text(text: str, max_length: int = MAX_MESSAGE_LENGTH) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_length:
        return [text]
    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) <= max_length:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(block) > max_length:
            chunks.append(block[:max_length])
            block = block[max_length:]
        current = block
    if current:
        chunks.append(current)
    return chunks


def _coerce_list(value: str | None) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _account_dir() -> Path:
    path = DATA_DIR / "weixin" / "accounts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _account_file(account_id: str) -> Path:
    return _account_dir() / f"{account_id}.json"


def save_weixin_account(*, account_id: str, token: str, base_url: str, user_id: str = "") -> None:
    payload = {
        "token": token,
        "base_url": base_url,
        "user_id": user_id,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path = _account_file(account_id)
    _atomic_write_json(path, payload)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_weixin_account(account_id: str) -> dict[str, Any] | None:
    path = _account_file(account_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_env_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _quote_env_value(value: str) -> str:
    if not value:
        return ""
    if any(char.isspace() for char in value) or "#" in value:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def set_env_values(updates: dict[str, str], path: Path | None = None) -> None:
    path = path or PROJECT_ROOT / ".env"
    lines = _read_env_lines(path)
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue
        key, _value = line.split("=", 1)
        key = key.strip()
        if key in updates:
            output.append(f"{key}={_quote_env_value(updates[key])}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={_quote_env_value(value)}")
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


async def qr_login(
    *,
    bot_type: str = "3",
    timeout_seconds: int = 480,
    write_env: bool = True,
    open_browser: bool = True,
) -> dict[str, str] | None:
    async with aiohttp.ClientSession(trust_env=True) as session:
        try:
            qr_resp = await _api_get(
                session,
                base_url=ILINK_BASE_URL,
                endpoint=f"{EP_GET_BOT_QR}?bot_type={bot_type}",
                timeout_ms=QR_TIMEOUT_MS,
            )
        except Exception as exc:
            logger.error("Weixin QR login failed to fetch QR code: %s", exc)
            return None

        qrcode_value = str(qr_resp.get("qrcode") or "")
        qrcode_url = str(qr_resp.get("qrcode_img_content") or "")
        if not qrcode_value:
            logger.error("Weixin QR response did not include qrcode.")
            return None

        _show_qr(qrcode_url or qrcode_value, open_browser=open_browser)
        deadline = time.monotonic() + timeout_seconds
        current_base_url = ILINK_BASE_URL
        refresh_count = 0

        while time.monotonic() < deadline:
            try:
                status_resp = await _api_get(
                    session,
                    base_url=current_base_url,
                    endpoint=f"{EP_GET_QR_STATUS}?qrcode={qrcode_value}",
                    timeout_ms=QR_TIMEOUT_MS,
                )
            except asyncio.TimeoutError:
                await asyncio.sleep(1)
                continue
            except Exception as exc:
                logger.warning("Weixin QR status poll failed: %s", exc)
                await asyncio.sleep(1)
                continue

            status = str(status_resp.get("status") or "wait")
            if status == "wait":
                print(".", end="", flush=True)
            elif status == "scaned":
                print("\nScanned. Please confirm login in WeChat.")
            elif status == "scaned_but_redirect":
                redirect_host = str(status_resp.get("redirect_host") or "")
                if redirect_host:
                    current_base_url = f"https://{redirect_host}"
            elif status == "expired":
                refresh_count += 1
                if refresh_count > 3:
                    print("\nQR code expired too many times. Please run login again.")
                    return None
                print(f"\nQR code expired. Refreshing... ({refresh_count}/3)")
                qr_resp = await _api_get(
                    session,
                    base_url=ILINK_BASE_URL,
                    endpoint=f"{EP_GET_BOT_QR}?bot_type={bot_type}",
                    timeout_ms=QR_TIMEOUT_MS,
                )
                qrcode_value = str(qr_resp.get("qrcode") or "")
                qrcode_url = str(qr_resp.get("qrcode_img_content") or "")
                if not qrcode_value:
                    return None
                _show_qr(qrcode_url or qrcode_value, open_browser=open_browser)
            elif status == "confirmed":
                account_id = str(status_resp.get("ilink_bot_id") or "")
                token = str(status_resp.get("bot_token") or "")
                base_url = str(status_resp.get("baseurl") or ILINK_BASE_URL)
                user_id = str(status_resp.get("ilink_user_id") or "")
                if not account_id or not token:
                    logger.error("Weixin QR confirmed but credentials were incomplete.")
                    return None
                save_weixin_account(account_id=account_id, token=token, base_url=base_url, user_id=user_id)
                credentials = {"account_id": account_id, "token": token, "base_url": base_url, "user_id": user_id}
                if write_env:
                    set_env_values(
                        {
                            "WEIXIN_ACCOUNT_ID": account_id,
                            "WEIXIN_TOKEN": token,
                            "WEIXIN_BASE_URL": base_url,
                        }
                    )
                print(f"\nWeixin connected. account_id={account_id}")
                return credentials
            await asyncio.sleep(1)

        print("\nWeixin QR login timed out.")
        return None


def _show_qr(scan_data: str, *, open_browser: bool) -> None:
    print("\nUse WeChat to scan this QR code:")
    print(scan_data)
    if open_browser:
        opened = _open_qr_in_browser(scan_data)
        if opened:
            print("Opened a browser QR page. Scan it with WeChat, then confirm login.")
    try:
        import qrcode

        qr = qrcode.QRCode()
        qr.add_data(scan_data)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception as exc:
        print(f"(Could not render terminal QR code: {exc}. Open or scan the URL above instead.)")


def _open_qr_in_browser(scan_data: str) -> bool:
    try:
        import qrcode
        import qrcode.image.svg

        qr = qrcode.QRCode(border=3)
        qr.add_data(scan_data)
        qr.make(fit=True)
        image = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
        svg = image.to_string(encoding="unicode")
        html = f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>WeClaw Weixin Login</title>
    <style>
      body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; font-family: system-ui, sans-serif; background: #f6f8fb; color: #151923; }}
      main {{ text-align: center; padding: 32px; }}
      .qr {{ width: min(78vw, 420px); height: min(78vw, 420px); margin: 0 auto 18px; background: white; padding: 18px; box-shadow: 0 16px 50px rgba(20, 30, 50, 0.16); }}
      .qr svg {{ width: 100%; height: 100%; }}
      p {{ margin: 8px 0; color: #4a5568; }}
      code {{ word-break: break-all; font-size: 12px; color: #68758a; }}
    </style>
  </head>
  <body>
    <main>
      <div class="qr">{svg}</div>
      <h1>WeClaw Weixin Login</h1>
      <p>Use WeChat to scan this code, then confirm login on your phone.</p>
      <p><code>{scan_data}</code></p>
    </main>
  </body>
</html>
"""
        path = Path(tempfile.gettempdir()) / "weclaw-weixin-login.html"
        path.write_text(html, encoding="utf-8")
        return webbrowser.open(path.as_uri())
    except Exception as exc:
        logger.debug("Could not open browser QR page: %s", exc)
        if scan_data.startswith(("http://", "https://")):
            return webbrowser.open(scan_data)
        return False


class ExpiringDeduplicator:
    def __init__(self, ttl_seconds: int = MESSAGE_DEDUP_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._seen: dict[str, float] = {}

    def is_duplicate(self, key: str) -> bool:
        now = time.time()
        expired = [item for item, seen_at in self._seen.items() if now - seen_at > self.ttl_seconds]
        for item in expired:
            self._seen.pop(item, None)
        if key in self._seen:
            return True
        self._seen[key] = now
        return False


class ContextTokenStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (DATA_DIR / "weixin")
        self.root.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, str] = {}

    def restore(self, account_id: str) -> None:
        path = self._path(account_id)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Weixin context token restore failed: %s", exc)
            return
        for user_id, token in data.items():
            if isinstance(token, str) and token:
                self._cache[self._key(account_id, user_id)] = token

    def get(self, account_id: str, user_id: str) -> str | None:
        return self._cache.get(self._key(account_id, user_id))

    def set(self, account_id: str, user_id: str, token: str) -> None:
        self._cache[self._key(account_id, user_id)] = token
        self._persist(account_id)

    def clear(self, account_id: str, user_id: str) -> None:
        self._cache.pop(self._key(account_id, user_id), None)
        self._persist(account_id)

    def _path(self, account_id: str) -> Path:
        return self.root / f"{account_id}.context-tokens.json"

    def _key(self, account_id: str, user_id: str) -> str:
        return f"{account_id}:{user_id}"

    def _persist(self, account_id: str) -> None:
        prefix = f"{account_id}:"
        payload = {key[len(prefix) :]: value for key, value in self._cache.items() if key.startswith(prefix)}
        self._path(account_id).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass(slots=True)
class WeixinConfig:
    account_id: str = WEIXIN_ACCOUNT_ID
    token: str = WEIXIN_TOKEN
    base_url: str = WEIXIN_BASE_URL or ILINK_BASE_URL
    cdn_base_url: str = WEIXIN_CDN_BASE_URL or CDN_BASE_URL
    dm_policy: str = WEIXIN_DM_POLICY
    group_policy: str = WEIXIN_GROUP_POLICY
    allowed_users: tuple[str, ...] = tuple(_coerce_list(WEIXIN_ALLOWED_USERS))
    group_allowed_users: tuple[str, ...] = tuple(_coerce_list(WEIXIN_GROUP_ALLOWED_USERS))


@dataclass(slots=True)
class WeixinInboundMedia:
    photo: PhotoPayload | None = None
    file: FilePayload | None = None
    voice_path: Path | None = None
    voice_text: str = ""
    errors: list[str] | None = None

    def add_error(self, message: str) -> None:
        if self.errors is None:
            self.errors = []
        self.errors.append(message)

    @property
    def has_media(self) -> bool:
        return self.photo is not None or self.file is not None or self.voice_path is not None or bool(self.voice_text)


class WeixinMessageSender:
    def __init__(
        self,
        config: WeixinConfig | None = None,
        *,
        token_store: ContextTokenStore | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.config = config or WeixinConfig()
        self.token_store = token_store or ContextTokenStore()
        if self.config.account_id:
            self.token_store.restore(self.config.account_id)
            if not self.config.token:
                persisted = load_weixin_account(self.config.account_id)
                if persisted:
                    self.config.token = str(persisted.get("token") or "").strip()
                    self.config.base_url = str(persisted.get("base_url") or self.config.base_url).strip()
        self._session = session

    async def send_text(self, target_id: str, text: str) -> None:
        await self._send_text(target_id, text)

    async def send_file(self, target_id: str, file_data: bytes, file_name: str) -> None:
        payload = save_uploaded_file(file_data, file_name, mimetypes.guess_type(file_name)[0] or "application/octet-stream")
        await self._send_file(target_id, payload.saved_path, "")

    async def confirm_tool_use(self, target_id: str, request: ToolConfirmationRequest) -> bool:
        try:
            register_pending_confirmation(target_id, request)
        except RuntimeError:
            await self.send_text(target_id, "当前已有待确认的工具操作，请先回复：允许、本会话允许，或拒绝。")
            return False

        lines = [
            request.prompt,
            f"工具：{request.tool_name}",
            f"类别：{request.category}",
            f"风险：{request.risk_level}",
        ]
        if request.summary:
            lines.append(f"摘要：{request.summary}")
        lines.append("请回复“允许”仅执行一次；回复“本会话允许”后续本会话同工具自动执行；回复“拒绝”取消。")
        await self.send_text(target_id, "\n".join(lines))

        try:
            return await wait_for_pending_confirmation(target_id)
        except TimeoutError:
            await self.send_text(target_id, "工具确认已超时，当前操作已取消。")
            return False

    async def _with_session(self, call):
        if self._session is not None and not self._session.closed:
            return await call(self._session)
        timeout = aiohttp.ClientTimeout(total=None, connect=None, sock_connect=None, sock_read=None)
        async with aiohttp.ClientSession(trust_env=True, timeout=timeout) as session:
            return await call(session)

    async def _send_text(self, target_id: str, text: str) -> None:
        if not self.config.token or not self.config.account_id:
            raise RuntimeError("Weixin channel is missing WEIXIN_TOKEN or WEIXIN_ACCOUNT_ID.")
        context_token = self.token_store.get(self.config.account_id, target_id)
        chunks = _split_text(text)
        for index, chunk in enumerate(chunks):
            await self._send_text_chunk(target_id, chunk, context_token)
            if index < len(chunks) - 1 and WEIXIN_SEND_CHUNK_DELAY_SECONDS > 0:
                await asyncio.sleep(WEIXIN_SEND_CHUNK_DELAY_SECONDS)

    async def _send_text_chunk(self, target_id: str, chunk: str, context_token: str | None) -> None:
        retried_without_token = False
        last_error: Exception | None = None
        for attempt in range(WEIXIN_SEND_CHUNK_RETRIES + 1):
            try:
                async def call(session: aiohttp.ClientSession) -> dict[str, Any]:
                    return await _send_message(
                        session,
                        base_url=self.config.base_url,
                        token=self.config.token,
                        to_user_id=target_id,
                        text=chunk,
                        context_token=context_token,
                        client_id=f"weclaw-weixin-{uuid.uuid4().hex}",
                    )

                response = await self._with_session(call)
                if _is_success_response(response):
                    return
                ret = response.get("ret")
                errcode = response.get("errcode")
                if (ret == SESSION_EXPIRED_ERRCODE or errcode == SESSION_EXPIRED_ERRCODE or _is_stale_session_ret(ret, errcode, response.get("errmsg"))) and context_token and not retried_without_token:
                    retried_without_token = True
                    context_token = None
                    self.token_store.clear(self.config.account_id, target_id)
                    continue
                raise RuntimeError(f"Weixin sendmessage error: {response}")
            except Exception as exc:
                last_error = exc
                if attempt >= WEIXIN_SEND_CHUNK_RETRIES:
                    break
                await asyncio.sleep(WEIXIN_SEND_CHUNK_RETRY_DELAY_SECONDS * (attempt + 1))
        raise last_error or RuntimeError("Weixin sendmessage failed.")

    async def _send_file(self, target_id: str, path: Path, caption: str = "") -> None:
        if not self.config.token or not self.config.account_id:
            raise RuntimeError("Weixin channel is missing WEIXIN_TOKEN or WEIXIN_ACCOUNT_ID.")
        plaintext = path.read_bytes()
        filekey = secrets.token_hex(16)
        aes_key = secrets.token_bytes(16)
        rawsize = len(plaintext)
        rawfilemd5 = hashlib.md5(plaintext).hexdigest()
        media_type, media_item = self._build_media_item(path, rawsize, rawfilemd5, "", aes_key, b"")

        async def call(session: aiohttp.ClientSession) -> None:
            upload_response = await _api_post(
                session,
                base_url=self.config.base_url,
                endpoint=EP_GET_UPLOAD_URL,
                payload={
                    "filekey": filekey,
                    "media_type": media_type,
                    "to_user_id": target_id,
                    "rawsize": rawsize,
                    "rawfilemd5": rawfilemd5,
                    "filesize": _aes_padded_size(rawsize),
                    "no_need_thumb": True,
                    "aeskey": aes_key.hex(),
                },
                token=self.config.token,
            )
            upload_param = str(upload_response.get("upload_param") or "")
            upload_full_url = str(upload_response.get("upload_full_url") or "")
            if upload_full_url:
                upload_url = upload_full_url
            elif upload_param:
                upload_url = _cdn_upload_url(self.config.cdn_base_url, upload_param, filekey)
            else:
                raise RuntimeError(f"Weixin getuploadurl returned no upload URL: {upload_response}")
            ciphertext = _aes128_ecb_encrypt(plaintext, aes_key)
            encrypted_query_param = await _upload_ciphertext(session, ciphertext=ciphertext, upload_url=upload_url)
            _, item = self._build_media_item(path, rawsize, rawfilemd5, encrypted_query_param, aes_key, ciphertext)
            context_token = self.token_store.get(self.config.account_id, target_id)
            if caption:
                await self._send_text(target_id, caption)
            await _api_post(
                session,
                base_url=self.config.base_url,
                endpoint=EP_SEND_MESSAGE,
                payload={
                    "msg": {
                        "from_user_id": "",
                        "to_user_id": target_id,
                        "client_id": f"weclaw-weixin-{uuid.uuid4().hex}",
                        "message_type": MSG_TYPE_BOT,
                        "message_state": MSG_STATE_FINISH,
                        "item_list": [item],
                        **({"context_token": context_token} if context_token else {}),
                    }
                },
                token=self.config.token,
            )

        del media_item
        await self._with_session(call)

    def _build_media_item(
        self,
        path: Path,
        rawsize: int,
        rawfilemd5: str,
        encrypted_query_param: str,
        aes_key: bytes,
        ciphertext: bytes,
    ) -> tuple[int, dict[str, Any]]:
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        aes_key_for_api = base64.b64encode(aes_key.hex().encode("ascii")).decode("ascii") if aes_key else ""
        media = {"encrypt_query_param": encrypted_query_param, "aes_key": aes_key_for_api, "encrypt_type": 1}
        if mime.startswith("image/"):
            return MEDIA_IMAGE, {"type": ITEM_IMAGE, "image_item": {"media": media, "mid_size": len(ciphertext)}}
        if mime.startswith("video/"):
            return MEDIA_VIDEO, {"type": ITEM_VIDEO, "video_item": {"media": media, "video_size": len(ciphertext), "video_md5": rawfilemd5, "play_length": 0}}
        return MEDIA_FILE, {"type": ITEM_FILE, "file_item": {"media": media, "file_name": path.name, "len": str(rawsize)}}


class WeixinBot:
    def __init__(self, config: WeixinConfig | None = None) -> None:
        self.config = config or WeixinConfig()
        self.token_store = ContextTokenStore()
        self.sender: WeixinMessageSender | None = None
        self._poll_session: aiohttp.ClientSession | None = None
        self._send_session: aiohttp.ClientSession | None = None
        self._running = False
        self._dedup = ExpiringDeduplicator()

    async def run_forever(self) -> None:
        if not self.config.token or not self.config.account_id:
            raise RuntimeError("WEIXIN_TOKEN and WEIXIN_ACCOUNT_ID are required for Weixin channel.")
        self.token_store.restore(self.config.account_id)
        timeout = aiohttp.ClientTimeout(total=None, connect=None, sock_connect=None, sock_read=None)
        async with aiohttp.ClientSession(trust_env=True) as poll_session, aiohttp.ClientSession(trust_env=True, timeout=timeout) as send_session:
            self._poll_session = poll_session
            self._send_session = send_session
            self.sender = WeixinMessageSender(self.config, token_store=self.token_store, session=send_session)
            self._running = True
            await self._poll_loop()

    async def _poll_loop(self) -> None:
        assert self._poll_session is not None
        sync_buf = self._load_sync_buf()
        timeout_ms = WEIXIN_LONG_POLL_TIMEOUT_MS
        consecutive_failures = 0
        while self._running:
            try:
                response = await _get_updates(
                    self._poll_session,
                    base_url=self.config.base_url,
                    token=self.config.token,
                    sync_buf=sync_buf,
                    timeout_ms=timeout_ms,
                )
                suggested_timeout = response.get("longpolling_timeout_ms")
                if isinstance(suggested_timeout, int) and suggested_timeout > 0:
                    timeout_ms = suggested_timeout
                ret = response.get("ret", 0)
                errcode = response.get("errcode", 0)
                if ret not in {0, None} or errcode not in {0, None}:
                    if ret == SESSION_EXPIRED_ERRCODE or errcode == SESSION_EXPIRED_ERRCODE or _is_stale_session_ret(ret, errcode, response.get("errmsg")):
                        logger.error("Weixin session expired; pausing for 10 minutes.")
                        await asyncio.sleep(600)
                        consecutive_failures = 0
                        continue
                    consecutive_failures += 1
                    logger.warning("Weixin getupdates failed: ret=%s errcode=%s response=%s", ret, errcode, response)
                    await asyncio.sleep(min(30, WEIXIN_RESTART_DELAY_SECONDS * consecutive_failures))
                    continue
                consecutive_failures = 0
                new_sync_buf = str(response.get("get_updates_buf") or "")
                if new_sync_buf:
                    sync_buf = new_sync_buf
                    self._save_sync_buf(sync_buf)
                for message in response.get("msgs") or []:
                    asyncio.create_task(self._process_message_safe(message))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                consecutive_failures += 1
                logger.warning("Weixin polling error: %s", exc, exc_info=True)
                await asyncio.sleep(min(30, WEIXIN_RESTART_DELAY_SECONDS * consecutive_failures))

    async def _process_message_safe(self, message: dict[str, Any]) -> None:
        try:
            await self._process_message(message)
        except Exception as exc:
            logger.error("Unhandled Weixin inbound error from=%s: %s", message.get("from_user_id"), exc, exc_info=True)

    async def _download_image(self, item: dict[str, Any]) -> PhotoPayload | None:
        assert self._poll_session is not None
        image_item = item.get("image_item") or {}
        media = _media_reference(item, "image_item")
        aes_key = ""
        raw_aeskey = str(image_item.get("aeskey") or "").strip()
        if raw_aeskey:
            aes_key = raw_aeskey
        else:
            aes_key = str(media.get("aes_key") or "")
        try:
            data = await _download_and_decrypt_media(
                self._poll_session,
                cdn_base_url=self.config.cdn_base_url,
                encrypted_query_param=_media_encrypt_query_param(media),
                aes_key=aes_key,
                full_url=_media_full_url(media),
                timeout_seconds=30.0,
            )
            return PhotoPayload(data=data, mime_type="image/jpeg")
        except Exception as exc:
            logger.warning("Weixin image download failed: %s", exc)
            return None

    async def _download_file(self, item: dict[str, Any]) -> FilePayload | None:
        assert self._poll_session is not None
        file_item = item.get("file_item") or {}
        media = file_item.get("media") or {}
        filename = str(file_item.get("file_name") or "document.bin")
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        try:
            data = await _download_and_decrypt_media(
                self._poll_session,
                cdn_base_url=self.config.cdn_base_url,
                encrypted_query_param=_media_encrypt_query_param(media),
                aes_key=media.get("aes_key"),
                full_url=_media_full_url(media),
                timeout_seconds=60.0,
            )
            return save_uploaded_file(data, filename, mime_type)
        except Exception as exc:
            logger.warning("Weixin file download failed: %s", exc)
            return None

    async def _download_video(self, item: dict[str, Any]) -> FilePayload | None:
        assert self._poll_session is not None
        video_item = item.get("video_item") or {}
        media = video_item.get("media") or {}
        try:
            data = await _download_and_decrypt_media(
                self._poll_session,
                cdn_base_url=self.config.cdn_base_url,
                encrypted_query_param=_media_encrypt_query_param(media),
                aes_key=media.get("aes_key"),
                full_url=_media_full_url(media),
                timeout_seconds=120.0,
            )
            return save_uploaded_file(data, "video.mp4", "video/mp4")
        except Exception as exc:
            logger.warning("Weixin video download failed: %s", exc)
            return None

    async def _download_voice(self, item: dict[str, Any]) -> Path | None:
        assert self._poll_session is not None
        voice_item = item.get("voice_item") or {}
        media = voice_item.get("media") or {}
        if not _has_downloadable_media(media):
            return None
        try:
            data = await _download_and_decrypt_media(
                self._poll_session,
                cdn_base_url=self.config.cdn_base_url,
                encrypted_query_param=_media_encrypt_query_param(media),
                aes_key=media.get("aes_key"),
                full_url=_media_full_url(media),
                timeout_seconds=60.0,
            )
            suffix = _media_suffix(
                voice_item.get("format"),
                voice_item.get("file_format"),
                voice_item.get("voice_format"),
                media.get("mime_type"),
                media.get("content_type"),
                default=".silk",
            )
            return save_voice_bytes(data, suffix)
        except Exception as exc:
            logger.warning("Weixin voice download failed: %s", exc)
            return None

    async def _collect_media(self, item_list: list[dict[str, Any]]) -> WeixinInboundMedia:
        result = WeixinInboundMedia()
        for item in item_list:
            item_type = item.get("type")
            if item_type == ITEM_IMAGE and result.photo is None:
                photo = await self._download_image(item)
                if photo is not None:
                    result.photo = photo
                else:
                    result.add_error("图片下载失败")
            elif item_type == ITEM_FILE and result.file is None:
                file_payload = await self._download_file(item)
                if file_payload is not None:
                    result.file = file_payload
                else:
                    result.add_error("文件下载失败")
            elif item_type == ITEM_VIDEO and result.file is None:
                video_payload = await self._download_video(item)
                if video_payload is not None:
                    result.file = video_payload
                else:
                    result.add_error("视频下载失败")
            elif item_type == ITEM_VOICE:
                voice_item = item.get("voice_item") or {}
                if not result.voice_text:
                    result.voice_text = str(voice_item.get("text") or "").strip()
                if result.voice_path is None:
                    voice_path = await self._download_voice(item)
                    if voice_path is not None:
                        result.voice_path = voice_path
                    elif not result.voice_text:
                        result.add_error("语音下载失败")
        return result

    async def _process_message(self, message: dict[str, Any]) -> None:
        sender_id = str(message.get("from_user_id") or "").strip()
        if not sender_id or sender_id == self.config.account_id:
            return
        message_id = str(message.get("message_id") or "").strip()
        if message_id and self._dedup.is_duplicate(message_id):
            return
        item_list = message.get("item_list") or []
        text = _extract_text(item_list)
        if text:
            content_key = f"content:{sender_id}:{hashlib.md5(text.encode()).hexdigest()}"
            if self._dedup.is_duplicate(content_key):
                return
        chat_type, target_id = _guess_chat_type(message, self.config.account_id)
        if chat_type == "group":
            if self.config.group_policy == "disabled":
                return
            if self.config.group_policy == "allowlist" and target_id not in self.config.group_allowed_users:
                return
        elif not self._is_dm_allowed(sender_id):
            return
        context_token = str(message.get("context_token") or "").strip()
        if context_token:
            self.token_store.set(self.config.account_id, sender_id, context_token)
        media = await self._collect_media(item_list)
        if not text and not media.has_media:
            if media.errors:
                assert self.sender is not None
                await self.sender.send_text(target_id, "收到媒体消息，但下载失败：" + "；".join(media.errors))
            return
        conversation = build_weixin_conversation(target_id=target_id, sender_id=sender_id, chat_type=chat_type)
        pending = get_pending_confirmation(conversation.target_id)
        if pending is not None:
            decision = normalize_confirmation_reply(text)
            if decision is None:
                await self._send_agent_reply(
                    conversation.target_id,
                    AgentReply.from_text("当前有待确认的工具操作，请回复“允许”“本会话允许”或“拒绝”。"),
                )
                return
            if decision == "approve_session":
                granted = grant_session_tool_access(pending.request.session_scope, pending.request)
                resolve_pending_confirmation(conversation.target_id, True)
                message = (
                    f"已允许并记住当前会话授权：{pending.request.tool_name}"
                    if granted
                    else "当前工具不支持会话级授权，已按单次确认继续执行。"
                )
            else:
                approved = decision == "approve_once"
                resolve_pending_confirmation(conversation.target_id, approved)
                message = "已确认，继续执行。" if approved else "已取消本次工具操作。"
            await self._send_agent_reply(conversation.target_id, AgentReply.from_text(message))
            return
        if not media.has_media and text.strip().lower() == "/reset":
            clear_session_id(conversation.session_scope)
            await self._send_agent_reply(conversation.target_id, AgentReply.from_text("当前微信会话已重置。"))
            return

        if not media.has_media:
            task_result = await handle_task_command(conversation, text)
            if task_result.handled:
                await self._send_agent_reply(conversation.target_id, AgentReply.from_text(task_result.message))
                return

        logger.info("Weixin inbound chat=%s sender=%s", target_id, sender_id)
        assert self.sender is not None
        photo_payload = media.photo
        file_payload = media.file
        caption = text or "无"
        if media.voice_path is not None or media.voice_text:
            if media.voice_text:
                transcript = media.voice_text
            elif media.voice_path is not None:
                try:
                    transcript = transcribe_voice(media.voice_path)
                except SpeechRecognitionError as exc:
                    await self._send_agent_reply(conversation.target_id, AgentReply.from_text(f"语音已保存，但语音转文字失败：{exc}"))
                    return
            else:
                transcript = ""
            prompt = (
                "用户发送了一条微信语音消息。\n"
                f"语音本地路径：{media.voice_path or '(iLink only provided text)'}\n"
                f"语音转写文本：{transcript}\n\n"
                "请把这条语音转写文本当作用户的真实输入来处理。"
            )
            record_text = f"[Weixin] 用户发送了一条语音。转写：{transcript}"
        elif file_payload is not None:
            prompt = (
                f"用户上传了一个微信文件：{file_payload.file_name}\n"
                f"用户附带说明：{caption}\n\n"
                f"文件已保存到：{file_payload.saved_path}\n"
                "请使用 read_workspace_file 工具读取并分析该文件，然后直接给出有帮助的中文回答。"
            )
            record_text = f"[Weixin] 用户上传了一个文件：{file_payload.file_name}。说明：{caption}"
        elif photo_payload is not None:
            prompt = (
                "用户上传了一张微信图片。\n"
                f"用户附带说明：{caption}\n\n"
                "请结合图片和用户说明进行分析，并直接给出有帮助的中文回答。"
            )
            record_text = f"[Weixin] 用户上传了一张图片。说明：{caption}"
        else:
            prompt = text
            record_text = text
        try:
            reply = await run_agent_for_conversation(
                prompt=prompt,
                conversation=conversation,
                sender=self.sender,
                continue_session=True,
                record_text=record_text,
                uploaded_image=photo_payload,
                uploaded_file=file_payload,
            )
        except AgentServiceError as exc:
            logger.warning("Weixin agent service failed for chat=%s sender=%s: %s", target_id, sender_id, exc)
            await self._send_agent_reply(
                conversation.target_id,
                AgentReply.from_text(f"抱歉，这次调用模型服务失败了：{exc}"),
            )
            return
        await self._send_agent_reply(conversation.target_id, reply)

    def _is_dm_allowed(self, sender_id: str) -> bool:
        if self.config.dm_policy == "disabled":
            return False
        if self.config.dm_policy == "allowlist":
            return sender_id in self.config.allowed_users
        return True

    async def _send_agent_reply(self, target_id: str, reply: AgentReply) -> None:
        assert self.sender is not None
        if reply.text.strip():
            await self.sender.send_text(target_id, reply.text)
        for image in reply.images:
            suffix = image.mime_type.split("/", 1)[-1] if "/" in image.mime_type else "png"
            await self.sender.send_file(target_id, image.data, f"generated.{suffix}")
        for file in reply.files:
            await self.sender.send_file(target_id, file.data, file.file_name)

    def _sync_path(self) -> Path:
        path = DATA_DIR / "weixin"
        path.mkdir(parents=True, exist_ok=True)
        return path / f"{self.config.account_id}.sync.json"

    def _load_sync_buf(self) -> str:
        path = self._sync_path()
        if not path.exists():
            return ""
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("get_updates_buf", "")
        except Exception:
            return ""

    def _save_sync_buf(self, sync_buf: str) -> None:
        self._sync_path().write_text(json.dumps({"get_updates_buf": sync_buf}, ensure_ascii=False, indent=2), encoding="utf-8")


def run_weixin_polling() -> None:
    while True:
        try:
            asyncio.run(WeixinBot().run_forever())
        except KeyboardInterrupt:
            print("Weixin bot stopped.")
            break
        except Exception as exc:
            logger.warning("Weixin bot crashed: %s. Restarting in %s seconds...", exc, WEIXIN_RESTART_DELAY_SECONDS, exc_info=True)
            time.sleep(WEIXIN_RESTART_DELAY_SECONDS)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(prog="weclaw-weixin", description="Weixin personal-account channel tools")
    subparsers = parser.add_subparsers(dest="command")
    login_parser = subparsers.add_parser("login", help="Scan a WeChat QR code and write WEIXIN_* credentials to .env")
    login_parser.add_argument("--bot-type", default="3")
    login_parser.add_argument("--timeout", type=int, default=480)
    login_parser.add_argument("--no-write-env", action="store_true")
    login_parser.add_argument("--no-open-browser", action="store_true")
    subparsers.add_parser("run", help="Run Weixin long polling")
    args = parser.parse_args(argv)
    if args.command == "login":
        result = asyncio.run(
            qr_login(
                bot_type=args.bot_type,
                timeout_seconds=args.timeout,
                write_env=not args.no_write_env,
                open_browser=not args.no_open_browser,
            )
        )
        raise SystemExit(0 if result else 1)
    run_weixin_polling()


if __name__ == "__main__":
    main()
