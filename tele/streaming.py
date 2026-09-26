#!/usr/bin/env python3
"""
Telegram Streaming, In-Place Status Updates & Safe Message Splitter.
Provides Bot API 9.5 sendMessageDraft support for private chat live previews,
single-bubble status updates (send_or_update_status), and safe chunking.
Adapted from hermes-agent/plugins/platforms/telegram/adapter.py.
"""

from __future__ import annotations

import logging
from typing import Optional, Dict, Tuple, List, Any
from telegram import Bot, Message
from telegram.constants import ParseMode
from telegram.error import BadRequest

logger = logging.getLogger("antigravity-tele-bot.streaming")

# Bookkeeping: (chat_id, status_key) -> Message
_status_bubbles: Dict[Tuple[str, str], Message] = {}
_STATUS_BUBBLES_MAX = 512


def supports_draft_streaming(bot: Bot, chat_type: Optional[str] = "private") -> bool:
    """Returns True if Bot API supports sendMessageDraft and chat is a private 1-on-1 DM."""
    if not hasattr(bot, "send_message_draft"):
        return False
    return (chat_type or "").lower() in ("private", "dm")


async def send_draft_stream(
    bot: Bot,
    chat_id: int,
    draft_id: int,
    text: str,
    message_thread_id: Optional[int] = None
) -> bool:
    """
    Sends an ephemeral draft preview frame via Bot API 9.5 sendMessageDraft.
    Drafts animate live typing in private chats without generating message_ids.
    """
    if not hasattr(bot, "send_message_draft"):
        return False

    kwargs: Dict[str, Any] = {
        "chat_id": chat_id,
        "draft_id": int(draft_id),
        "text": text[:4000]
    }
    if message_thread_id is not None:
        kwargs["message_thread_id"] = message_thread_id

    try:
        await bot.send_message_draft(**kwargs)
        return True
    except Exception as e:
        logger.debug(f"sendMessageDraft failed (chat_id={chat_id}): {e}")
        return False


async def send_or_update_status(
    bot: Bot,
    chat_id: int,
    status_key: str,
    text: str,
    message_thread_id: Optional[int] = None,
    reply_to_message_id: Optional[int] = None,
    parse_mode: Optional[str] = ParseMode.HTML
) -> Optional[Message]:
    """
    Sends a new status bubble or edits the existing one with the same (chat_id, status_key).
    Prevents chat history clutter during multi-step reasoning or tool execution.
    """
    key = (str(chat_id), str(status_key))
    existing_msg = _status_bubbles.get(key)

    if existing_msg is not None:
        try:
            await existing_msg.edit_text(text, parse_mode=parse_mode)
            return existing_msg
        except BadRequest as e:
            err = str(e).lower()
            if "message is not modified" in err:
                return existing_msg
            # If message was deleted or cannot be edited, remove from cache and send new
            _status_bubbles.pop(key, None)
        except Exception:
            _status_bubbles.pop(key, None)

    # Send new status bubble
    kwargs: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_notification": True,
    }
    if message_thread_id is not None:
        kwargs["message_thread_id"] = message_thread_id
    if reply_to_message_id is not None:
        kwargs["reply_to_message_id"] = reply_to_message_id

    try:
        new_msg = await bot.send_message(**kwargs)
        if len(_status_bubbles) >= _STATUS_BUBBLES_MAX:
            # FIFO trim
            for k in list(_status_bubbles.keys())[: _STATUS_BUBBLES_MAX // 2]:
                _status_bubbles.pop(k, None)
        _status_bubbles[key] = new_msg
        return new_msg
    except Exception as e:
        logger.warning(f"Failed to send status message: {e}")
        return None


def clear_status_bubble(chat_id: int, status_key: str) -> None:
    """Removes a status bubble from bookkeeping."""
    _status_bubbles.pop((str(chat_id), str(status_key)), None)


def split_message(text: str, max_length: int = 4000) -> List[str]:
    """
    Safely splits long text so it does not exceed Telegram's limit (4096).
    Splits on line breaks or spaces, ensuring code blocks stay well-formed.
    """
    if not text:
        return ["(Tidak ada output teks dari agy)"]
    if len(text) <= max_length:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        split_idx = text.rfind("\n", 0, max_length)
        if split_idx == -1 or split_idx < max_length // 2:
            split_idx = text.rfind(" ", 0, max_length)
        if split_idx == -1 or split_idx < max_length // 2:
            split_idx = max_length
        chunk = text[:split_idx]
        chunks.append(chunk)
        text = text[split_idx:].lstrip("\r\n")

    return chunks
