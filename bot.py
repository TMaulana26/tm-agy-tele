#!/usr/bin/env python3
"""
Antigravity Telegram Bot (Native agy CLI Subprocess Engine).
Features parity with Hermes Agent in Telegram:
1. Ingress gate: whitelist authorization + anti-replay admission for every update.
2. Bot API 9.4 Private Chat Topics (/topic) for multi-session parallel DMs.
3. Turn lifecycle reactions (👀 -> 👍/👎), quiet pinning, and in-place status bubble.
4. Interactive Model Picker (/model) using official agy models.
5. Multi-Media Pipeline with Voice Bubble audio & strict Media Path Traversal Guard.
6. DNS-over-HTTPS (DoH) & Host/SNI preserving fallback transport.
7. Polling stall watchdog with automatic polling restart.
8. Interactive Inline Approval (Hermes Guard) & /cancel process-tree killing.
9. Follow-up message queue while a turn is running.
"""

from __future__ import annotations

import os
import sys
import re
import time
import html
import uuid
import shutil
import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple, List, Any

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    BotCommand,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    ApplicationHandlerStop,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    TypeHandler,
    filters,
)

# Central Configuration
from config import (
    TELEGRAM_BOT_TOKEN,
    ALLOWED_USER_IDS,
    AGY_BIN_PATH,
    WORKSPACE_DIR,
    APPROVAL_MODE,
    APPROVAL_TIMEOUT_SECONDS,
    AGY_TIMEOUT_SECONDS,
    DEFAULT_MODEL,
    TELEGRAM_FALLBACK_TRANSPORT,
    TELEGRAM_PROXY,
    AGY_SKIP_PERMISSIONS,
    TELEGRAM_DEBOUNCE_SECONDS,
    TELEGRAM_MEDIA_GROUP_SECONDS,
    TELEGRAM_REQUIRE_MENTION_IN_GROUPS,
    TELEGRAM_WEBHOOK_URL,
    TELEGRAM_WEBHOOK_SECRET,
    TELEGRAM_WEBHOOK_PORT,
    TELEGRAM_WEBHOOK_LISTEN,
    TELEGRAM_POLLING_STALL_TIMEOUT,
    TELEGRAM_STT_ENABLED,
    TELEGRAM_WHISPER_MODEL,
    resolve_workspace_dir,
    build_cli_prompt,
)


def get_upload_dir() -> Path:
    """Memastikan dan mengembalikan direktori penyimpanan berkas unggahan Telegram (.telegram_uploads)."""
    upload_dir = Path(WORKSPACE_DIR) / ".telegram_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


# Database State
from database.state import get_db

# Telegram Platform Components
from tele.network import (
    build_resilient_request,
    redact_telegram_error_text,
    acquire_instance_lock,
    release_instance_lock,
    PollingStallWatchdog,
)
from tele.admission import check_update_admission, notify_progress, set_progress_listener
from tele.entities import expand_link_entities, clean_bot_mentions, format_reply_context
from tele.clarify import (
    detect_clarify_options,
    build_clarify_keyboard,
    register_clarification,
    get_clarification,
    pop_clarification,
)
from tele.stt import transcribe_audio_file
from tele.media import (
    validate_media_delivery_path,
    extract_media_paths,
    send_outbound_media,
)
from tele.streaming import split_message
from tele.formatters import markdown_to_telegram_html, append_duration_badge, strip_html_for_plain_text
from tele.topics import (
    handle_topic_command,
    handle_topics_command,
    handle_title_command,
    handle_delete_topic_command,
    get_conversation_for_message,
    bind_conversation_to_topic,
    auto_rename_forum_topic,
)
from tele.picker import handle_model_command, handle_model_callback, send_model_picker
from tele.gating import should_process_chat_message
from tele.coalescing import TextDebouncer, MediaGroupCollector

# Core Engine & Approval (some names are re-exported for tests and back-compat)
from core.agy_engine import (
    user_conversations,
    user_processes,
    user_locks,
    user_tasks,
    get_user_lock,
    list_brain_bases,
    recover_last_response_from_transcript,
    run_agy_cli,
    terminate_process_tree,
)
from core.approval import (
    is_hardline_blocked,
    is_destructive_prompt,
    pending_approvals,
    request_user_approval,
    handle_approval_callback,
)
from core.usage import (
    format_progress_bar,
    format_relative_time,
    format_usage_data,  # re-exported for tests
    fetch_agy_usage_report,
    is_quota_inquiry,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("antigravity-tele-bot")

# Follow-up prompts received while a turn is running: user_id -> [(update, context, prompt)]
MAX_QUEUED_PROMPTS = 5
user_pending_prompts: Dict[int, List[Tuple[Update, ContextTypes.DEFAULT_TYPE, str]]] = {}


def is_user_busy(user_id: int) -> bool:
    """Checks whether the user currently has an active running turn task or locked mutex."""
    task = user_tasks.get(user_id)
    if task and not task.done():
        return True
    lock = user_locks.get(user_id)
    if lock and lock.locked():
        return True
    return False


async def _coalesced_dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str) -> None:
    """Dispatches coalesced/debounced prompts into agent turn execution."""
    await _dispatch_agent_turn(update, context, prompt)


text_debouncer = TextDebouncer(
    debounce_seconds=TELEGRAM_DEBOUNCE_SECONDS,
    dispatch_callback=_coalesced_dispatch,
    is_busy_func=is_user_busy
)

media_group_collector = MediaGroupCollector(
    window_seconds=TELEGRAM_MEDIA_GROUP_SECONDS,
    upload_dir_func=get_upload_dir,
    dispatch_callback=_coalesced_dispatch,
    is_busy_func=is_user_busy
)

# Polling Stall Watchdog (Heartbeat & CLOSE-WAIT hang prevention)
stall_watchdog = PollingStallWatchdog(
    stall_timeout=TELEGRAM_POLLING_STALL_TIMEOUT,
    probe_interval=20.0
)
set_progress_listener(stall_watchdog.record_progress)


# ==============================================================================
# AUTHORIZATION, INGRESS GATE & UTILITIES
# ==============================================================================
def is_authorized(update: Update) -> bool:
    """Verifies whether the sender is in ALLOWED_USER_IDS."""
    user = update.effective_user
    if user is None:
        return False
    return user.id in ALLOWED_USER_IDS


def _is_start_command(update: Update) -> bool:
    msg = update.message
    text = msg.text if msg is not None and isinstance(getattr(msg, "text", None), str) else ""
    if not text.strip():
        return False
    return text.split(maxsplit=1)[0].split("@", 1)[0].lower() == "/start"


async def ingress_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Runs before every handler (group -1).
    1. Rejects updates from users outside ALLOWED_USER_IDS (only /start is let through so a
       stranger can learn their Telegram ID), covering commands, media and button callbacks.
    2. Drops redelivered updates via persistent anti-replay receipts.
    """
    notify_progress()

    if not (is_authorized(update) or _is_start_command(update)):
        user = update.effective_user
        logger.warning(f"Update ditolak dari pengguna di luar whitelist: {user.id if user else 'Unknown'}")
        if update.callback_query is not None:
            try:
                await update.callback_query.answer("⛔ Akses ditolak.", show_alert=True)
            except Exception:
                pass
        raise ApplicationHandlerStop

    if not check_update_admission(update, context):
        raise ApplicationHandlerStop


def get_effective_thread_id(update: Update) -> Optional[int]:
    """Extracts message_thread_id reliably whether update is Message or CallbackQuery."""
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if msg:
        tid = getattr(msg, "message_thread_id", None)
        if isinstance(tid, int):
            return tid
    return None


def get_root_conversation(user_id: int) -> Optional[str]:
    """Returns the user's root-chat (non-topic) conversation id, restoring it from SQLite after a restart."""
    conv = user_conversations.get(user_id)
    if conv:
        return conv
    try:
        conv = get_db().get_root_conversation(user_id)
    except Exception as e:
        logger.debug(f"Could not load root conversation for {user_id}: {e}")
        return None
    if conv:
        user_conversations[user_id] = conv
    return conv


def set_root_conversation(user_id: int, conv_id: Optional[str]) -> None:
    """Stores (or clears when conv_id is falsy) the user's root-chat conversation id."""
    if conv_id:
        user_conversations[user_id] = conv_id
    else:
        user_conversations.pop(user_id, None)
    try:
        db = get_db()
        if conv_id:
            db.set_root_conversation(user_id, conv_id)
        else:
            db.delete_root_conversation(user_id)
    except Exception as e:
        logger.warning(f"Gagal menyimpan sesi root untuk user {user_id}: {e}")


