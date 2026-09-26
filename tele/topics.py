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
    If arguments given (/topic <nama>), creates a forum topic directly with that name.
    If no arguments, checks Threaded Mode and ensures General topic.
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
            "2. Klik tombol **Open** (Mini App BotFather).\n"
            "3. Buka menu **Bot Settings** ➔ **Threads Settings**.\n"
            "4. Aktifkan **Threaded Mode** (geser toggle ke kanan).\n\n"
            "💡 *Setelah diaktifkan di BotFather, jalankan kembali `/topic` di sini!*"
        )
        if update.message:
            await update.message.reply_text(guide_text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        return

    db = get_db()
    args = context.args if context.args else []
    custom_name = " ".join(args).strip() if args else ""

    # Case A: User provided a custom topic name (/topic <nama>)
    if custom_name:
        try:
            topic = await bot.create_forum_topic(chat_id=chat.id, name=custom_name)
            new_thread_id = topic.message_thread_id
            new_conv_id = str(uuid.uuid4())
            db.set_topic_binding(chat.id, new_thread_id, conv_id=new_conv_id, topic_name=custom_name)
            logger.info(f"Created custom forum topic '{custom_name}' for chat {chat.id} (thread_id: {new_thread_id})")

            # Send welcome message into the new topic
            await bot.send_message(
                chat_id=chat.id,
                message_thread_id=new_thread_id,
                text=f"🚀 <b>Topik Baru: {custom_name}</b>\n\nSesi percakapan Antigravity untuk topik ini telah siap dan terisolasi secara mandiri.",
                parse_mode=ParseMode.HTML
            )
            if update.message:
                await update.message.reply_text(
                    f"✓ Topik <b>{custom_name}</b> berhasil dibuat! Silakan buka topik tersebut di tab obrolan Anda untuk mulai berinteraksi.",
                    parse_mode=ParseMode.HTML
                )
        except Exception as e:
            logger.error(f"Gagal membuat topic baru: {e}")
            if update.message:
                await update.message.reply_text(f"❌ Gagal membuat topik baru: {e}")
        return

    # Case B: No argument, ensure general system topic
    try:
        topic = await bot.create_forum_topic(chat_id=chat.id, name="Umum / General", icon_color=7322096)
        system_thread_id = topic.message_thread_id
        db.set_topic_binding(chat.id, system_thread_id, conv_id="", topic_name="Umum / General")
        logger.info(f"Created system forum topic for chat {chat.id} (thread_id: {system_thread_id})")
    except Exception as e:
        logger.debug(f"Forum topic creation note (may already exist): {e}")

    success_text = (
        "🎉 <b>Mode Topik Multi-Sesi Aktif!</b>\n\n"
        "Anda dapat membuat topik baru dengan cara:\n"
        "• Ketik: <code>/topic [Nama Topik]</code> (contoh: <code>/topic Refactor Modul Auth</code>)\n"
        "• Atau tekan tombol <b>+ (New Topic)</b> di Telegram.\n\n"
        "✨ <b>Perintah Kelola Topik:</b>\n"
        "• <code>/topics</code> : Lihat daftar semua topik aktif\n"
        "• <code>/title [Nama Baru]</code> : Ganti nama topik aktif saat ini\n"
        "• <code>/deletetopic</code> : Hapus topik aktif saat ini\n"
        "• <code>/reset</code> : Bersihkan riwayat memori sesi topik ini"
    )
    if update.message:
        await update.message.reply_text(success_text, parse_mode=ParseMode.HTML)


async def handle_topics_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /topics command - lists all active forum topic bindings."""
    chat = update.effective_chat
    if not chat:
        return

    raw_thread = getattr(update.message, "message_thread_id", None)
    thread_id = raw_thread if isinstance(raw_thread, int) else None

    db = get_db()
    bindings = db.list_topic_bindings(chat.id)

    if not bindings:
        text = (
            "📋 <b>Belum Ada Topik Terdaftar</b>\n\n"
            "Anda sedang berada di sesi chat utama (Lobby). Untuk membuat topik baru, ketik:\n"
            "<code>/topic &lt;nama topik&gt;</code>\n"
            "Contoh: <code>/topic Refactor Auth</code>"
        )
        if update.message:
            await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    lines = ["📋 <b>Daftar Topik Percakapan Aktif:</b>\n"]
    for idx, b in enumerate(bindings, 1):
        tid = b.get("thread_id")
        name = b.get("topic_name") or "Tanpa Judul"
        conv = b.get("conv_id") or "Belum dimulai"
        short_conv = f"<code>{conv[:8]}...</code>" if len(conv) > 8 else f"<code>{conv}</code>"
        is_current = " 👈 <i>(Topik ini)</i>" if thread_id is not None and str(thread_id) == str(tid) else ""
        lines.append(f"{idx}. 💬 <b>{name}</b> (Thread ID: <code>{tid}</code>){is_current}\n   └ Sesi: {short_conv}")

    lines.append("\n💡 <i>Gunakan <code>/title [nama]</code> untuk ganti nama, atau <code>/deletetopic</code> untuk menghapus topik aktif.</i>")
    if update.message:
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def handle_title_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /title command - renames the current forum topic or shows current title."""
    chat = update.effective_chat
    if not chat:
        return

    raw_thread = getattr(update.message, "message_thread_id", None)
    thread_id = raw_thread if isinstance(raw_thread, int) else None

    if thread_id is None:
        if update.message:
            await update.message.reply_text(
                "⚠️ Perintah <code>/title</code> digunakan di dalam topik obrolan untuk mengganti judulnya.",
                parse_mode=ParseMode.HTML
            )
        return

    db = get_db()
    binding = db.get_topic_binding(chat.id, thread_id)
    current_name = binding.get("topic_name") if binding else "Tanpa Judul"

    args = context.args if context.args else []
    new_title = " ".join(args).strip() if args else ""

    if not new_title:
        if update.message:
            await update.message.reply_text(
                f"🏷️ Judul topik saat ini: <b>{current_name}</b>\n\nUntuk mengganti judul, ketik: <code>/title &lt;nama baru&gt;</code>",
                parse_mode=ParseMode.HTML
            )
        return

    bot: Bot = context.bot
    try:
        await bot.edit_forum_topic(chat_id=chat.id, message_thread_id=thread_id, name=new_title)
        db.update_topic_name(chat.id, thread_id, new_title)
        if update.message:
            await update.message.reply_text(f"✓ Judul topik berhasil diubah menjadi: <b>{new_title}</b>", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Gagal mengubah judul topik: {e}")
        if update.message:
            await update.message.reply_text(f"❌ Gagal mengubah judul topik: {e}")


async def handle_delete_topic_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /deletetopic command - deletes the current forum topic."""
    chat = update.effective_chat
    if not chat:
        return

    raw_thread = getattr(update.message, "message_thread_id", None)
    thread_id = raw_thread if isinstance(raw_thread, int) else None

    if thread_id is None:
        if update.message:
            await update.message.reply_text(
                "⚠️ Perintah <code>/deletetopic</code> harus dijalankan di dalam topik yang ingin Anda hapus.",
                parse_mode=ParseMode.HTML
            )
        return

    db = get_db()
    binding = db.get_topic_binding(chat.id, thread_id)
    topic_name = binding.get("topic_name") if binding else f"Topik #{thread_id}"

    bot: Bot = context.bot
    try:
        db.delete_topic_binding(chat.id, thread_id)
        await bot.delete_forum_topic(chat_id=chat.id, message_thread_id=thread_id)
        logger.info(f"Deleted forum topic {thread_id} ('{topic_name}') in chat {chat.id}")
    except Exception as e:
        logger.error(f"Gagal menghapus topik {thread_id}: {e}")
        if update.message:
            await update.message.reply_text(f"❌ Gagal menghapus topik: {e}")


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
