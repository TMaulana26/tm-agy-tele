#!/usr/bin/env python3
"""
Telegram Private Chat Topics (Forum Topics in DM - Bot API 9.4).
Enables ChatGPT-style parallel multi-session DMs with isolated conversation memory,
automatic topic creation, and session-based auto-renaming.
Adapted from hermes-agent/plugins/platforms/telegram/adapter.py and hermes_state_telegram.py.
"""

from __future__ import annotations

import re
import uuid
import logging
from typing import Optional, Tuple, Dict, Any
from telegram import Update, Bot
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from database.state import get_db

logger = logging.getLogger("antigravity-tele-bot.topics")


async def handle_topic_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles /topic command in Telegram private DM.
    Checks BotFather Threaded Mode status and introduces topic functionality.
    """
    chat = update.effective_chat
    if not chat or chat.type != "private":
        if update.message:
            await update.message.reply_text("ℹ️ Perintah `/topic` hanya dapat digunakan di Private Chat (DM).", parse_mode=ParseMode.MARKDOWN)
        return

    bot: Bot = context.bot
    bot_info = await bot.get_me()

    has_topics = getattr(bot_info, "has_topics_enabled", False)
    if not has_topics:
        guide_text = (
            "⚠️ **Threaded Mode Belum Aktif di BotFather**\n\n"
            "Untuk menikmati fitur **Multi-Session DM (Topik Obrolan Paralel)** seperti di ChatGPT / Hermes:\n\n"
            "1. Buka bot [@BotFather](https://t.me/BotFather) di Telegram.\n"
            "2. Buka menu **Bot Settings** ➔ **Threads Settings**.\n"
            "3. Pilih **Turn on Threaded Mode**.\n\n"
            "💡 *Setelah diaktifkan di BotFather, kirim kembali `/topic` di sini untuk mulai menggunakan tombol + topik!*"
        )
        if update.message:
            await update.message.reply_text(guide_text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        return

    # Threaded mode is enabled: create General / System topic if needed
    try:
        topic = await bot.create_forum_topic(chat_id=chat.id, name="Umum / General", icon_color=7322096)
        system_thread_id = topic.message_thread_id
        db = get_db()
        db.set_topic_binding(chat.id, system_thread_id, conv_id="", topic_name="Umum / General")
        logger.info(f"Created system forum topic for chat {chat.id} (thread_id: {system_thread_id})")
    except Exception as e:
        logger.debug(f"Forum topic creation note (may already exist): {e}")

    success_text = (
        "🎉 **Mode Topik Multi-Sesi Aktif!**\n\n"
        "Kini Anda dapat menekan tombol **+** di Telegram untuk membuat topik obrolan baru kapan saja.\n\n"
        "✨ **Keunggulan Multi-Sesi:**\n"
        "• Setiap topik memiliki memori percakapan Antigravity yang **terisolasi secara mandiri**.\n"
        "• Anda dapat menjalankan tugas terpisah (misal: 'Perbaiki Bug Docker' dan 'Tulis Dokumentasi') secara simultan.\n"
        "• Mengetik `/reset` di dalam topik hanya mereset sesi topik tersebut tanpa mengganggu topik lainnya.\n"
        "• Judul topik akan otomatis dinamai sesuai tugas pertama yang Anda berikan!"
    )
    if update.message:
        await update.message.reply_text(success_text, parse_mode=ParseMode.MARKDOWN)


def get_conversation_for_message(chat_id: int, thread_id: Optional[int]) -> Tuple[Optional[str], Optional[str]]:
    """
    Resolves the agy conversation_id and topic_name for an incoming message.
    Returns (conv_id, topic_name).
    """
    if thread_id is None:
        return None, None

    db = get_db()
    binding = db.get_topic_binding(chat_id, thread_id)
    if binding:
        return binding.get("conv_id") or None, binding.get("topic_name")
    return None, None


def bind_conversation_to_topic(chat_id: int, thread_id: Optional[int], conv_id: str, topic_name: Optional[str] = None) -> None:
    """Binds an agy conversation_id to a specific Telegram message_thread_id."""
    if thread_id is None:
        return
    db = get_db()
    db.set_topic_binding(chat_id, thread_id, conv_id, topic_name)


async def auto_rename_forum_topic(
    bot: Bot,
    chat_id: int,
    thread_id: int,
    user_prompt: str,
    ai_response: str
) -> None:
    """
    Generates a concise 3-5 word topic title from the initial conversation turn
    and updates the forum topic title via edit_forum_topic.
    """
    db = get_db()
    binding = db.get_topic_binding(chat_id, thread_id)
    existing_name = binding.get("topic_name") if binding else None

    # Only auto-rename if topic has no name or default placeholder name
    if existing_name and existing_name not in ("New Topic", "Topik Baru", "General", "Umum / General"):
        return

    # Extract 3 to 5 words from prompt
    clean = re.sub(r"[^\w\s-]", "", user_prompt).strip()
    words = clean.split()
    if words:
        title_candidate = " ".join(words[:5]).capitalize()
    else:
        title_candidate = "Sesi Baru"

    if len(title_candidate) > 60:
        title_candidate = title_candidate[:57] + "..."

    try:
        await bot.edit_forum_topic(chat_id=chat_id, message_thread_id=thread_id, name=title_candidate)
        db.update_topic_name(chat_id, thread_id, title_candidate)
        logger.info(f"Auto-renamed topic {thread_id} in chat {chat_id} to '{title_candidate}'")
    except Exception as e:
        logger.debug(f"Could not auto-rename forum topic {thread_id}: {e}")