async def safe_send_message(
    bot,
    chat_id: int,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: Optional[str] = ParseMode.HTML,
    disable_notification: bool = False,
    reply_to_message_id: Optional[int] = None,
    message_thread_id: Optional[int] = None
) -> Optional[Message]:
    """
    Safely sends a message to Telegram with automatic fallback to plain text.
    Handles stale reply targets and missing thread anchors gracefully.
    """
    effective_thread = message_thread_id if isinstance(message_thread_id, int) else None
    effective_reply = reply_to_message_id if isinstance(reply_to_message_id, int) else None

    send_kwargs: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": reply_markup,
        "parse_mode": parse_mode,
        "disable_notification": disable_notification,
    }
    if effective_reply is not None:
        send_kwargs["reply_to_message_id"] = effective_reply
    if effective_thread is not None:
        send_kwargs["message_thread_id"] = effective_thread

    try:
        return await bot.send_message(**send_kwargs)
    except BadRequest as e:
        err_str = str(e).lower()

        # 1. If error because reply target invalid, retry with formatting but without reply_to
        if "repl" in err_str and effective_reply is not None:
            logger.warning(f"Reply target {effective_reply} tidak valid ({e}). Mengirim ulang tanpa reply_to...")
            retry_kwargs = dict(send_kwargs)
            retry_kwargs.pop("reply_to_message_id", None)
            try:
                return await bot.send_message(**retry_kwargs)
            except BadRequest as e_retry:
                e = e_retry
                err_str = str(e).lower()
            except Exception as e_retry:
                logger.error(f"Gagal mengirim ulang pesan: {e_retry}")
                return None

        # 1b. If thread not found (topic deleted in Telegram), prune stale topic binding and retry without thread_id
        if "thread" in err_str and effective_thread is not None:
            logger.warning(f"Thread {effective_thread} tidak ditemukan ({e}). Memangkas binding usang dan mengirim ulang...")
            try:
                get_db().prune_stale_topic_binding(chat_id, effective_thread)
            except Exception:
                pass
            retry_thread_kwargs = dict(send_kwargs)
            retry_thread_kwargs.pop("message_thread_id", None)
            try:
                return await bot.send_message(**retry_thread_kwargs)
            except BadRequest as e_thread:
                e = e_thread
                err_str = str(e).lower()
            except Exception as e_thread:
                logger.error(f"Gagal mengirim ulang tanpa thread: {e_thread}")
                return None

        # 2. Fallback to plain text if formatting error occurred
        logger.warning(f"Error saat send_message ({e}). Mengirim ulang sebagai plain text...")
        target_reply = effective_reply if "repl" not in err_str else None
        plain_kwargs = dict(send_kwargs)
        plain_kwargs["parse_mode"] = None
        plain_kwargs["text"] = strip_html_for_plain_text(str(send_kwargs.get("text", "")))
        if target_reply is not None:
            plain_kwargs["reply_to_message_id"] = target_reply
        else:
            plain_kwargs.pop("reply_to_message_id", None)

        try:
            return await bot.send_message(**plain_kwargs)
        except BadRequest as e2:
            if "repl" in str(e2).lower() and "reply_to_message_id" in plain_kwargs:
                plain_kwargs.pop("reply_to_message_id", None)
                try:
                    return await bot.send_message(**plain_kwargs)
                except Exception:
                    pass
            logger.error(f"Gagal total mengirim pesan plain text: {e2}")
            return None
        except Exception as e2:
            logger.error(f"Gagal total mengirim pesan plain text: {e2}")
            return None
    except Exception as e:
        logger.error(f"Unexpected error in safe_send_message: {e}")
        return None


async def safe_edit_message(
    msg: Message,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: Optional[str] = ParseMode.HTML
) -> bool:
    """Safely edits an existing message with plain text fallback."""
    try:
        await msg.edit_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
        return True
    except BadRequest as e:
        err_msg = str(e).lower()
        if "not modified" in err_msg:
            return True
        logger.warning(f"Formatting parse error saat edit_message ({e}). Mengedit ulang sebagai plain text.")
        try:
            await msg.edit_text(
                text=strip_html_for_plain_text(text),
                reply_markup=reply_markup,
                parse_mode=None
            )
            return True
        except Exception as e2:
            logger.error(f"Gagal edit_message plain text: {e2}")
            return False
    except Exception as e:
        logger.error(f"Error tidak terduga di safe_edit_message: {e}")
        return False


async def send_typing_and_progress(
    bot,
    chat_id: int,
    status_msg: Optional[Message],
    stop_event: asyncio.Event,
    message_thread_id: Optional[int] = None
):
    """Periodically sends typing action and updates status bubble."""
    start_time = time.time()
    effective_thread = message_thread_id if isinstance(message_thread_id, int) else None
    while not stop_event.is_set():
        try:
            kwargs = {"chat_id": chat_id, "action": ChatAction.TYPING}
            if effective_thread is not None:
                kwargs["message_thread_id"] = effective_thread
            await bot.send_chat_action(**kwargs)
        except Exception:
            pass

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=4.0)
        except asyncio.TimeoutError:
            if status_msg and not stop_event.is_set():
                elapsed = int(time.time() - start_time)
                await safe_edit_message(
                    status_msg,
                    f"⏳ *Antigravity sedang berpikir & memproses...* `({elapsed}s)`\n"
                    f"_Kirim /cancel untuk menghentikan proses kapan saja._",
                    parse_mode=ParseMode.MARKDOWN
                )


# ==============================================================================
# REACTION & PIN TURN LIFECYCLE
# ==============================================================================
async def on_turn_start(bot, chat_id: int, message_id: Optional[int]) -> None:
    """Sets initial 'eyes' reaction and quietly pins user message during agent turn."""
    if not isinstance(message_id, int):
        return
    if hasattr(bot, "set_message_reaction"):
        try:
            await bot.set_message_reaction(chat_id=chat_id, message_id=message_id, reaction="👀")
        except Exception as e:
            logger.debug(f"Could not set turn start reaction: {e}")

    if hasattr(bot, "pin_chat_message"):
        try:
            await bot.pin_chat_message(chat_id=chat_id, message_id=message_id, disable_notification=True)
        except Exception as e:
            logger.debug(f"Could not pin message {message_id}: {e}")


async def on_turn_complete(
    bot,
    chat_id: int,
    message_id: Optional[int],
    success: bool = True,
    cancelled: bool = False
) -> None:
    """Swaps reaction to 👍 / 👎 (or clears if cancelled) and unpins user message."""
    if not isinstance(message_id, int):
        return
    if hasattr(bot, "set_message_reaction"):
        try:
            if cancelled:
                await bot.set_message_reaction(chat_id=chat_id, message_id=message_id, reaction=None)
            else:
                final_emoji = "👍" if success else "👎"
                await bot.set_message_reaction(chat_id=chat_id, message_id=message_id, reaction=final_emoji)
        except Exception as e:
            logger.debug(f"Could not update turn complete reaction: {e}")

    if hasattr(bot, "unpin_chat_message"):
        try:
            await bot.unpin_chat_message(chat_id=chat_id, message_id=message_id)
        except Exception as e:
            logger.debug(f"Could not unpin message {message_id}: {e}")


# ==============================================================================
# COMMAND HANDLERS
# ==============================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        user_id = update.effective_user.id if update.effective_user else "Unknown"
        logger.warning(f"Akses ditolak untuk User ID: {user_id}")
        await update.message.reply_text(
            f"⛔ <b>Akses Ditolak!</b>\n\n"
            f"ID Telegram Anda: <code>{user_id}</code>\n"
            f"Akun Anda belum terdaftar di whitelist bot.",
            parse_mode=ParseMode.HTML
        )
        return

    welcome_text = (
        "🤖 **Halo Kang! Antigravity Telegram Bot Aktif.**\n\n"
        "Bot ini terhubung langsung ke **Native `agy` CLI Engine** di VPS/Host dengan sesi login Google Antigravity Akang.\n"
        "Dilengkapi fitur **Hermes Guard (Interactive Approval)** untuk mencegah eksekusi instruksi katastropik.\n\n"
        "**Perintah Tersedia:**\n"
        "• `/model`  - Pilih model AI aktif (Gemini 3.8 Flash, Claude Sonnet 4.6, dll)\n"
        "• `/topic`  - Buka mode Multi-Session DM (Private Forum Topics)\n"
        "• `/usage`  - Cek kuota & sisa limit model (Gemini, Claude, GPT)\n"
        "• `/status` - Cek engine, memory ID, workspace, dan status proses\n"
        "• `/cancel` - Hentikan paksa proses `agy` yang sedang berjalan\n"
        "• `/reset`  - Hapus memori percakapan & mulai sesi baru\n"
        "• `/help`   - Panduan lengkap fitur & media transfer\n\n"
        "Silakan kirim pesan atau instruksi koding/perintah apa pun langsung di sini."
    )
    await safe_send_message(context.bot, update.effective_chat.id, welcome_text, parse_mode=ParseMode.MARKDOWN)


