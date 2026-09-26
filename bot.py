#!/usr/bin/env python3
"""
Antigravity Telegram Bot (Native agy CLI Subprocess Engine).
Features full parity with Hermes Agent in Telegram:
1. Bot API 9.5 Draft Streaming (sendMessageDraft) & In-place status updates.
2. Bot API 9.4 Private Chat Topics (/topic) for multi-session parallel DMs.
3. Silent Turn Pinned Indicator & Real Reactions (👀 -> 👍/👎).
4. Interactive Model Picker (/model) using official agy models.
5. Multi-Media Pipeline with Voice Bubble audio & strict Media Path Traversal Guard.
6. DNS-over-HTTPS (DoH) & Host/SNI preserving fallback transport.
7. Anti-replay update admission control.
8. Interactive Inline Approval (Hermes Guard) & /cancel PID killing.
"""

from __future__ import annotations

import os
import sys
import re
import json
import time
import html
import uuid
import shutil
import asyncio
import logging
from pathlib import Path
from typing import Dict, Set, Optional, Tuple, List, Any

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
    ContextTypes,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
)

# Central Configuration
import config
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
    resolve_workspace_dir,
    get_data_dir,
    build_cli_prompt,
)

def get_upload_dir() -> Path:
    """Memastikan dan mengembalikan direktori penyimpanan berkas unggahan Telegram (.telegram_uploads)."""
    current_ws = globals().get("WORKSPACE_DIR", WORKSPACE_DIR)
    upload_dir = Path(current_ws) / ".telegram_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


# Database State
from database.state import get_db, StateDatabase

# Telegram Platform Components
from tele.network import build_resilient_request, TelegramFallbackTransport
from tele.admission import check_update_admission, is_update_admitted
from tele.entities import expand_link_entities, clean_bot_mentions
from tele.media import (
    validate_media_delivery_path,
    extract_media_paths,
    send_outbound_media,
    classify_media_type,
)
from tele.streaming import (
    supports_draft_streaming,
    send_draft_stream,
    send_or_update_status,
    clear_status_bubble,
    split_message,
)
from tele.formatters import markdown_to_telegram_html, append_duration_badge
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

# Core Engine & Approval
from core.approval import (
    HARDLINE_BLOCKLIST,
    DESTRUCTIVE_PATTERNS,
    is_hardline_blocked,
    is_destructive_prompt,
    pending_approvals,
    request_user_approval,
    handle_approval_callback,
)
from core.usage import (
    format_progress_bar,
    format_relative_time,
    format_usage_data,
    fetch_agy_usage_report,
    is_quota_inquiry,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("antigravity-tele-bot")

# Global registries for state and cancellation
user_conversations: Dict[int, str] = {}
user_processes: Dict[int, asyncio.subprocess.Process] = {}
user_locks: Dict[int, asyncio.Lock] = {}
user_tasks: Dict[int, asyncio.Task] = {}


def get_user_lock(user_id: int) -> asyncio.Lock:
    """Returns mutex lock per user to prevent concurrent race conditions."""
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]


# ==============================================================================
# AUTHORIZATION & UTILITIES
# ==============================================================================
def is_authorized(update: Update) -> bool:
    """Verifies whether the sender is in ALLOWED_USER_IDS."""
    user = update.effective_user
    if user is None:
        return False
    return user.id in ALLOWED_USER_IDS


def get_effective_thread_id(update: Update) -> Optional[int]:
    """Extracts message_thread_id reliably whether update is Message or CallbackQuery."""
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if msg:
        tid = getattr(msg, "message_thread_id", None)
        if isinstance(tid, int):
            return tid
    return None


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

        # 2. Fallback to plain text if formatting error occurred
        logger.warning(f"Error saat send_message ({e}). Mengirim ulang sebagai plain text...")
        target_reply = effective_reply if "repl" not in err_str else None
        plain_kwargs = dict(send_kwargs)
        plain_kwargs["parse_mode"] = None
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
                text=text,
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
# SUBPROCESS ENGINE & TRANSCRIPT RECOVERY
# ==============================================================================
def get_transcript_path(conv_id: Optional[str]) -> Tuple[Optional[Path], Optional[str]]:
    """
    Searches for transcript.jsonl for a given conversation ID or the newest session.
    Reads WORKSPACE_DIR dynamically to respect runtime test patching.
    """
    current_workspace = globals().get("WORKSPACE_DIR", WORKSPACE_DIR)
    candidate_bases: List[Path] = []
    home = Path.home()
    candidate_bases.extend([
        home / ".gemini" / "antigravity-cli" / "brain",
        home / ".gemini" / "antigravity" / "brain",
        Path("/home/ubuntu/.gemini/antigravity-cli/brain"),
        Path("/home/ubuntu/.gemini/antigravity/brain"),
        Path(current_workspace) / ".gemini" / "brain",
    ])

    valid_bases = [b for b in candidate_bases if b.is_dir()]

    if conv_id:
        for base in valid_bases:
            cand = base / conv_id / ".system_generated" / "logs" / "transcript.jsonl"
            if cand.is_file():
                return cand, conv_id
        return None, conv_id

    newest_file: Optional[Path] = None
    newest_mtime = -1.0
    found_conv_id: Optional[str] = None

    for base in valid_bases:
        try:
            for item in base.iterdir():
                if item.is_dir():
                    cand = item / ".system_generated" / "logs" / "transcript.jsonl"
                    if cand.is_file():
                        mtime = cand.stat().st_mtime
                        if mtime > newest_mtime:
                            newest_mtime = mtime
                            newest_file = cand
                            found_conv_id = item.name
        except Exception as e:
            logger.debug(f"Error checking brain dir {base}: {e}")

    if newest_file and found_conv_id:
        return newest_file, found_conv_id

    return None, None


