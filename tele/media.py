#!/usr/bin/env python3
"""
Multi-Media Pipeline & Security Path Traversal Guard.
Handles inbound audio, photo, document processing and outbound voice/photo/doc dispatch
with strict path validation against unauthorized file access.
Adapted from hermes-agent gateway/platforms/base.py and plugins/platforms/telegram/adapter.py.
"""

from __future__ import annotations

import os
import re
import logging
from pathlib import Path
from typing import List, Tuple, Optional, Any
from telegram import Bot, Message
from telegram.constants import ParseMode
from telegram.error import BadRequest

from config import WORKSPACE_DIR, get_upload_dir

logger = logging.getLogger("antigravity-tele-bot.media")

# Restricted system directories that must NEVER be read or sent
FORBIDDEN_DIR_PREFIXES = (
    "/etc",
    "/proc",
    "/sys",
    "/dev",
    "/boot",
    "/root",
    "/var/run",
    "c:\\windows",
    "c:\\boot",
)

# Restricted credential / secret patterns
FORBIDDEN_FILE_PATTERNS = [
    r"[\\/]\.ssh[\\/]",
    r"[\\/]\.aws[\\/]",
    r"[\\/]\.gnupg[\\/]",
    r"[\\/]\.gemini[\\/]",
    r"[\\/]\.env(?:\..*)?$",
    r"[\\/].*\.db$",
    r"[\\/]id_rsa(?:\.pub)?$",
    r"[\\/]credentials\.json$",
    r"[\\/]\..*history$",
    r"[\\/]\.bash.*",
    r"[\\/]\.profile$",
    r"[\\/]\.config[\\/]",
    r"[\\/]\.git[\\/]",
]


def validate_media_delivery_path(path_str: str, workspace_dir: str = WORKSPACE_DIR) -> Tuple[bool, str]:
    """
    Validates whether a local file path is authorized to be dispatched to Telegram.
    Blocks directory traversal, sensitive OS directories, and credential files.
    Returns (is_valid: bool, error_reason: str).
    """
    try:
        raw_path = Path(path_str).expanduser()
        resolved = raw_path.resolve()
    except Exception as e:
        return False, f"Invalid path syntax: {e}"

    if not resolved.exists():
        return False, f"File does not exist: {resolved}"

    if not resolved.is_file():
        return False, f"Target is not a regular file: {resolved}"

    resolved_str = str(resolved).lower()

    # 1. Check system forbidden directory prefixes
    for prefix in FORBIDDEN_DIR_PREFIXES:
        if resolved_str.startswith(prefix.lower()):
            logger.critical(f"BLOCKED MEDIA ATTEMPT to system directory: {resolved}")
            return False, f"Access to system directory '{prefix}' is strictly forbidden."

    # 2. Check secret / credential patterns
    for pat in FORBIDDEN_FILE_PATTERNS:
        if re.search(pat, resolved_str, re.IGNORECASE):
            logger.critical(f"BLOCKED MEDIA ATTEMPT to secret file: {resolved}")
            return False, "Access to sensitive credentials/configuration is strictly forbidden."

    # 3. Check boundary: must be within workspace, upload dir, or system temp
    ws_resolved = Path(workspace_dir).resolve()
    upload_resolved = get_upload_dir().resolve()
    temp_resolved = Path(os.environ.get("TEMP", "/tmp")).resolve()

    is_within_allowed = (
        resolved.is_relative_to(ws_resolved)
        or resolved.is_relative_to(upload_resolved)
        or resolved.is_relative_to(temp_resolved)
    )

    if not is_within_allowed:
        logger.warning(f"BLOCKED MEDIA ATTEMPT outside allowed workspace: {resolved}")
        return False, f"File '{resolved.name}' is outside the authorized workspace."

    return True, ""


def extract_media_paths(text: str, workspace_dir: str = WORKSPACE_DIR) -> List[str]:
    """
    Extracts all MEDIA:/path/to/file directives from model response text.
    Supports MEDIA:, BERKAS:, and FILE: prefixes, markdown backticks, and file:// URIs.
    Validates every candidate through validate_media_delivery_path.
    """
    if not text:
        return []

    found_paths: List[str] = []
    # Pattern: MEDIA:/path/to/file or MEDIA: /path/to/file or [MEDIA: /path] or BERKAS: or FILE:
    patterns = [
        r"(?:MEDIA|BERKAS|FILE):\s*([^\s\n\r]+)",
        r"\[(?:MEDIA|BERKAS|FILE):\s*([^\]]+)\]",
    ]

    for pat in patterns:
        for match in re.finditer(pat, text, re.IGNORECASE):
            raw_path = match.group(1).strip().strip("\"'()[]<>`*")
            if not raw_path:
                continue

            # Strip file:// prefix if present
            if raw_path.lower().startswith("file:///"):
                raw_path = raw_path[8:] if os.name == "nt" and len(raw_path) > 9 and raw_path[9] == ":" else raw_path[7:]
            elif raw_path.lower().startswith("file://"):
                raw_path = raw_path[7:]

            p = Path(raw_path).expanduser()
            if not p.is_absolute():
                candidate = (Path(workspace_dir) / raw_path).resolve()
            else:
                candidate = p.resolve()

            is_valid, _ = validate_media_delivery_path(str(candidate), workspace_dir)
            if is_valid:
                found_paths.append(str(candidate))

    return list(dict.fromkeys(found_paths))