def _help_nav_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("« Kembali ke Menu Bantuan", callback_data="help:main"),
            InlineKeyboardButton("✖️ Tutup", callback_data="help:close")
        ]
    ])


def get_help_menu_content(category: str = "main") -> Tuple[str, InlineKeyboardMarkup]:
    """Builds interactive Help Center views with categorized guidance and navigation buttons."""
    if category == "topics":
        text = (
            "💬 <b>Panduan Topik & Multi-Session DM (Ala Hermes)</b>\n\n"
            "Fitur ini membagi percakapan menjadi sub-topik terisolasi layaknya forum di dalam DM:\n\n"
            "• <code>/topic</code> — Cek status & inisialisasi Threaded Mode.\n"
            "• <code>/topic &lt;nama&gt;</code> — Buat topik baru langsung dengan nama (contoh: <code>/topic Refactor Auth</code>).\n"
            "• <code>/topics</code> — Lihat daftar seluruh topik aktif beserta ID thread-nya.\n"
            "• <code>/title &lt;nama baru&gt;</code> — Ganti nama topik yang sedang dibuka.\n"
            "• <code>/deletetopic</code> (alias: <code>/rmtopic</code>) — Hapus topik saat ini beserta riwayat binding-nya.\n\n"
            "💡 <i>Tiap topik memiliki memori percakapan independen tanpa mencemari topik lain!</i>"
        )
        return text, _help_nav_keyboard()

    if category == "sessions":
        text = (
            "🗂️ <b>Panduan Sesi & Memori Percakapan</b>\n\n"
            "Antigravity menyimpan riwayat percakapan di sistem dalam format multi-turn:\n\n"
            "• <code>/sessions</code> — Tampilkan riwayat ID sesi percakapan terbaru.\n"
            "• <code>/resume &lt;id_sesi&gt;</code> — Lanjutkan kembali konteks percakapan lama.\n"
            "• <code>/reset</code> (alias: <code>/new</code>, <code>/clear</code>) — Hapus memori aktif & mulai sesi fresh.\n"
            "• <code>/cancel</code> — Hentikan paksa proses CLI yang sedang berjalan (<code>SIGTERM</code>/<code>SIGKILL</code>) beserta antrean pesan.\n\n"
            "💡 <i>Pesan yang dikirim saat tugas masih berjalan otomatis diantrikan dan diproses setelahnya.</i>"
        )
        return text, _help_nav_keyboard()

    if category == "security":
        text = (
            "🛡️ <b>Panduan Keamanan & Sandbox (Hermes Guard)</b>\n\n"
            "Bot dilengkapi pengaman berlapis untuk melindungi VPS & data Akang:\n\n"
            "• <b>Whitelist</b>: Semua pesan, perintah, dan tombol dari akun di luar whitelist ditolak.\n"
            "• <b>Interactive Approval</b>: Instruksi berisiko (drop table, rm -rf, git force) wajib disetujui manual via tombol Approve / Deny (timeout 120 detik, fail-closed).\n"
            "• <b>Hardline Blocklist</b>: Perintah katastropik sistem (<code>rm -rf /</code>, <code>mkfs</code>, <code>dd</code>, <code>shutdown</code>) <b>DIBLOKIR TOTAL</b>.\n"
            "• <b>Media Guard</b>: Pengiriman file sensitif (<code>.env</code>, <code>state.db</code>, <code>auth.json</code>, SSH keys, <code>*.pem</code>) dilarang secara ketat.\n\n"
            "⚠️ <i>Approval & blocklist memeriksa teks instruksi Anda, bukan setiap aksi agent. "
            "Jangan meminta agent memproses dokumen dari sumber yang tidak dipercaya.</i>"
        )
        return text, _help_nav_keyboard()

    if category == "system":
        text = (
            "⚙️ <b>Perintah Sistem & Utilitas</b>\n\n"
            "• <code>/status</code> — Informasi engine, PID proses aktif, path workspace, & status memory.\n"
            "• <code>/usage</code> (alias: <code>/limit</code>) — Tampilkan sisa kuota model & waktu refresh.\n"
            "• <code>/model</code> — Buka pemilih model interaktif (Gemini 3.8, Claude, dll).\n"
            "• <code>/cancel</code> — Hentikan eksekusi perintah yang sedang berjalan."
        )
        return text, _help_nav_keyboard()

    # Default: "main"
    text = (
        "📖 <b>Antigravity CLI — Interactive Help Center</b>\n\n"
        "Selamat datang di pusat bantuan interaktif Antigravity Telegram Bot! "
        "Bot ini memberi Anda akses penuh ke native <code>agy</code> CLI dengan keamanan ala Hermes.\n\n"
        "Silakan pilih kategori panduan di bawah atau klik tombol aksi cepat:"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🤖 Pilih Model", callback_data="help:act_model"),
            InlineKeyboardButton("📊 Cek Kuota", callback_data="help:act_usage")
        ],
        [
            InlineKeyboardButton("💬 Panduan Topik", callback_data="help:cat_topics"),
            InlineKeyboardButton("🗂️ Panduan Sesi", callback_data="help:cat_sessions")
        ],
        [
            InlineKeyboardButton("🛡️ Keamanan & Guard", callback_data="help:cat_security"),
            InlineKeyboardButton("⚙️ Perintah Sistem", callback_data="help:cat_system")
        ],
        [
            InlineKeyboardButton("ℹ️ Status Engine", callback_data="help:act_status"),
            InlineKeyboardButton("✨ Sesi Baru", callback_data="help:act_reset")
        ],
        [
            InlineKeyboardButton("✖️ Tutup Bantuan", callback_data="help:close")
        ]
    ])
    return text, keyboard


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    text, reply_markup = get_help_menu_content("main")
    await safe_send_message(
        context.bot,
        update.effective_chat.id,
        text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML,
        message_thread_id=get_effective_thread_id(update)
    )


def _usage_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="usage_refresh"),
            InlineKeyboardButton("✖️ Tutup", callback_data="msg_close")
        ]
    ])


async def usage_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    chat_id = update.effective_chat.id
    thread_id = get_effective_thread_id(update)

    status_msg = await safe_send_message(
        context.bot,
        chat_id,
        "⏳ *Mengambil data kuota model dari Antigravity CLI...*",
        parse_mode=ParseMode.MARKDOWN,
        message_thread_id=thread_id
    )

    usage_kb = _usage_keyboard()
    try:
        report_html = await fetch_agy_usage_report()
    except Exception as e:
        logger.error(f"Error handling /usage: {e}", exc_info=True)
        report_html = f"❌ Gagal mengambil data kuota:\n<code>{html.escape(str(e))}</code>"

    if status_msg:
        await safe_edit_message(status_msg, report_html, reply_markup=usage_kb, parse_mode=ParseMode.HTML)
    else:
        await safe_send_message(context.bot, chat_id, report_html, reply_markup=usage_kb, parse_mode=ParseMode.HTML, message_thread_id=thread_id)