def recover_last_response_from_transcript(conv_id: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Recovers the model's last response from transcript.jsonl if subprocess timed out.
    Enforces anti-stale turn protection (checks USER_INPUT before PLANNER_RESPONSE).
    """
    transcript_path, resolved_conv_id = get_transcript_path(conv_id)
    if not transcript_path or not transcript_path.is_file():
        return None, resolved_conv_id

    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            lines = [line.strip() for line in f if line.strip()]

        last_planner_content = None
        user_input_seen_after_planner = False

        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except Exception:
                continue

            entry_type = entry.get("type", "")
            if entry_type == "USER_INPUT":
                if last_planner_content is None:
                    user_input_seen_after_planner = True
                    break

            if entry_type == "PLANNER_RESPONSE" and last_planner_content is None:
                content = entry.get("content", "").strip()
                if content:
                    last_planner_content = content

        if user_input_seen_after_planner:
            logger.info(
                f"Menemukan USER_INPUT sebelum PLANNER_RESPONSE di transkrip ({transcript_path}). "
                "Tidak ada balasan baru untuk giliran ini."
            )
            return None, resolved_conv_id

        if last_planner_content:
            logger.info(
                f"Berhasil me-recover balasan model ({len(last_planner_content)} karakter) dari "
                f"{transcript_path} (Conv: {resolved_conv_id})"
            )
            return last_planner_content, resolved_conv_id

        return None, resolved_conv_id
    except Exception as e:
        logger.warning(f"Error reading transcript {transcript_path}: {e}")
        return None, resolved_conv_id


async def run_agy_cli(
    user_id: int,
    prompt: str,
    conv_id: Optional[str] = None,
    cwd: Optional[str] = None,
    model: Optional[str] = None
) -> Tuple[str, Optional[str]]:
    """
    Executes agy CLI as an asynchronous subprocess.
    Extracts conversation_id and response body with timeout & transcript recovery.
    Returns (response_text, new_or_existing_conv_id).
    """
    effective_cwd = cwd or globals().get("WORKSPACE_DIR", WORKSPACE_DIR)

    if not os.path.exists(AGY_BIN_PATH) and not shutil.which(AGY_BIN_PATH):
        raise FileNotFoundError(
            f"Binary agy tidak ditemukan di: '{AGY_BIN_PATH}'. "
            f"Periksa variabel AGY_BIN_PATH di file .env."
        )

    cmd = [AGY_BIN_PATH]
    if conv_id:
        cmd.extend(["--conversation", conv_id])

    active_model = model or DEFAULT_MODEL
    if active_model:
        cmd.extend(["--model", active_model])

    full_prompt = build_cli_prompt(prompt)
    cmd.extend([
        "-p", full_prompt,
        "--print-timeout", f"{AGY_TIMEOUT_SECONDS}s",
    ])
    if AGY_SKIP_PERMISSIONS:
        cmd.append("--dangerously-skip-permissions")
    cmd.extend(["--output-format", "json"])

    env = os.environ.copy()
    extra_paths = [
        "/home/ubuntu/.gemini/antigravity-cli/bin",
        "/home/ubuntu/.local/bin",
        "/usr/local/bin"
    ]
    env["PATH"] = os.pathsep.join(extra_paths + [env.get("PATH", "")])

    logger.info(
        f"Menjalankan subprocess agy untuk user {user_id} "
        f"(Conv: {conv_id or 'Baru'}, Timeout: {AGY_TIMEOUT_SECONDS}s)..."
    )

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=effective_cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    user_processes[user_id] = proc
    stdout_bytes = b""
    stderr_bytes = b""
    timed_out = False

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=float(AGY_TIMEOUT_SECONDS + 10)
        )
    except asyncio.TimeoutError:
        timed_out = True
        logger.warning(
            f"Proses agy untuk user {user_id} (PID: {proc.pid if hasattr(proc, 'pid') else 'unknown'}) "
            f"melebihi batas waktu ({AGY_TIMEOUT_SECONDS}s). Menghentikan subprocess..."
        )
        try:
            res = proc.terminate()
            if asyncio.iscoroutine(res):
                await res
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except Exception:
            try:
                k_res = proc.kill()
                if asyncio.iscoroutine(k_res):
                    await k_res
            except Exception:
                pass
    finally:
        user_processes.pop(user_id, None)

    if timed_out:
        # Check current module for mocked recover_last_response_from_transcript
        recover_fn = getattr(sys.modules[__name__], "recover_last_response_from_transcript", recover_last_response_from_transcript)
        recovered, found_id = recover_fn(conv_id)
        eff_id = found_id or conv_id
        if recovered:
            return (
                f"{recovered}\n\n"
                f"⏱️ <i>(Catatan: Subprocess agy melebihi batas waktu {AGY_TIMEOUT_SECONDS} detik dan dihentikan, "
                f"namun jawaban berhasil dipulihkan dari log transkrip sistem.)</i>",
                eff_id
            )
        return (
            f"⏱️ **Waktu eksekusi habis (Timeout {AGY_TIMEOUT_SECONDS} detik).**\n"
            f"Subprocess Antigravity telah dihentikan secara aman demi kestabilan sistem.\n\n"
            f"💡 *Jika tugas memerlukan waktu lebih lama, Anda dapat memperbesar nilai `AGY_TIMEOUT_SECONDS` di file `.env`.*",
            eff_id
        )

    stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
    stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()

    parsed_json = None
    if stdout_text:
        for line in stdout_text.splitlines():
            line_str = line.strip()
            if line_str.startswith("{") and line_str.endswith("}"):
                try:
                    parsed_json = json.loads(line_str)
                    break
                except Exception:
                    continue
        if not parsed_json:
            try:
                parsed_json = json.loads(stdout_text)
            except Exception:
                pass

    if parsed_json and isinstance(parsed_json, dict):
        ret_conv = parsed_json.get("conversation_id") or conv_id
        resp = parsed_json.get("response", "").strip()
        status = parsed_json.get("status", "")
        err = parsed_json.get("error", "").strip()

        if status == "ERROR" and err:
            return f"❌ **Error dari agy:**\n```text\n{err}\n```", ret_conv
        if resp:
            return resp, ret_conv
        elif err:
            return f"⚠️ **Output agy:**\n```text\n{err}\n```", ret_conv

    if stdout_text:
        return stdout_text, conv_id
    elif stderr_text:
        return f"⚠️ Output (stderr):\n```text\n{stderr_text}\n```", conv_id

    return "(agy menyelesaikan tugas tanpa balasan output teks)", conv_id


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
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("« Kembali ke Menu Bantuan", callback_data="help:main"),
                InlineKeyboardButton("✖️ Tutup", callback_data="help:close")
            ]
        ])
        return text, keyboard

    if category == "sessions":
        text = (
            "🗂️ <b>Panduan Sesi & Memori Percakapan</b>\n\n"
            "Antigravity menyimpan riwayat percakapan di sistem dalam format multi-turn:\n\n"
            "• <code>/sessions</code> — Tampilkan riwayat ID sesi percakapan terbaru.\n"
            "• <code>/resume &lt;id_sesi&gt;</code> — Lanjutkan kembali konteks percakapan lama.\n"
            "• <code>/reset</code> (alias: <code>/new</code>, <code>/clear</code>) — Hapus memori aktif & mulai sesi fresh.\n"
            "• <code>/cancel</code> — Hentikan paksa proses CLI yang sedang berjalan (<code>SIGTERM</code>/<code>SIGKILL</code>).\n\n"
            "💡 <i>Gunakan /reset bila model mulai keluar konteks atau ingin memulai tugas baru.</i>"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("« Kembali ke Menu Bantuan", callback_data="help:main"),
                InlineKeyboardButton("✖️ Tutup", callback_data="help:close")
            ]
        ])
        return text, keyboard

    if category == "security":
        text = (
            "🛡️ <b>Panduan Keamanan & Sandbox (Hermes Guard)</b>\n\n"
            "Bot dilengkapi pengaman berlapis untuk melindungi VPS & data Akang:\n\n"
            "• <b>Interactive Approval</b>: Instruksi berisiko (drop table, rm -rf, git force) wajib disetujui manual via tombol Approve / Deny (timeout 120 detik, fail-closed).\n"
            "• <b>Hardline Blocklist</b>: Perintah katastropik sistem (<code>rm -rf /</code>, <code>mkfs</code>, <code>dd</code>, <code>shutdown</code>) <b>DIBLOKIR TOTAL</b>.\n"
            "• <b>Media Guard</b>: Pengiriman file sensitif (<code>.env</code>, <code>state.db</code>, <code>auth.json</code>, SSH keys) dilarang secara ketat."
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("« Kembali ke Menu Bantuan", callback_data="help:main"),
                InlineKeyboardButton("✖️ Tutup", callback_data="help:close")
            ]
        ])
        return text, keyboard

    if category == "system":
        text = (
            "⚙️ <b>Perintah Sistem & Utilitas</b>\n\n"
            "• <code>/status</code> — Informasi engine, PID proses aktif, path workspace, & status memory.\n"
            "• <code>/usage</code> (alias: <code>/limit</code>) — Tampilkan sisa kuota model & waktu refresh.\n"
            "• <code>/model</code> — Buka pemilih model interaktif (Gemini 3.8, Claude, dll).\n"
            "• <code>/cancel</code> — Hentikan eksekusi perintah yang sedang berjalan."
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("« Kembali ke Menu Bantuan", callback_data="help:main"),
                InlineKeyboardButton("✖️ Tutup", callback_data="help:close")
            ]
        ])
        return text, keyboard

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

    chat_id = update.effective_chat.id
    thread_id = get_effective_thread_id(update)

    text, reply_markup = get_help_menu_content("main")
    await safe_send_message(
        context.bot,
        chat_id,
        text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML,
        message_thread_id=thread_id
    )


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

    fetch_fn = getattr(sys.modules[__name__], "fetch_agy_usage_report", fetch_agy_usage_report)
    usage_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="usage_refresh"),
            InlineKeyboardButton("✖️ Tutup", callback_data="msg_close")
        ]
    ])
    try:
        report_html = await fetch_fn()
        if status_msg:
            await safe_edit_message(status_msg, report_html, reply_markup=usage_kb, parse_mode=ParseMode.HTML)
        else:
            await safe_send_message(context.bot, chat_id, report_html, reply_markup=usage_kb, parse_mode=ParseMode.HTML, message_thread_id=thread_id)
    except Exception as e:
        logger.error(f"Error handling /usage: {e}", exc_info=True)
        err_msg = f"❌ Gagal mengambil data kuota:\n<code>{html.escape(str(e))}</code>"
        if status_msg:
            await safe_edit_message(status_msg, err_msg, reply_markup=usage_kb, parse_mode=ParseMode.HTML)
        else:
            await safe_send_message(context.bot, chat_id, err_msg, reply_markup=usage_kb, parse_mode=ParseMode.HTML, message_thread_id=thread_id)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    thread_id = get_effective_thread_id(update)

    db = get_db()
    current_conv = None
    if thread_id is not None:
        topic_binding = db.get_topic_binding(chat_id, thread_id)
        if topic_binding:
            current_conv = topic_binding.get("conv_id")
    if not current_conv:
        current_conv = user_conversations.get(user_id)

    selected_model = db.get_user_model(user_id, chat_id, thread_id or "root") or DEFAULT_MODEL
    running_proc = user_processes.get(user_id)
    is_proc_running = running_proc is not None and running_proc.returncode is None
    bin_exists = os.path.exists(AGY_BIN_PATH) or shutil.which(AGY_BIN_PATH) is not None

    status_text = (
        "📊 **Status Sistem Antigravity Bot (CLI Engine)**\n\n"
        f"• **Engine**: `Native agy CLI Subprocess`\n"
        f"• **Model Aktif**: `{selected_model}`\n"
        f"• **Binary Path**: `{AGY_BIN_PATH}` ({'✅ Ditemukan' if bin_exists else '❌ Tidak Ditemukan!'})\n"
        f"• **Workspace Path**: `{globals().get('WORKSPACE_DIR', WORKSPACE_DIR)}`\n"
        f"• **Approval Mode**: `{APPROVAL_MODE}`\n"
        f"• **Approval Timeout**: `{APPROVAL_TIMEOUT_SECONDS} detik`\n"
        f"• **Execution Timeout**: `{AGY_TIMEOUT_SECONDS} detik`\n"
        f"• **Sesi Percakapan**: `{current_conv if current_conv else 'Belum ada (Fresh)'}`\n"
        f"• **Topik Thread**: `{'Thread ' + str(thread_id) if thread_id is not None else 'Root Chat'}`\n"
        f"• **Status Tugas Saat Ini**: `{'⏳ Sedang Berjalan (PID: ' + str(running_proc.pid) + ')' if is_proc_running else '💤 Idle'}`\n"
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


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    thread_id = get_effective_thread_id(update)

    # 1. Hentikan proses yang sedang berjalan
    proc = user_processes.get(user_id)
    if proc and proc.returncode is None:
        try:
            proc.terminate()
        except Exception:
            pass

    task = user_tasks.get(user_id)
    if task and not task.done():
        task.cancel()

    # 2. Reset memori
    old_conv = user_conversations.pop(user_id, None)
    if thread_id is not None:
        db = get_db()
        binding = db.get_topic_binding(chat_id, thread_id)
        if binding and binding.get("conv_id"):
            old_conv = binding.get("conv_id")
        db.set_topic_binding(chat_id, thread_id, conv_id="", topic_name="Umum / General")

    # 3. Batalkan approval tertunda
    for req_id, info in list(pending_approvals.items()):
        if info.get("user_id") == user_id:
            fut = info.get("future")
            if fut and not fut.done():
                fut.cancel()

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


async def sessions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists recent conversation sessions on host and current active binding."""
    if not is_authorized(update):
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    thread_id = get_effective_thread_id(update)

    db = get_db()
    current_conv = None
    if thread_id is not None:
        binding = db.get_topic_binding(chat_id, thread_id)
        if binding:
            current_conv = binding.get("conv_id")
    if not current_conv:
        current_conv = user_conversations.get(user_id)

    current_workspace = globals().get("WORKSPACE_DIR", WORKSPACE_DIR)
    home = Path.home()
    candidate_bases = [
        home / ".gemini" / "antigravity-cli" / "brain",
        home / ".gemini" / "antigravity" / "brain",
        Path("/home/ubuntu/.gemini/antigravity-cli/brain"),
        Path("/home/ubuntu/.gemini/antigravity/brain"),
        Path(current_workspace) / ".gemini" / "brain",
    ]
    valid_bases = [b for b in candidate_bases if b.is_dir()]

    discovered = []
    seen_ids = set()
    for base in valid_bases:
        try:
            for item in base.iterdir():
                if item.is_dir() and item.name not in seen_ids:
                    t_path = item / ".system_generated" / "logs" / "transcript.jsonl"
                    if t_path.is_file():
                        mtime = t_path.stat().st_mtime
                        discovered.append((mtime, item.name, base))
                        seen_ids.add(item.name)
        except Exception:
            pass

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
    for idx, (mtime, cid, base) in enumerate(recent, 1):
        rel_time = format_relative_time(mtime)
        is_active = " 👈 <i>(Aktif)</i>" if cid == current_conv else ""
        lines.append(f"{idx}. <code>{cid}</code> ({rel_time}){is_active}")

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

    db = get_db()
    if thread_id is not None:
        db.set_topic_binding(chat_id, thread_id, conv_id=target_id)
    else:
        user_conversations[user_id] = target_id

    await safe_send_message(
        context.bot,
        chat_id,
        f"✓ <b>Berhasil beralih ke sesi:</b>\n<code>{target_id}</code>\n\n"
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
    cancelled_anything = False

    proc = user_processes.get(user_id)
    if proc and proc.returncode is None:
        try:
            proc.terminate()
            await asyncio.sleep(0.5)
            if proc.returncode is None:
                proc.kill()
            cancelled_anything = True
            logger.info(f"Subprocess agy (PID: {proc.pid}) untuk user {user_id} dimatikan via /cancel.")
        except Exception as e:
            logger.warning(f"Error mematikan proses {user_id}: {e}")

    task = user_tasks.get(user_id)
    if task and not task.done():
        task.cancel()
        cancelled_anything = True

    for req_id, info in list(pending_approvals.items()):
        if info.get("user_id") == user_id:
            fut = info.get("future")
            if fut and not fut.done():
                fut.cancel()
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
            "🛑 **Tugas berhasil dibatalkan!** Subprocess `agy` dan eksekusi telah dihentikan.",
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


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Central callback router for approval confirmations, model selection, and help center."""
    query = update.callback_query
    if query is None or not query.data:
        return

    data = query.data
    if data.startswith("appr:") or data.startswith("deny:"):
        await handle_approval_callback(update, context)
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
        fetch_fn = getattr(sys.modules[__name__], "fetch_agy_usage_report", fetch_agy_usage_report)
        try:
            report_html = await fetch_fn()
            usage_kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔄 Refresh", callback_data="usage_refresh"),
                    InlineKeyboardButton("✖️ Tutup", callback_data="msg_close")
                ]
            ])
            await safe_edit_message(query.message, report_html, reply_markup=usage_kb, parse_mode=ParseMode.HTML)
        except Exception as e:
            await query.answer(f"Gagal refresh: {e}", show_alert=True)


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
        category = action[4:]
        text, markup = get_help_menu_content(category)
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
        await send_model_picker(chat_id, thread_id, page=0, context=context)
    elif action == "act_usage":
        fetch_fn = getattr(sys.modules[__name__], "fetch_agy_usage_report", fetch_agy_usage_report)
        try:
            report_html = await fetch_fn()
            usage_kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔄 Refresh", callback_data="usage_refresh"),
                    InlineKeyboardButton("✖️ Tutup", callback_data="msg_close")
                ]
            ])
            await safe_send_message(context.bot, chat_id, report_html, reply_markup=usage_kb, parse_mode=ParseMode.HTML, message_thread_id=thread_id)
        except Exception as e:
            await safe_send_message(context.bot, chat_id, f"❌ Gagal mengambil kuota: {e}", message_thread_id=thread_id)
    elif action == "act_status":
        await status_command(update, context)
    elif action == "act_reset":
        await reset_command(update, context)
    elif action == "act_cancel":
        await cancel_command(update, context)


# ==============================================================================
# AGENT WORKFLOW & MESSAGE EXECUTION
# ==============================================================================
async def execute_agent_turn(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_text: str
):
    """
    Executes one turn against native agy CLI subprocess under user mutex lock.
    Incorporates Hermes reactions, quiet pinning, in-place status, draft streaming,
    and automatic topic renaming.
    """
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    raw_thread = getattr(update.message, "message_thread_id", None)
    thread_id = raw_thread if isinstance(raw_thread, int) else None

    raw_reply = getattr(update.message, "message_id", None)
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
    active_conv = None
    if thread_id is not None:
        active_conv, _ = get_conversation_for_message(chat_id, thread_id)
    if not active_conv:
        active_conv = user_conversations.get(user_id)

    active_model = db.get_user_model(user_id, chat_id, thread_id or "root") or DEFAULT_MODEL
    current_workspace = globals().get("WORKSPACE_DIR", WORKSPACE_DIR)
    turn_success = False

    run_fn = getattr(sys.modules[__name__], "run_agy_cli", run_agy_cli)
    try:
        output_text, new_conv_id = await run_fn(
            user_id=user_id,
            prompt=str(user_text),
            conv_id=active_conv,
            cwd=current_workspace,
            model=active_model
        )
        turn_success = True

        elapsed_seconds = max(1, int(time.time() - turn_start_time))
        duration_str = f"~{elapsed_seconds}s" if elapsed_seconds < 60 else f"~{elapsed_seconds // 60}m {elapsed_seconds % 60}s"

        if new_conv_id:
            if thread_id is not None:
                bind_conversation_to_topic(chat_id, thread_id, new_conv_id)
                asyncio.create_task(auto_rename_forum_topic(context.bot, chat_id, thread_id, str(user_text), output_text))
            else:
                user_conversations[user_id] = new_conv_id

        # 4. Media Dispatch with strict Security Path Traversal Guard
        media_paths = extract_media_paths(output_text, workspace_dir=current_workspace)

        # 5. Format HTML with badge
        formatted_html = markdown_to_telegram_html(output_text)
        non_empty_lines = [l.strip() for l in formatted_html.splitlines() if l.strip()]
        last_line = non_empty_lines[-1].lower() if non_empty_lines else ""
        has_footer = bool(re.search(r"^⏱️.*(?:respons dalam|waktu respons|durasi pengerjaan)", last_line))
        if not has_footer:
            formatted_html = f"{formatted_html.rstrip()}\n\n⏱️ <i>Respons dalam {duration_str}</i>"

        # 6. Split message safely (<4000 char)
        chunks = split_message(formatted_html, max_length=4000)

        # Remove temporary waiting bubble
        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass
            status_msg = None

        # Send formatted chunks
        for i, chunk in enumerate(chunks):
            await safe_send_message(
                context.bot,
                chat_id,
                chunk,
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
                message_thread_id=thread_id
            )

    except asyncio.CancelledError:
        logger.info(f"Task user {user_id} dibatalkan.")
        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass
            status_msg = None
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
        err_msg = (
            f"❌ <b>Terjadi kesalahan saat memproses permintaan:</b>\n"
            f"<pre><code>{html.escape(str(e))[:1000]}</code></pre>\n\n"
            f"💡 <i>Petunjuk:</i> Pastikan binary <code>agy</code> terpasang di path yang sesuai atau gunakan <code>/reset</code>."
        )
        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass
            status_msg = None
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


async def _dispatch_agent_turn(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    prompt: str
):
    """Dispatches prompt into user mutex lock queue."""
    user_id = update.effective_user.id

    existing_task = user_tasks.get(user_id)
    if existing_task and not existing_task.done():
        if update.message:
            await update.message.reply_text(
                "⏳ *Antigravity sedang menyelesaikan tugas sebelumnya.*\n"
                "Kirim `/cancel` jika Anda ingin menghentikan proses tersebut.",
                parse_mode=ParseMode.MARKDOWN
            )
        return

    user_lock = get_user_lock(user_id)
    if user_lock.locked():
        if update.message:
            await update.message.reply_text("⏳ Mohon tunggu sebentar, sesi Anda sedang sibuk.")
        return

    async def _locked_runner():
        async with user_lock:
            await execute_agent_turn(update, context, prompt)

    task = asyncio.create_task(_locked_runner())
    user_tasks[user_id] = task

    def _cleanup_task(t):
        if user_tasks.get(user_id) == t:
            user_tasks.pop(user_id, None)

    task.add_done_callback(_cleanup_task)


# ==============================================================================
# INBOUND MEDIA & MESSAGE HANDLERS
# ==============================================================================
async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    if not update.message or not update.message.photo:
        return

    user_id = update.effective_user.id
    photo = update.message.photo[-1]

    try:
        file_obj = await photo.get_file()
        upload_dir = get_upload_dir()
        filename = f"photo_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg"
        dest_path = (upload_dir / filename).resolve()
        await file_obj.download_to_drive(custom_path=dest_path)
        logger.info(f"Foto berhasil diunduh ke: {dest_path}")
    except Exception as e:
        logger.error(f"Gagal mengunduh foto untuk user {user_id}: {e}", exc_info=True)
        if update.message:
            try:
                await update.message.reply_text(f"❌ Gagal mengunduh foto: {e}")
            except Exception:
                await safe_send_message(context.bot, update.effective_chat.id, f"❌ Gagal mengunduh foto: {e}")
        return

    caption = (update.message.caption or "").strip()
    caption_prompt = caption if caption else "Tolong periksa dan analisis gambar terlampir ini."

    prompt_text = (
        f"[PENGGUNA MENGIRIMKAN GAMBAR / SCREENSHOT]\n"
        f"Berkas gambar telah disimpan di: {dest_path}\n\n"
        f"[INSTRUKSI / CAPTION PENGGUNA]:\n"
        f"{caption_prompt}"
    )

    dispatch_fn = getattr(sys.modules[__name__], "_dispatch_agent_turn", _dispatch_agent_turn)
    await dispatch_fn(update, context, prompt_text)


async def handle_document_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    if not update.message or not update.message.document:
        return

    user_id = update.effective_user.id
    doc = update.message.document

    try:
        file_obj = await doc.get_file()
        orig_name = doc.file_name or f"doc_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        clean_name = re.sub(r'[\/:*?"<>|\x00-\x1f]', '_', Path(orig_name).name).strip()
        safe_name = clean_name
        if not safe_name.strip("._"):
            safe_name = f"doc_{int(time.time())}_{uuid.uuid4().hex[:6]}"

        prefix = f"{int(time.time())}_{uuid.uuid4().hex[:4]}_"
        filename = f"{prefix}{safe_name}"
        upload_dir = get_upload_dir()
        dest_path = (upload_dir / filename).resolve()
        await file_obj.download_to_drive(custom_path=dest_path)
        logger.info(f"Dokumen '{orig_name}' berhasil diunduh ke: {dest_path}")
    except Exception as e:
        logger.error(f"Gagal mengunduh dokumen untuk user {user_id}: {e}", exc_info=True)
        if update.message:
            try:
                await update.message.reply_text(f"❌ Gagal mengunduh dokumen: {e}")
            except Exception:
                await safe_send_message(context.bot, update.effective_chat.id, f"❌ Gagal mengunduh dokumen: {e}")
        return

    caption = (update.message.caption or "").strip()
    caption_prompt = caption if caption else "Tolong periksa, baca, dan analisis dokumen terlampir ini."

    prompt_text = (
        f"[PENGGUNA MENGIRIMKAN DOKUMEN / BERKAS]\n"
        f"Nama berkas asli: {orig_name}\n"
        f"Berkas telah disimpan di: {dest_path}\n\n"
        f"[INSTRUKSI / CAPTION PENGGUNA]:\n"
        f"{caption_prompt}"
    )

    dispatch_fn = getattr(sys.modules[__name__], "_dispatch_agent_turn", _dispatch_agent_turn)
    await dispatch_fn(update, context, prompt_text)


async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inbound Voice Note / Audio memo handler."""
    if not is_authorized(update):
        return
    msg = update.message
    if not msg or not (msg.voice or msg.audio):
        return

    media = msg.voice or msg.audio
    user_id = update.effective_user.id

    try:
        file_obj = await media.get_file()
        ext = ".ogg" if msg.voice else ".mp3"
        filename = f"voice_{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"
        upload_dir = get_upload_dir()
        dest_path = (upload_dir / filename).resolve()
        await file_obj.download_to_drive(custom_path=dest_path)
        logger.info(f"Voice memo berhasil diunduh ke: {dest_path}")
    except Exception as e:
        logger.error(f"Gagal mengunduh audio: {e}")
        if msg:
            await msg.reply_text(f"❌ Gagal mengunduh pesan suara: {e}")
        return

    caption = (msg.caption or "").strip()
    prompt_text = (
        f"[PENGGUNA MENGIRIMKAN REKAMAN SUARA / VOICE NOTE]\n"
        f"Berkas audio tersimpan di: {dest_path}\n\n"
        f"[CATATAN / CAPTION]:\n{caption if caption else 'Mohon dengarkan dan tindak lanjuti pesan suara ini.'}"
    )
    dispatch_fn = getattr(sys.modules[__name__], "_dispatch_agent_turn", _dispatch_agent_turn)
    await dispatch_fn(update, context, prompt_text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main router for incoming text messages."""
    if not is_authorized(update):
        user_id = update.effective_user.id if update.effective_user else "Unknown"
        logger.warning(f"Unauthorized text message attempt from User ID: {user_id}")
        return

    raw_thread = getattr(update.message, "message_thread_id", None)
    thread_id = raw_thread if isinstance(raw_thread, int) else None

    # Expand any hidden text_link URLs
    user_text = ""
    if update.message:
        if isinstance(update.message.text, str):
            user_text = expand_link_entities(update.message)
        elif isinstance(getattr(update.message, "caption", None), str):
            user_text = expand_link_entities(update.message)

    raw_username = getattr(context.bot, "username", "")
    bot_username = raw_username if isinstance(raw_username, str) else ""
    user_text = clean_bot_mentions(user_text, bot_username)

    if not user_text.strip():
        return

    # Intent Interceptor: Quota / Usage Inquiry
    inquiry_fn = getattr(sys.modules[__name__], "is_quota_inquiry", is_quota_inquiry)
    if inquiry_fn(user_text):
        status_msg = await safe_send_message(
            context.bot,
            update.effective_chat.id,
            "⏳ *Mengambil data kuota model dari Antigravity CLI...*",
            parse_mode=ParseMode.MARKDOWN,
            message_thread_id=thread_id
        )
        fetch_fn = getattr(sys.modules[__name__], "fetch_agy_usage_report", fetch_agy_usage_report)
        try:
            report_html = await fetch_fn()
            if status_msg:
                await safe_edit_message(status_msg, report_html, parse_mode=ParseMode.HTML)
            else:
                await safe_send_message(context.bot, update.effective_chat.id, report_html, parse_mode=ParseMode.HTML, message_thread_id=thread_id)
        except Exception as e:
            logger.error(f"Error handling quota inquiry: {e}")
        return

    dispatch_fn = getattr(sys.modules[__name__], "_dispatch_agent_turn", _dispatch_agent_turn)
    await dispatch_fn(update, context, user_text)


# ==============================================================================
# LIFECYCLE HOOKS & APPLICATION BUILDER
# ==============================================================================
async def post_init(application):
    """Registers bot commands with Telegram API on startup."""
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


async def post_shutdown(application):
    """Cleans up running subprocesses and state on shutdown."""
    logger.info("Menutup seluruh subprocess Antigravity...")
    for user_id, proc in list(user_processes.items()):
        try:
            if proc.returncode is None:
                proc.terminate()
        except Exception:
            pass
    user_processes.clear()
    user_conversations.clear()


def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN belum diatur di file .env! Bot tidak dapat berjalan.")
        sys.exit(1)

    if not ALLOWED_USER_IDS:
        logger.warning("⚠️ ALLOWED_USER_ID belum diatur. Tidak ada pengguna yang dapat mengakses bot!")

    logger.info(f"🤖 Antigravity Engine : Subprocess agy CLI (Hermes-Parity)")
    logger.info(f"📂 Binary Path        : {AGY_BIN_PATH}")
    logger.info(f"🛡️ Whitelist User IDs : {ALLOWED_USER_IDS}")
    logger.info(f"⚙️ Approval Mode      : {APPROVAL_MODE} (Timeout: {APPROVAL_TIMEOUT_SECONDS}s)")
    logger.info(f"📁 Workspace Path     : {WORKSPACE_DIR}")

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

    # Callback Query Handlers (Approval & Model selection)
    app.add_handler(CallbackQueryHandler(handle_callback_query))

    # Media Handlers
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_message))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice_message))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document_message))

    # Text Messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🚀 Antigravity CLI Telegram Bot siap berjalan...")
    app.run_polling()


if __name__ == "__main__":
    main()