def classify_media_type(file_path: str) -> str:
    """Classifies file extension into appropriate Telegram media type."""
    ext = Path(file_path).suffix.lower()
    if ext in (".ogg", ".oga", ".opus"):
        return "voice"
    elif ext in (".mp3", ".wav", ".m4a", ".aac", ".flac"):
        return "audio"
    elif ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
        return "photo"
    elif ext == ".gif":
        return "animation"
    elif ext in (".mp4", ".mov", ".mkv", ".webm"):
        return "video"
    return "document"


async def send_outbound_media(
    bot: Bot,
    chat_id: int,
    file_path: str,
    caption: Optional[str] = None,
    reply_to_message_id: Optional[int] = None,
    message_thread_id: Optional[int] = None
) -> Optional[Message]:
    """
    Dispatches media to Telegram using the appropriate native API method
    (send_voice, send_photo, send_video, send_animation, or send_document).
    Supports safe retry on forum thread reply targets and caption parse errors.
    """
    is_valid, err_reason = validate_media_delivery_path(file_path)
    if not is_valid:
        logger.error(f"Cannot send media '{file_path}': {err_reason}")
        return None

    media_type = classify_media_type(file_path)
    filename = Path(file_path).name

    effective_thread = message_thread_id if isinstance(message_thread_id, int) else None
    effective_reply = reply_to_message_id if isinstance(reply_to_message_id, int) else None

    kwargs: dict[str, Any] = {
        "chat_id": chat_id,
    }
    if effective_thread is not None:
        kwargs["message_thread_id"] = effective_thread
    if effective_reply is not None:
        kwargs["reply_to_message_id"] = effective_reply

    async def _send_with_retry(send_fn, **call_kwargs) -> Optional[Message]:
        try:
            return await send_fn(**call_kwargs)
        except BadRequest as e:
            err_str = str(e).lower()
            # 1. Retry without reply_to_message_id if reply anchor failed in forum thread
            if "repl" in err_str and "reply_to_message_id" in call_kwargs:
                logger.warning(
                    f"Reply target {call_kwargs.get('reply_to_message_id')} invalid for media dispatch ({e}). Retrying without reply..."
                )
                retry_kwargs = dict(call_kwargs)
                retry_kwargs.pop("reply_to_message_id", None)
                try:
                    return await send_fn(**retry_kwargs)
                except BadRequest as e_retry:
                    e = e_retry
                    err_str = str(e).lower()

            # 2. Retry without parse_mode if caption formatting failed
            if ("parse" in err_str or "entity" in err_str) and "parse_mode" in call_kwargs:
                logger.warning(f"Caption markdown parse error ({e}). Retrying media with plain text caption...")
                plain_kwargs = dict(call_kwargs)
                plain_kwargs.pop("parse_mode", None)
                try:
                    return await send_fn(**plain_kwargs)
                except Exception as e_plain:
                    logger.error(f"Failed plain caption retry: {e_plain}")
                    return None

            logger.error(f"Telegram BadRequest during media dispatch: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in _send_with_retry for '{file_path}': {e}")
            return None

    try:
        with open(file_path, "rb") as f:
            if media_type == "voice":
                return await _send_with_retry(
                    bot.send_voice,
                    **kwargs,
                    voice=f,
                    caption=caption or f"🎙️ Suara: `{filename}`",
                    parse_mode=ParseMode.MARKDOWN
                )
            elif media_type == "audio":
                return await _send_with_retry(
                    bot.send_audio,
                    **kwargs,
                    audio=f,
                    caption=caption or f"🎵 Audio: `{filename}`",
                    parse_mode=ParseMode.MARKDOWN
                )
            elif media_type == "photo":
                return await _send_with_retry(
                    bot.send_photo,
                    **kwargs,
                    photo=f,
                    caption=caption or f"🖼️ Gambar: `{filename}`",
                    parse_mode=ParseMode.MARKDOWN
                )
            elif media_type == "animation":
                return await _send_with_retry(
                    bot.send_animation,
                    **kwargs,
                    animation=f,
                    caption=caption or f"🎞️ Animasi: `{filename}`",
                    parse_mode=ParseMode.MARKDOWN
                )
            elif media_type == "video":
                return await _send_with_retry(
                    bot.send_video,
                    **kwargs,
                    video=f,
                    caption=caption or f"🎬 Video: `{filename}`",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                return await _send_with_retry(
                    bot.send_document,
                    **kwargs,
                    document=f,
                    caption=caption or f"📄 Berkas: `{filename}`",
                    parse_mode=ParseMode.MARKDOWN
                )
    except Exception as e:
        logger.error(f"Failed to dispatch media '{file_path}' via Telegram: {e}")
        return None