def _resolve_current_conversation(user_id: int, chat_id: int, thread_id: Optional[int]) -> Optional[str]:
    """Topic conversation inside a thread, root conversation otherwise."""
    if thread_id is not None:
        binding = get_db().get_topic_binding(chat_id, thread_id)
        return (binding or {}).get("conv_id") or None
    return get_root_conversation(user_id)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    thread_id = get_effective_thread_id(update)

    db = get_db()
    current_conv = _resolve_current_conversation(user_id, chat_id, thread_id)
    selected_model = db.get_user_model(user_id, chat_id, thread_id or "root") or DEFAULT_MODEL
    running_proc = user_processes.get(user_id)
    is_proc_running = running_proc is not None and running_proc.returncode is None
    bin_exists = os.path.exists(AGY_BIN_PATH) or shutil.which(AGY_BIN_PATH) is not None
    queued = len(user_pending_prompts.get(user_id, []))

    status_text = (
        "📊 **Status Sistem Antigravity Bot (CLI Engine)**\n\n"
        f"• **Engine**: `Native agy CLI Subprocess`\n"
        f"• **Model Aktif**: `{selected_model}`\n"
        f"• **Binary Path**: `{AGY_BIN_PATH}` ({'✅ Ditemukan' if bin_exists else '❌ Tidak Ditemukan!'})\n"
        f"• **Workspace Path**: `{WORKSPACE_DIR}`\n"
        f"• **Approval Mode**: `{APPROVAL_MODE}`\n"
        f"• **Approval Timeout**: `{APPROVAL_TIMEOUT_SECONDS} detik`\n"
        f"• **Execution Timeout**: `{AGY_TIMEOUT_SECONDS} detik`\n"
        f"• **Sesi Percakapan**: `{current_conv if current_conv else 'Belum ada (Fresh)'}`\n"
        f"• **Topik Thread**: `{'Thread ' + str(thread_id) if thread_id is not None else 'Root Chat'}`\n"
        f"• **Status Tugas Saat Ini**: `{'⏳ Sedang Berjalan (PID: ' + str(running_proc.pid) + ')' if is_proc_running else '💤 Idle'}`\n"
        f"• **Antrean Pesan**: `{queued}`\n"
        f"• **Whitelist User ID**: `{user_id}` (Terverifikasi)\n"
        f"• **Pending Approvals**: `{len(pending_approvals)}`"
    )
    status_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🤖 Ganti Model", callback_data="help:act_model"),
            InlineKeyboardButton("✖️ Tutup", callback_data="msg_close")
        ]
    ])
    await safe_send_message(context.bot, chat_id, status_text, reply_markup=status_kb, parse_mode=ParseMode.MARKDOWN, message_thread_id=thread_id)


def _cancel_pending_approvals(user_id: int) -> List[dict]:
    """Cancels the user's pending approval futures and returns their registry entries."""
    cancelled = []
    for info in list(pending_approvals.values()):
        if info.get("user_id") != user_id:
            continue
        fut = info.get("future")
        if fut and not fut.done():
            fut.cancel()
        cancelled.append(info)
    return cancelled


