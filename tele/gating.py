#!/usr/bin/env python3
"""
Group Chat Mention & Reply Gating.
Controls whether incoming messages in group or supergroup chats should be processed by the bot.
In private chats, messages are always allowed. In groups, requires explicit @mention or direct reply to bot.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple
from telegram import Update


def should_process_chat_message(
    update: Update,
    bot_id: Optional[int] = None,
    bot_username: str = "",
    require_mention: bool = True,
    text: str = ""
) -> Tuple[bool, str]:
    """
    Evaluates whether the incoming update should be processed.
    Returns (should_process, cleaned_text).

    1. In private chats: always allowed.
    2. In groups / supergroups:
       - If require_mention is False: allowed.
       - If message explicitly @mentions the bot: allowed (with mention stripped).
       - If message is a direct reply to the bot's own message: allowed.
       - Otherwise: rejected (silent ignore).
    """
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat:
        return False, text

    chat_type = getattr(chat, "type", None)
    # Only group and supergroup chats require mention gating
    if chat_type not in ("group", "supergroup"):
        return True, text

    # In groups/supergroups, if require_mention is disabled, process all
    if not require_mention:
        return True, text

    clean_bot_username = bot_username.lstrip("@").strip().lower()

    # 1. Check if user is replying to the bot
    reply = getattr(msg, "reply_to_message", None)
    if reply and reply.from_user:
        is_bot_reply = reply.from_user.is_bot
        matches_id = (bot_id is not None and reply.from_user.id == bot_id)
        reply_username = (reply.from_user.username or "").lower()
        matches_username = bool(clean_bot_username and reply_username == clean_bot_username)

        if is_bot_reply and (matches_id or matches_username or not bot_id):
            return True, text

    # 2. Check if bot is explicitly mentioned in text
    has_mention = False
    cleaned = text

    if clean_bot_username:
        mention_pattern = re.compile(rf"@?{re.escape(clean_bot_username)}\b", re.IGNORECASE)
        if mention_pattern.search(text):
            has_mention = True
            cleaned = mention_pattern.sub("", text).strip()

    # 3. Check entity mentions if regex did not hit
    if not has_mention and msg.entities:
        for ent in msg.entities:
            if ent.type == "mention" and clean_bot_username:
                ent_text = text[ent.offset : ent.offset + ent.length].lstrip("@").lower()
                if ent_text == clean_bot_username:
                    has_mention = True
                    # Remove this mention segment
                    cleaned = (text[: ent.offset] + text[ent.offset + ent.length :]).strip()
                    break
            elif ent.type == "text_mention" and ent.user and bot_id:
                if ent.user.id == bot_id:
                    has_mention = True
                    cleaned = (text[: ent.offset] + text[ent.offset + ent.length :]).strip()
                    break

    if has_mention:
        return True, cleaned

    return False, text