async def perform_reset_execution(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Executes actual session reset, PID cancellation, and memory purge."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    thread_id = get_effective_thread_id(update)

    # 1. Hentikan proses yang sedang berjalan beserta antreannya
    user_pending_prompts.pop(user_id, None)
    proc = user_processes.get(user_id)
    if proc and proc.returncode is None:
        try:
            terminate_process_tree(proc)
        except Exception:
            pass

    task = user_tasks.get(user_id)
    if task and not task.done():
        task.cancel()

    # 2. Reset memori
    if thread_id is not None:
        db = get_db()
        binding = db.get_topic_binding(chat_id, thread_id)
        old_conv = binding.get("conv_id") if binding else None
        db.set_topic_binding(chat_id, thread_id, conv_id="", topic_name="Umum / General")
    else:
        old_conv = get_root_conversation(user_id)
        set_root_conversation(user_id, None)

    # 3. Batalkan approval tertunda
    _cancel_pending_approvals(user_id)

    reset_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✖️ Tutup", callback_data="msg_close")
        ]
    ])
    await safe_send_message(
        context.bot,
        chat_id,
        "🔄 **Sesi Percakapan Direset!**\n"
        f"Riwayat percakapan lama ({old_conv[:8] + '...' if old_conv else 'None'}) telah dibersihkan. "
        "Instruksi berikutnya akan memulai percakapan baru di `agy`.",
        reply_markup=reset_kb,
        parse_mode=ParseMode.MARKDOWN,
        message_thread_id=thread_id
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /reset command to clear conversation memory and cancel running tasks."""
    if not is_authorized(update):
        return

    await perform_reset_execution(update, context)


def _format_age(mtime: float) -> str:
    """Formats an epoch timestamp as a short relative age, e.g. '5m lalu'."""
    seconds = max(0, int(time.time() - mtime))
    if seconds < 60:
        return "baru saja"
    if seconds < 3600:
        return f"{seconds // 60}m lalu"
    if seconds < 86400:
        return f"{seconds // 3600}j lalu"
    return f"{seconds // 86400}h lalu"


async def sessions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists recent conversation sessions on host and current active binding."""
    if not is_authorized(update):
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    thread_id = get_effective_thread_id(update)
    current_conv = _resolve_current_conversation(user_id, chat_id, thread_id)

    discovered = []
    seen_ids = set()
    for base in list_brain_bases():
        try:
            for item in base.iterdir():
                if item.is_dir() and item.name not in seen_ids:
                    t_path = item / ".system_generated" / "logs" / "transcript.jsonl"
                    if t_path.is_file():
                        discovered.append((t_path.stat().st_mtime, item.name))
                        seen_ids.add(item.name)
        except (PermissionError, OSError):
            continue

    discovered.sort(key=lambda x: x[0], reverse=True)
    recent = discovered[:8]

    close_kb = InlineKeyboardMarkup([[InlineKeyboardButton("✖️ Tutup", callback_data="msg_close")]])
    if not recent:
        msg = (
            "🗂️ <b>Riwayat Sesi Antigravity:</b>\n\n"
            "Belum ada sesi percakapan yang ditemukan pada host ini.\n"
            f"Sesi aktif saat ini: <code>{current_conv or 'Fresh / Belum dimulai'}</code>"
        )
        await safe_send_message(context.bot, chat_id, msg, reply_markup=close_kb, parse_mode=ParseMode.HTML, message_thread_id=thread_id)
        return

    lines = ["🗂️ <b>Daftar Sesi Percakapan Antigravity Terbaru:</b>\n"]
    for idx, (mtime, cid) in enumerate(recent, 1):
        rel_time = _format_age(mtime)
        is_active = " 👈 <i>(Aktif)</i>" if cid == current_conv else ""
        lines.append(f"{idx}. <code>{html.escape(cid)}</code> ({rel_time}){is_active}")

    lines.append("\n💡 <i>Gunakan perintah:</i> <code>/resume &lt;id_sesi&gt;</code> <i>untuk berpindah ke sesi tersebut.</i>")
    await safe_send_message(context.bot, chat_id, "\n".join(lines), reply_markup=close_kb, parse_mode=ParseMode.HTML, message_thread_id=thread_id)


async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switches active session to a specified conversation ID."""
    if not is_authorized(update):
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    thread_id = get_effective_thread_id(update)

    args = context.args if context.args else []
    target_id = args[0].strip() if args else ""

    if not target_id:
        await safe_send_message(
            context.bot,
            chat_id,
            "⚠️ <b>Format Perintah Salah</b>\n\n"
            "Gunakan: <code>/resume &lt;id_sesi&gt;</code>\n"
            "Contoh: <code>/resume 735b76d9-c9ca-405f-b911-00c17daa777f</code>\n\n"
            "Ketik <code>/sessions</code> untuk melihat daftar ID sesi yang tersedia.",
            parse_mode=ParseMode.HTML,
            message_thread_id=thread_id
        )
        return

    if thread_id is not None:
        get_db().set_topic_binding(chat_id, thread_id, conv_id=target_id)
    else:
        set_root_conversation(user_id, target_id)

    await safe_send_message(
        context.bot,
        chat_id,
        f"✓ <b>Berhasil beralih ke sesi:</b>\n<code>{html.escape(target_id)}</code>\n\n"
        "Instruksi berikutnya akan melanjutkan konteks dan riwayat dari sesi tersebut.",
        parse_mode=ParseMode.HTML,
        message_thread_id=thread_id
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    thread_id = get_effective_thread_id(update)
    cancelled_anything = bool(user_pending_prompts.pop(user_id, None))

    proc = user_processes.get(user_id)
    if proc and proc.returncode is None:
        try:
            terminate_process_tree(proc)
            await asyncio.sleep(0.5)
            if proc.returncode is None:
                terminate_process_tree(proc, force=True)
            cancelled_anything = True
            logger.info(f"Subprocess agy (PID: {proc.pid}) untuk user {user_id} dimatikan via /cancel.")
        except Exception as e:
            logger.warning(f"Error mematikan proses {user_id}: {e}")

    task = user_tasks.get(user_id)
    if task and not task.done():
        task.cancel()
        cancelled_anything = True

    for info in _cancel_pending_approvals(user_id):
        msg = info.get("message")
        if msg:
            try:
                await safe_edit_message(
                    msg,
                    f"{info.get('text', '')}\n\nStatus: 🛑 **DIBATALKAN VIA /cancel**"
                )
            except Exception:
                pass
        cancelled_anything = True

    if cancelled_anything:
        await safe_send_message(
            context.bot,
            chat_id,
            "🛑 **Tugas berhasil dibatalkan!** Subprocess `agy`, eksekusi, dan antrean pesan telah dihentikan.",
            parse_mode=ParseMode.MARKDOWN,
            message_thread_id=thread_id
        )
    else:
        await safe_send_message(
            context.bot,
            chat_id,
            "ℹ️ Tidak ada tugas atau proses `agy` yang sedang berjalan saat ini.",
            parse_mode=ParseMode.MARKDOWN,
            message_thread_id=thread_id
        )


# ==============================================================================
# CALLBACK QUERY HANDLERS
# ==============================================================================
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Central callback router for approval confirmations, model selection, and help center."""
    query = update.callback_query
    if query is None or not query.data:
        return

    # Defense in depth: ingress_gate already rejects non-whitelisted clickers
    if not is_authorized(update):
        try:
            await query.answer("⛔ Akses ditolak.", show_alert=True)
        except Exception:
            pass
        return

    data = query.data
    if data.startswith("appr:") or data.startswith("deny:"):
        await handle_approval_callback(update, context)
    elif data.startswith("cl:"):
        await handle_clarify_callback(update, context)
    elif data.startswith("model_"):
        await handle_model_callback(update, context)
    elif data.startswith("help:"):
        await handle_help_callback(update, context)
    elif data == "msg_close":
        await query.answer()
        try:
            await query.message.delete()
        except Exception:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
    elif data == "usage_refresh":
        await query.answer("Memperbarui data kuota...")
        try:
            report_html = await fetch_agy_usage_report()
            await safe_edit_message(query.message, report_html, reply_markup=_usage_keyboard(), parse_mode=ParseMode.HTML)
        except Exception as e:
            await query.answer(f"Gagal refresh: {e}", show_alert=True)


async def handle_clarify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles taps on clarify single-choice question buttons (Hermes standard)."""
    query = update.callback_query
    if query is None or not query.data:
        return

    parts = query.data.split(":")
    if len(parts) < 3:
        return

    clarify_id = parts[1]
    choice_idx = parts[2]
    user_id = update.effective_user.id

    clarify_data = get_clarification(clarify_id)
    if not clarify_data:
        await query.answer("Pertanyaan ini sudah dijawab atau kedaluwarsa.", show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    owner_id = clarify_data.get("user_id")
    if owner_id is not None and owner_id != user_id:
        await query.answer("⛔ Pilihan ini milik pengguna lain.", show_alert=True)
        return

    pop_clarification(clarify_id)

    if choice_idx == "other":
        await query.answer()
        await safe_edit_message(
            query.message,
            "✏️ <b>Mode Jawaban Manual:</b>\nSilakan ketik jawaban Anda secara langsung di chat ini.",
            reply_markup=None,
            parse_mode=ParseMode.HTML
        )
        return

    try:
        idx = int(choice_idx)
        selected_text = clarify_data["choices"][idx]
    except (ValueError, IndexError, KeyError):
        selected_text = f"Pilihan #{choice_idx}"

    await query.answer(f"Memilih: {selected_text[:30]}")
    await safe_edit_message(
        query.message,
        f"✅ <i>Pilihan dipilih:</i> <b>{html.escape(selected_text)}</b>",
        reply_markup=None,
        parse_mode=ParseMode.HTML
    )

    await _dispatch_agent_turn(update, context, selected_text)


async def handle_help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles interactive navigation in the Help Center."""
    query = update.callback_query
    if query is None or not query.data:
        return
    await query.answer()

    action = query.data.split(":", 1)[1]
    chat_id = query.message.chat_id
    thread_id = get_effective_thread_id(update)

    if action.startswith("cat_"):
        text, markup = get_help_menu_content(action[4:])
        await safe_edit_message(query.message, text, reply_markup=markup, parse_mode=ParseMode.HTML)
    elif action == "main":
        text, markup = get_help_menu_content("main")
        await safe_edit_message(query.message, text, reply_markup=markup, parse_mode=ParseMode.HTML)
    elif action == "close":
        try:
            await query.message.delete()
        except Exception:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
    elif action == "act_model":
        await send_model_picker(chat_id, thread_id, page=0, context=context, user_id=update.effective_user.id)
    elif action == "act_usage":
        try:
            report_html = await fetch_agy_usage_report()
            await safe_send_message(context.bot, chat_id, report_html, reply_markup=_usage_keyboard(), parse_mode=ParseMode.HTML, message_thread_id=thread_id)
        except Exception as e:
            await safe_send_message(context.bot, chat_id, f"❌ Gagal mengambil kuota: {html.escape(str(e))}", message_thread_id=thread_id)
    elif action == "act_status":
        await status_command(update, context)
    elif action == "act_reset":
        await reset_command(update, context)
    elif action == "act_cancel":
        await cancel_command(update, context)


# ==============================================================================
# AGENT WORKFLOW & MESSAGE EXECUTION
# ==============================================================================
_FILE_INTENT_PATTERNS = [
    r"(?:kirim|unduh|download|minta|lihat)\s+(?:file|berkas|dokumen)?\s*[`'\"]?([a-zA-Z0-9_\-\.\/]+?\.[a-zA-Z0-9]+)[`'\"]?",
    r"(?:kirimkan|lihatkan)\s+(?:file|berkas|dokumen)\s*[`'\"]?([a-zA-Z0-9_\-\.\/]+?\.[a-zA-Z0-9]+)[`'\"]?",
]


def _collect_media_paths(user_text: str, output_text: str, workspace: str) -> List[str]:
    """
    Gathers files to deliver: MEDIA: directives from the model, files the user explicitly
    asked for, and an auto-exported Markdown document for long reports.
    Every path goes through the media guard for the turn's workspace.
    """
    media_paths = extract_media_paths(output_text, workspace_dir=workspace)

    # Smart user intent file auto-discovery ("kirim file X", "unduh berkas X")
    for f_pat in _FILE_INTENT_PATTERNS:
        for match in re.finditer(f_pat, user_text, re.IGNORECASE):
            req_filename = match.group(1).strip().strip("`'\"")
            for cand in ((Path(workspace) / req_filename).resolve(), (get_upload_dir() / req_filename).resolve()):
                if cand.is_file():
                    is_valid, _ = validate_media_delivery_path(str(cand), workspace)
                    if is_valid and str(cand) not in media_paths:
                        media_paths.append(str(cand))
                        break

    # Auto document export for long reports or explicit markdown/document requests
    user_wants_doc = bool(re.search(r"(?:kirim|buatkan|minta)\s+(?:file|berkas|dokumen|markdown|\.md|laporan)", user_text, re.IGNORECASE))
    if (len(output_text) > 4000 or user_wants_doc) and not any(p.endswith(".md") for p in media_paths):
        try:
            doc_slug = "laporan"
            if re.search(r"keamanan|security|audit|vulnerabilit", output_text[:400], re.IGNORECASE):
                doc_slug = "laporan_audit_keamanan"
            elif re.search(r"ringkasan|summary", output_text[:400], re.IGNORECASE):
                doc_slug = "ringkasan"

            doc_path = get_upload_dir() / f"{doc_slug}_{int(time.time())}.md"
            doc_path.write_text(output_text, encoding="utf-8")

            is_valid, _ = validate_media_delivery_path(str(doc_path), workspace)
            if is_valid:
                media_paths.append(str(doc_path))
        except Exception as e_doc:
            logger.warning(f"Failed to auto-export markdown document: {e_doc}")

    return media_paths


async def execute_agent_turn(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_text: str
):
    """
    Executes one turn against native agy CLI subprocess under user mutex lock.
    Incorporates Hermes reactions, quiet pinning, in-place status,
    and automatic topic renaming. Works for both messages and button callbacks.
    """
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    thread_id = get_effective_thread_id(update)

    raw_reply = getattr(update.message, "message_id", None) if update.message else None
    reply_id = raw_reply if isinstance(raw_reply, int) else None

    # 1. Hardline Security Blocklist
    if is_hardline_blocked(str(user_text)):
        logger.error(f"🚨 HARDLINE SECURITY BLOCKLIST TRIGGERED: {user_text}")
        await safe_send_message(
            bot=context.bot,
            chat_id=chat_id,
            text=(
                f"🚨 **HARDLINE SECURITY BLOCKLIST TRIGGERED!**\n\n"
                f"Instruksi berikut terdeteksi berisiko katastropik dan **DIBLOKIR TOTAL** demi integritas sistem:\n"
                f"```bash\n{user_text}\n```"
            ),
            reply_to_message_id=reply_id,
            message_thread_id=thread_id,
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # 2. Intent Guard (Interactive Approval)
    is_destruct, reason = is_destructive_prompt(str(user_text))
    if is_destruct:
        approved = await request_user_approval(
            bot=context.bot,
            chat_id=chat_id,
            user_id=user_id,
            prompt=str(user_text),
            reason=reason,
            message_thread_id=thread_id
        )
        if not approved:
            await safe_send_message(
                context.bot,
                chat_id,
                "❌ **Instruksi Ditolak.** Eksekusi tidak dijalankan.",
                reply_to_message_id=reply_id,
                message_thread_id=thread_id,
                parse_mode=ParseMode.MARKDOWN
            )
            return

    # 3. Lifecycle Start: Reaction 👀 & Quiet Pin
    await on_turn_start(context.bot, chat_id, reply_id)

    turn_start_time = time.time()
    status_msg = await safe_send_message(
        context.bot,
        chat_id,
        "⏳ *Antigravity sedang berpikir & memproses...*",
        parse_mode=ParseMode.MARKDOWN,
        disable_notification=True,
        reply_to_message_id=reply_id,
        message_thread_id=thread_id
    )

    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(
        send_typing_and_progress(context.bot, chat_id, status_msg, stop_typing, message_thread_id=thread_id)
    )

    db = get_db()
    topic_binding = None
    if thread_id is not None:
        # Topics keep isolated memory: never fall back to the root chat session
        active_conv, _ = get_conversation_for_message(chat_id, thread_id)
        topic_binding = db.get_topic_binding(chat_id, thread_id)
    else:
        active_conv = get_root_conversation(user_id)

    topic_workspace = topic_binding.get("workspace_path") if topic_binding else None
    current_workspace = topic_workspace or WORKSPACE_DIR

    topic_model = topic_binding.get("model_override") if topic_binding else None
    active_model = topic_model or db.get_user_model(user_id, chat_id, thread_id or "root") or DEFAULT_MODEL
    turn_success = False

    async def _stop_status_bubble():
        nonlocal status_msg
        stop_typing.set()
        typing_task.cancel()
        try:
            await asyncio.wait_for(typing_task, timeout=0.5)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass
        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass
            status_msg = None

    try:
        output_text, new_conv_id = await run_agy_cli(
            user_id=user_id,
            prompt=str(user_text),
            conv_id=active_conv,
            cwd=current_workspace,
            model=active_model
        )
        turn_success = True

        if new_conv_id:
            if thread_id is not None:
                bind_conversation_to_topic(chat_id, thread_id, new_conv_id)
                asyncio.create_task(auto_rename_forum_topic(context.bot, chat_id, thread_id, str(user_text), output_text))
            else:
                set_root_conversation(user_id, new_conv_id)

        # 4. Media Dispatch with strict Security Path Traversal Guard
        media_paths = _collect_media_paths(str(user_text), output_text, current_workspace)

        # 5. Format HTML with duration badge and split safely (<4000 char)
        formatted_html = append_duration_badge(
            markdown_to_telegram_html(output_text),
            int(time.time() - turn_start_time)
        )
        chunks = split_message(formatted_html, max_length=4000)

        # Stop background typing loop before deleting status bubble to prevent race condition
        await _stop_status_bubble()

        # Check if the output text has structured clarification / choice options
        clarify_kb = None
        clarify_detect = detect_clarify_options(output_text)
        if clarify_detect:
            _, options = clarify_detect
            clarify_id = str(uuid.uuid4())[:8]
            register_clarification(clarify_id, {
                "user_id": user_id,
                "chat_id": chat_id,
                "thread_id": thread_id,
                "choices": options
            })
            clarify_kb = build_clarify_keyboard(clarify_id, options)

        # Send formatted chunks
        for i, chunk in enumerate(chunks):
            is_last = (i == len(chunks) - 1)
            await safe_send_message(
                context.bot,
                chat_id,
                chunk,
                reply_markup=clarify_kb if is_last else None,
                parse_mode=ParseMode.HTML,
                reply_to_message_id=reply_id if i == 0 else None,
                message_thread_id=thread_id,
                disable_notification=False
            )

        # Dispatch outbound media files (voice, photo, video, doc)
        for fpath in media_paths:
            await send_outbound_media(
                bot=context.bot,
                chat_id=chat_id,
                file_path=fpath,
                reply_to_message_id=reply_id,
                message_thread_id=thread_id,
                workspace_dir=current_workspace
            )

    except asyncio.CancelledError:
        logger.info(f"Task user {user_id} dibatalkan.")
        await _stop_status_bubble()
        await safe_send_message(
            context.bot,
            chat_id,
            "🛑 *Tugas dibatalkan oleh pengguna via /cancel.*",
            parse_mode=ParseMode.MARKDOWN,
            reply_to_message_id=reply_id,
            message_thread_id=thread_id,
            disable_notification=False
        )
        await on_turn_complete(context.bot, chat_id, reply_id, success=False, cancelled=True)
        raise
    except Exception as e:
        logger.error(f"Error saat mengeksekusi agy: {e}", exc_info=True)
        await _stop_status_bubble()
        err_msg = (
            f"❌ <b>Terjadi kesalahan saat memproses permintaan:</b>\n"
            f"<pre><code>{html.escape(str(e))[:1000]}</code></pre>\n\n"
            f"💡 <i>Petunjuk:</i> Pastikan binary <code>agy</code> terpasang di path yang sesuai atau gunakan <code>/reset</code>."
        )
        await safe_send_message(
            context.bot,
            chat_id,
            err_msg,
            parse_mode=ParseMode.HTML,
            reply_to_message_id=reply_id,
            message_thread_id=thread_id,
            disable_notification=False
        )
        await on_turn_complete(context.bot, chat_id, reply_id, success=False)
    finally:
        stop_typing.set()
        typing_task.cancel()
        if turn_success:
            await on_turn_complete(context.bot, chat_id, reply_id, success=True)


def _turn_key(update: Update) -> Tuple[Optional[int], Optional[int]]:
    chat = update.effective_chat
    return (chat.id if chat else None, get_effective_thread_id(update))


def _start_turn_task(user_id: int, update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str) -> None:
    """Runs one agent turn in a background task under the user's mutex."""
    user_lock = get_user_lock(user_id)

    async def _locked_runner():
        async with user_lock:
            await execute_agent_turn(update, context, prompt)

    task = asyncio.create_task(_locked_runner())
    user_tasks[user_id] = task

    def _on_done(t: asyncio.Task) -> None:
        if user_tasks.get(user_id) is t:
            user_tasks.pop(user_id, None)
        if t.cancelled():
            # /cancel or /reset also discards follow-ups queued behind the cancelled turn
            user_pending_prompts.pop(user_id, None)
            return
        _drain_pending_prompts(user_id)

    task.add_done_callback(_on_done)


def _drain_pending_prompts(user_id: int) -> None:
    """Starts the next queued turn, merging consecutive prompts sent to the same chat/topic."""
    queue = user_pending_prompts.get(user_id)
    if not queue:
        user_pending_prompts.pop(user_id, None)
        return

    key = _turn_key(queue[0][0])
    batch = []
    while queue and _turn_key(queue[0][0]) == key:
        batch.append(queue.pop(0))
    if not queue:
        user_pending_prompts.pop(user_id, None)

    update, context, _ = batch[-1]
    combined = "\n\n".join(prompt for _, _, prompt in batch)
    _start_turn_task(user_id, update, context, combined)


async def _dispatch_agent_turn(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    prompt: str
):
    """Starts a turn, or queues the prompt when the user already has one running."""
    user_id = update.effective_user.id

    if is_user_busy(user_id):
        queue = user_pending_prompts.setdefault(user_id, [])
        if len(queue) >= MAX_QUEUED_PROMPTS:
            text = (
                f"⚠️ *Antrean penuh ({MAX_QUEUED_PROMPTS} pesan).* Tunggu tugas saat ini selesai "
                "atau kirim /cancel untuk menghentikannya."
            )
        else:
            queue.append((update, context, prompt))
            text = (
                f"📥 *Pesan diantrikan (#{len(queue)}).* Akan diproses otomatis setelah tugas saat ini selesai.\n"
                "Kirim /cancel untuk membatalkan tugas beserta antreannya."
            )
        await safe_send_message(
            context.bot,
            update.effective_chat.id,
            text,
            parse_mode=ParseMode.MARKDOWN,
            disable_notification=True,
            message_thread_id=get_effective_thread_id(update)
        )
        return

    _start_turn_task(user_id, update, context, prompt)


# ==============================================================================
# INBOUND MEDIA & MESSAGE HANDLERS
# ==============================================================================
def _passes_chat_gating(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> Tuple[bool, str]:
    """Group chat mention & reply gating (private chats always pass)."""
    raw_username = getattr(context.bot, "username", "")
    return should_process_chat_message(
        update,
        bot_id=getattr(context.bot, "id", None),
        bot_username=raw_username if isinstance(raw_username, str) else "",
        require_mention=TELEGRAM_REQUIRE_MENTION_IN_GROUPS,
        text=text
    )


async def _reply_error(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    if update.message:
        try:
            await update.message.reply_text(text)
            return
        except Exception:
            pass
    await safe_send_message(context.bot, update.effective_chat.id, text, parse_mode=None)


async def _download_inbound_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    source: Any,
    filename: str,
    label: str
) -> Optional[Path]:
    """Downloads a Telegram file into the upload directory; replies with an error on failure."""
    try:
        file_obj = await source.get_file()
        dest_path = (get_upload_dir() / filename).resolve()
        await file_obj.download_to_drive(custom_path=dest_path)
        logger.info(f"Berkas {label} berhasil diunduh ke: {dest_path}")
        return dest_path
    except Exception as e:
        logger.error(f"Gagal mengunduh {label} untuk user {update.effective_user.id}: {e}", exc_info=True)
        await _reply_error(update, context, f"❌ Gagal mengunduh {label}: {e}")
        return None


def _with_reply_context(update: Update, text: str) -> str:
    if update.message and getattr(update.message, "reply_to_message", None):
        return format_reply_context(update.message, text)
    return text


async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    if not update.message or not update.message.photo:
        return

    caption = (update.message.caption or "").strip()
    should_proc, _ = _passes_chat_gating(update, context, caption)
    if not should_proc:
        return

    # Route multi-photo albums to MediaGroupCollector
    media_group_id = getattr(update.message, "media_group_id", None)
    if isinstance(media_group_id, str) and media_group_id.strip():
        enqueued = await media_group_collector.enqueue(
            update, context,
            chat_id=update.effective_chat.id,
            user_id=update.effective_user.id,
            media_group_id=media_group_id
        )
        if enqueued:
            return

    filename = f"photo_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg"
    dest_path = await _download_inbound_file(update, context, update.message.photo[-1], filename, "foto")
    if dest_path is None:
        return

    caption_prompt = _with_reply_context(update, caption or "Tolong periksa dan analisis gambar terlampir ini.")
    prompt_text = (
        f"[PENGGUNA MENGIRIMKAN GAMBAR / SCREENSHOT]\n"
        f"Berkas gambar telah disimpan di: {dest_path}\n\n"
        f"[INSTRUKSI / CAPTION PENGGUNA]:\n"
        f"{caption_prompt}"
    )
    await _dispatch_agent_turn(update, context, prompt_text)


async def handle_document_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    if not update.message or not update.message.document:
        return

    caption = (update.message.caption or "").strip()
    should_proc, _ = _passes_chat_gating(update, context, caption)
    if not should_proc:
        return

    doc = update.message.document
    orig_name = doc.file_name or f"doc_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    safe_name = re.sub(r'[\/:*?"<>|\x00-\x1f]', '_', Path(orig_name).name).strip()
    if not safe_name.strip("._"):
        safe_name = f"doc_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    filename = f"{int(time.time())}_{uuid.uuid4().hex[:4]}_{safe_name}"

    dest_path = await _download_inbound_file(update, context, doc, filename, "dokumen")
    if dest_path is None:
        return

    caption_prompt = _with_reply_context(update, caption or "Tolong periksa, baca, dan analisis dokumen terlampir ini.")
    prompt_text = (
        f"[PENGGUNA MENGIRIMKAN DOKUMEN / BERKAS]\n"
        f"Nama berkas asli: {orig_name}\n"
        f"Berkas telah disimpan di: {dest_path}\n\n"
        f"[INSTRUKSI / CAPTION PENGGUNA]:\n"
        f"{caption_prompt}"
    )
    await _dispatch_agent_turn(update, context, prompt_text)


async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inbound Voice Note handler (Opus/OGG voice bubble) with Speech-to-Text (STT)."""
    if not is_authorized(update):
        return
    msg = update.message
    if not msg or not msg.voice:
        return

    caption = (msg.caption or "").strip()
    should_proc, _ = _passes_chat_gating(update, context, caption)
    if not should_proc:
        return

    filename = f"voice_{int(time.time())}_{uuid.uuid4().hex[:6]}.ogg"
    dest_path = await _download_inbound_file(update, context, msg.voice, filename, "pesan suara")
    if dest_path is None:
        return

    # Speech-to-Text (STT) transcription if enabled
    transcript = None
    if TELEGRAM_STT_ENABLED:
        try:
            transcript = await transcribe_audio_file(dest_path, model_name=TELEGRAM_WHISPER_MODEL)
        except Exception as e:
            logger.warning(f"Gagal mentranskripsi pesan suara: {e}")

    if transcript:
        caption_line = f"\n[CATATAN PENGGUNA]: {caption}" if caption else ""
        prompt_text = (
            f"[PENGGUNA MENGIRIMKAN REKAMAN SUARA / VOICE NOTE (TRANSKRIPSI OTOMATIS)]:\n"
            f"\"{transcript}\"{caption_line}\n\n"
            f"(Berkas audio asli tersimpan di: {dest_path})"
        )
    else:
        prompt_text = (
            f"[PENGGUNA MENGIRIMKAN REKAMAN SUARA / VOICE NOTE]\n"
            f"Berkas audio tersimpan di: {dest_path}\n\n"
            f"[CATATAN / CAPTION]:\n{caption if caption else 'Mohon dengarkan dan tindak lanjuti pesan suara ini.'}"
        )

    await _dispatch_agent_turn(update, context, prompt_text)


async def handle_audio_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inbound Audio / Music track handler (distinct from voice notes, no STT)."""
    if not is_authorized(update):
        return
    msg = update.message
    if not msg or not msg.audio:
        return

    caption = (msg.caption or "").strip()
    should_proc, _ = _passes_chat_gating(update, context, caption)
    if not should_proc:
        return

    audio = msg.audio
    orig_name = audio.file_name or f"audio_{int(time.time())}.mp3"
    ext = Path(orig_name).suffix or ".mp3"
    filename = f"audio_{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"
    dest_path = await _download_inbound_file(update, context, audio, filename, "berkas audio")
    if dest_path is None:
        return

    title_info = f"Judul: {audio.title}" if audio.title else ""
    performer_info = f"Artis: {audio.performer}" if audio.performer else ""
    meta_info = " | ".join(filter(None, [title_info, performer_info]))
    meta_line = f"Metadata: {meta_info}\n" if meta_info else ""

    prompt_text = (
        f"[PENGGUNA MENGIRIMKAN BERKAS AUDIO / MUSIK]\n"
        f"Nama berkas asli: {orig_name}\n"
        f"{meta_line}"
        f"Berkas tersimpan di: {dest_path}\n\n"
        f"[CATATAN / CAPTION]:\n{caption if caption else 'Tolong periksa berkas audio ini.'}"
    )
    await _dispatch_agent_turn(update, context, prompt_text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main router for incoming text messages."""
    if not is_authorized(update):
        user_id = update.effective_user.id if update.effective_user else "Unknown"
        logger.warning(f"Unauthorized text message attempt from User ID: {user_id}")
        return

    thread_id = get_effective_thread_id(update)

    # Expand any hidden text_link URLs
    user_text = ""
    if update.message and (
        isinstance(update.message.text, str) or isinstance(getattr(update.message, "caption", None), str)
    ):
        user_text = expand_link_entities(update.message)

    should_proc, user_text = _passes_chat_gating(update, context, user_text)
    if not should_proc:
        return

    raw_username = getattr(context.bot, "username", "")
    user_text = clean_bot_mentions(user_text, raw_username if isinstance(raw_username, str) else "")
    if not user_text.strip():
        return

    # Intent Interceptor: Quota / Usage Inquiry
    if is_quota_inquiry(user_text):
        status_msg = await safe_send_message(
            context.bot,
            update.effective_chat.id,
            "⏳ *Mengambil data kuota model dari Antigravity CLI...*",
            parse_mode=ParseMode.MARKDOWN,
            message_thread_id=thread_id
        )
        try:
            report_html = await fetch_agy_usage_report()
            if status_msg:
                await safe_edit_message(status_msg, report_html, parse_mode=ParseMode.HTML)
            else:
                await safe_send_message(context.bot, update.effective_chat.id, report_html, parse_mode=ParseMode.HTML, message_thread_id=thread_id)
        except Exception as e:
            logger.error(f"Error handling quota inquiry: {e}")
        return

    # Check and prepend reply context if user is replying to a message
    user_text = _with_reply_context(update, user_text)

    # Route through TextDebouncer if debounce window is active (>0)
    if TELEGRAM_DEBOUNCE_SECONDS > 0:
        enqueued = await text_debouncer.enqueue(
            update, context, user_text,
            chat_id=update.effective_chat.id,
            user_id=update.effective_user.id,
            thread_id=thread_id
        )
        if enqueued:
            return
    await _dispatch_agent_turn(update, context, user_text)


# ==============================================================================
# LIFECYCLE HOOKS & APPLICATION BUILDER
# ==============================================================================
async def _restart_polling(application) -> None:
    """Soft recovery for a stalled long-poll; escalates to a process restart on failure."""
    updater = getattr(application, "updater", None)
    if updater is None:
        return
    logger.warning("♻️ Memulai ulang long polling Telegram setelah stall terdeteksi...")
    try:
        if updater.running:
            await updater.stop()
        await updater.start_polling()
        stall_watchdog.record_progress()
        logger.info("✓ Long polling berhasil dimulai ulang.")
    except Exception as e:
        logger.critical(
            f"Gagal memulai ulang polling ({redact_telegram_error_text(e)}). "
            f"Menghentikan aplikasi agar supervisor (systemd/docker) melakukan restart."
        )
        application.stop_running()


async def post_init(application):
    """Registers bot commands with Telegram API on startup and starts watchdog."""
    commands = [
        BotCommand("help", "📖 Buka Help Center interaktif"),
        BotCommand("model", "🤖 Pilih model AI aktif"),
        BotCommand("topic", "💬 Buat topik baru di private DM"),
        BotCommand("topics", "📋 Lihat daftar semua topik aktif"),
        BotCommand("title", "🏷️ Ganti nama topik saat ini"),
        BotCommand("deletetopic", "🗑️ Hapus topik obrolan saat ini"),
        BotCommand("sessions", "🗂️ Riwayat sesi percakapan AGY"),
        BotCommand("resume", "🔄 Lanjutkan sesi percakapan lama"),
        BotCommand("reset", "✨ Mulai sesi obrolan baru"),
        BotCommand("usage", "📊 Cek kuota model & sisa limit"),
        BotCommand("status", "ℹ️ Status engine, PID, & memori"),
        BotCommand("cancel", "🛑 Hentikan tugas aktif seketika"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("✓ BotCommand menu resmi berhasil didaftarkan ke Telegram API.")
    except Exception as e:
        logger.warning(f"Gagal mendaftarkan bot commands: {e}")

    try:
        pruned = get_db().prune_expired_receipts()
        if pruned:
            logger.info(f"✓ {pruned} receipt anti-replay kedaluwarsa dibersihkan.")
    except Exception as e:
        logger.warning(f"Gagal membersihkan receipt anti-replay: {e}")

    # Start PollingStallWatchdog if running in polling mode
    if not TELEGRAM_WEBHOOK_URL:
        try:
            stall_watchdog.on_stall_callback = lambda: _restart_polling(application)
            stall_watchdog.start(application)
            logger.info("✓ Polling stall watchdog aktif.")
        except Exception as e:
            logger.warning(f"Gagal memulai stall watchdog: {e}")


async def post_shutdown(application):
    """Cleans up running subprocesses, watchdog, and instance lock on shutdown."""
    logger.info("Menutup seluruh subprocess Antigravity...")
    try:
        await stall_watchdog.stop()
    except Exception:
        pass

    try:
        await text_debouncer.cancel_all()
        await media_group_collector.cancel_all()
    except Exception:
        pass

    user_pending_prompts.clear()
    for proc in list(user_processes.values()):
        try:
            if proc.returncode is None:
                terminate_process_tree(proc)
        except Exception:
            pass
    user_processes.clear()
    user_conversations.clear()

    try:
        release_instance_lock()
    except Exception:
        pass


def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN belum diatur di file .env! Bot tidak dapat berjalan.")
        sys.exit(1)

    if not ALLOWED_USER_IDS:
        logger.warning("⚠️ ALLOWED_USER_ID belum diatur. Tidak ada pengguna yang dapat mengakses bot!")

    # Instance lock guard (Anti-409 Conflict)
    if not acquire_instance_lock():
        logger.critical("❌ Proses bot lain sedang berjalan dengan instance lock aktif! Menghentikan proses ini untuk mencegah 409 Conflict.")
        sys.exit(1)

    logger.info(f"🤖 Antigravity Engine : Subprocess agy CLI (Hermes-Parity)")
    logger.info(f"📂 Binary Path        : {AGY_BIN_PATH}")
    logger.info(f"🛡️ Whitelist User IDs : {ALLOWED_USER_IDS}")
    logger.info(f"⚙️ Approval Mode      : {APPROVAL_MODE} (Timeout: {APPROVAL_TIMEOUT_SECONDS}s)")
    logger.info(f"📁 Workspace Path     : {WORKSPACE_DIR}")
    if AGY_SKIP_PERMISSIONS:
        logger.warning(
            "⚠️ AGY_SKIP_PERMISSIONS=true: agy menjalankan tool tanpa konfirmasi. "
            "Hermes Guard hanya memeriksa teks instruksi, bukan aksi agent."
        )

    try:
        request_client = build_resilient_request(
            proxy_url=TELEGRAM_PROXY if TELEGRAM_PROXY else None,
            enable_fallback=TELEGRAM_FALLBACK_TRANSPORT
        )

        app = (
            ApplicationBuilder()
            .token(TELEGRAM_BOT_TOKEN)
            .request(request_client)
            .post_init(post_init)
            .post_shutdown(post_shutdown)
            .build()
        )

        # Ingress gate: whitelist + anti-replay for every update, before any other handler
        app.add_handler(TypeHandler(Update, ingress_gate), group=-1)

        # Command Handlers
        app.add_handler(CommandHandler("start", start_command))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("model", handle_model_command))
        app.add_handler(CommandHandler("topic", handle_topic_command))
        app.add_handler(CommandHandler("topics", handle_topics_command))
        app.add_handler(CommandHandler("title", handle_title_command))
        app.add_handler(CommandHandler(["deletetopic", "rmtopic"], handle_delete_topic_command))
        app.add_handler(CommandHandler("sessions", sessions_command))
        app.add_handler(CommandHandler("resume", resume_command))
        app.add_handler(CommandHandler(["usage", "limit"], usage_command))
        app.add_handler(CommandHandler("status", status_command))
        app.add_handler(CommandHandler(["reset", "new", "clear"], reset_command))
        app.add_handler(CommandHandler("cancel", cancel_command))

        # Callback Query Handlers (Approval, Model selection, Clarify, Help)
        app.add_handler(CallbackQueryHandler(handle_callback_query))

        # Media Handlers
        app.add_handler(MessageHandler(filters.PHOTO, handle_photo_message))
        app.add_handler(MessageHandler(filters.VOICE, handle_voice_message))
        app.add_handler(MessageHandler(filters.AUDIO, handle_audio_message))
        app.add_handler(MessageHandler(filters.Document.ALL, handle_document_message))

        # Text Messages
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        if TELEGRAM_WEBHOOK_URL:
            if not TELEGRAM_WEBHOOK_SECRET:
                logger.critical("❌ TELEGRAM_WEBHOOK_URL diatur tetapi TELEGRAM_WEBHOOK_SECRET kosong! (GHSA-3vpc-7q5r-276h security policy). Startup dibatalkan.")
                release_instance_lock()
                sys.exit(1)
            logger.info(f"🌐 Menjalankan mode Webhook di {TELEGRAM_WEBHOOK_LISTEN}:{TELEGRAM_WEBHOOK_PORT} -> {TELEGRAM_WEBHOOK_URL}")
            app.run_webhook(
                listen=TELEGRAM_WEBHOOK_LISTEN,
                port=TELEGRAM_WEBHOOK_PORT,
                webhook_url=TELEGRAM_WEBHOOK_URL,
                secret_token=TELEGRAM_WEBHOOK_SECRET,
            )
        else:
            logger.info("🚀 Antigravity CLI Telegram Bot siap berjalan dalam mode Long Polling...")
            app.run_polling()
    finally:
        release_instance_lock()


if __name__ == "__main__":
    main()
