#!/usr/bin/env python3
"""
Antigravity Telegram Bot (Native agy CLI Subprocess Engine)
Terintegrasi langsung dengan binary resmi agy di VPS/Host.

Fitur Unggulan:
1. Bebas API Key - Menggunakan sesi OAuth Google Antigravity bawaan (~/.gemini/antigravity-cli/)
2. Super Hemat RAM (~35 MB) - Ideal dijalankan via systemd service di VPS
3. Multi-turn Stateful Memory via flag `agy --conversation <CONV_ID>`
4. Hardline Security Blocklist (Blokir total perintah katastropik: rm -rf /, forkbomb, dd, mkfs, shutdown)
5. Hermes-Style Interactive Approval (Inline Buttons [Approve] / [Deny] dengan fail-closed timeout)
6. Real Process Cancellation (/cancel langsung mematikan PID subprocess agy)
7. Live Progress Feedback (Timer detik berjalan + Telegram typing status)
8. Media & Document Dispatcher (Deteksi otomatis format MEDIA:/path/ke/file)
9. Safe Markdown Splitter (< 4000 karakter dengan auto fallback ke teks polos)
"""

import os
import sys
import re
import json
import time
from datetime import datetime, timezone
import uuid
import html
import shutil
import asyncio
import logging
from pathlib import Path
from typing import Dict, Set, Optional, Tuple, List
from dotenv import load_dotenv

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

load_dotenv()

# ==============================================================================
# LOGGING SETUP
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("antigravity-tele-bot")

# ==============================================================================
# CONFIGURATION & ENVIRONMENT
# ==============================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

raw_allowed = os.getenv("ALLOWED_USER_ID", "0").strip()
ALLOWED_USER_IDS: Set[int] = set()
for part in raw_allowed.split(","):
    part = part.strip()
    if part.isdigit():
        ALLOWED_USER_IDS.add(int(part))

# Path binary agy di VPS atau Laptop
AGY_BIN_PATH = os.getenv(
    "AGY_BIN_PATH",
    shutil.which("agy") or shutil.which("agy.exe") or "/home/ubuntu/.local/bin/agy"
).strip()

def resolve_workspace_dir() -> str:
    """
    Memvalidasi dan mengembalikan path direktori kerja (workspace) yang valid.
    Jika WORKSPACE_DIR di .env tidak valid atau menunjuk ke folder yang tidak ada
    (misalnya peninggalan '/workspace' Docker), otomatis menggunakan fallback direktori home.
    """
    raw = os.getenv("WORKSPACE_DIR", "").strip()
    if raw and os.path.exists(raw) and os.path.isdir(raw):
        return raw

    # Fallback 1: Jika di Linux VPS host dan /home/ubuntu ada
    if os.path.isdir("/home/ubuntu"):
        if raw and raw != "/home/ubuntu":
            logger.warning(f"WORKSPACE_DIR '{raw}' tidak ditemukan. Menggunakan fallback: /home/ubuntu")
        return "/home/ubuntu"

    # Fallback 2: Direktori home pengguna saat ini
    home = Path.home()
    if home.is_dir():
        if raw and raw != str(home):
            logger.warning(f"WORKSPACE_DIR '{raw}' tidak ditemukan. Menggunakan fallback home: {home}")
        return str(home)

    return os.getcwd()

WORKSPACE_DIR = resolve_workspace_dir()

def get_upload_dir() -> Path:
    """
    Memastikan dan mengembalikan direktori penyimpanan berkas unggahan Telegram (.telegram_uploads).
    """
    upload_dir = Path(WORKSPACE_DIR) / ".telegram_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir

APPROVAL_MODE = os.getenv("APPROVAL_MODE", "ask_destructive").strip().lower()
APPROVAL_TIMEOUT_SECONDS = int(os.getenv("APPROVAL_TIMEOUT_SECONDS", "120"))
AGY_TIMEOUT_SECONDS = int(os.getenv("AGY_TIMEOUT_SECONDS", "180"))

# ==============================================================================
# SYSTEM INSTRUCTIONS & PROMPT BUILDER
# ==============================================================================
SYSTEM_INSTRUCTIONS = (
    "Anda adalah asisten AI Antigravity yang terhubung melalui Telegram Bot di VPS Linux. "
    f"Direktori kerja utama: {WORKSPACE_DIR}.\n\n"
    "ATURAN OPERASIONAL PENTING:\n"
    "1. Anda berjalan dalam sesi headless non-interaktif (print mode).\n"
    "2. JANGAN PERNAH menggunakan tool internal `schedule` untuk recurring cron atau background timers. "
    "Jika pengguna meminta cron job atau penjadwalan otomatis, selalu buat script dan pasang langsung ke crontab Linux host via terminal (`crontab`).\n"
    "3. Selalu selesaikan eksekusi perintah terminal sebelum mengakhiri giliran Anda."
)

def build_cli_prompt(prompt: str) -> str:
    """
    Menyusun prompt pengguna dengan menyisipkan instruksi operasional headless VPS
    agar model AI tidak menggunakan tool internal schedule dan selalu mengutamakan crontab host.
    """
    return f"[INSTRUKSI SISTEM]\n{SYSTEM_INSTRUCTIONS}\n\n[PERMINTAAN PENGGUNA]\n{prompt}"


# ==============================================================================
# STATE & REGISTRY
# ==============================================================================
# Menyimpan conversation_id resmi agy per user_id Telegram (Multi-turn Memory)
user_conversations: Dict[int, str] = {}

# Menyimpan subprocess aktif per user_id untuk keperluan pembatalan (/cancel)
user_processes: Dict[int, asyncio.subprocess.Process] = {}

# Pending approval registry:
# { request_id: {"future": asyncio.Future, "user_id": int, "message": Message, "text": str} }
pending_approvals: Dict[str, dict] = {}

# User mutex locks & active tasks
user_locks: Dict[int, asyncio.Lock] = {}
user_tasks: Dict[int, asyncio.Task] = {}

def get_user_lock(user_id: int) -> asyncio.Lock:
    """Mendapatkan mutex lock per user untuk mencegah race condition percakapan."""
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]

# ==============================================================================
# HARDLINE SECURITY BLOCKLIST & INTENT GUARD
# ==============================================================================
HARDLINE_BLOCKLIST = [
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
    r"mkfs(?:\.[a-z0-9]+)?\s+",
    r"dd\s+if=.*of=/dev/(?:sd[a-z]|nvme[0-9]|hd[a-z]|vd[a-z]|mmcblk)",
    r"fdisk\s+/dev/",
    r">\s*/dev/(?:sd[a-z]|nvme[0-9]|hd[a-z]|vd[a-z])",
    r"chmod\s+-[rR]\s+777\s+/(?:\s|$)",
    r"chown\s+-[rR]\s+.*\s+/(?:\s|$)",
    r"(?:^|[\s;&|])(?:shutdown|reboot|poweroff|halt|init\s+0)(?:$|[\s;&|])",
    r"\brm\s+.*-(?:[a-zA-Z0-9]*[rR]|--recursive).*/(?:\*|\s|$)"
]

def is_hardline_blocked(prompt: str) -> bool:
    """Mengecek apakah teks instruksi mengandung perintah katastropik OS."""
    p_clean = prompt.strip()
    for pattern in HARDLINE_BLOCKLIST:
        if re.search(pattern, p_clean, re.IGNORECASE):
            return True
    return False

# Pola-pola instruksi yang berpotensi mutatif/destruktif dan memerlukan Interactive Approval
DESTRUCTIVE_PATTERNS = [
    r"\b(?:hapus|delete|remove|unlink|shred)\b",
    r"\brm\s+-[a-zA-Z0-9]*[rfRF]",
    r"\b(?:drop\s+database|drop\s+table|truncate\s+table)\b",
    r"\b(?:migrate:fresh|db:wipe)\b",
    r"\bgit\s+(?:reset\s+--hard|clean\s+-[a-zA-Z0-9]*f|push\s+.*--force)\b",
    r"\bdocker\s+(?:rm|rmi|system\s+prune|compose\s+down\s+-v)\b",
    r"\b(?:kill\s+-9|pkill\s+-9|killall)\b",
    r"\bformat\s+(?:disk|drive)\b",
]

def is_destructive_prompt(prompt: str) -> Tuple[bool, str]:
    """
    Mengevaluasi apakah prompt meminta tindakan berisiko tinggi.
    Mengembalikan tuple (is_destructive: bool, reason: str).
    """
    if APPROVAL_MODE == "auto_approve":
        return False, ""

    prompt_low = prompt.strip().lower()
    for pattern in DESTRUCTIVE_PATTERNS:
        match = re.search(pattern, prompt_low)
        if match:
            trigger = match.group(0)
            return True, f"Terdeteksi kata/perintah berisiko: `{trigger}`"

    return False, ""

# ==============================================================================
# TELEGRAM HELPER UTILITIES
# ==============================================================================
def is_authorized(update: Update) -> bool:
    """Verifikasi apakah pengguna ada di whitelist ALLOWED_USER_IDS."""
    user = update.effective_user
    if user is None:
        return False
    return user.id in ALLOWED_USER_IDS

def split_message(text: str, max_length: int = 4000) -> List[str]:
    """
    Memecah teks panjang secara aman agar tidak melampaui limit karakter Telegram (4096).
    Memotong pada boundary baris baru (\n) atau spasi jika memungkinkan.
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

def markdown_to_telegram_html(text: str) -> str:
    """
    Mengonversi output Markdown dari AI ke format Telegram HTML yang valid dan rapi.
    Sangat tahan banting terhadap underscore (seperti nama container Docker),
    karakter khusus, regex, dan format heading.
    """
    if not text:
        return ""

    # 1. Simpan code blocks (```...```) agar isinya tidak terpengaruh format lain
    code_blocks = []
    def save_code_block(match):
        lang = (match.group(1) or "").strip()
        code = match.group(2)
        idx = len(code_blocks)
        escaped_code = html.escape(code.strip("\r\n"))
        if lang:
            replacement = f'<pre><code class="language-{html.escape(lang)}">{escaped_code}</code></pre>'
        else:
            replacement = f"<pre><code>{escaped_code}</code></pre>"
        code_blocks.append(replacement)
        return f"\x00CODEBLOCK{idx}\x00"

    text = re.sub(r"```([a-zA-Z0-9_\+\-]*)?\n([\s\S]*?)```", save_code_block, text)

    # 2. Simpan GFM pipe tables agar rapi & monospace di mobile Telegram (<pre><code>...</code></pre>)
    table_pattern = re.compile(
        r"(?m)^([ \t]*\|?[^\n|]+\|[^\n]*\n"
        r"[ \t]*\|?(?:[ \t]*:?-+:?[ \t]*\|)+[ \t]*:?-+:?[ \t]*\|?[ \t]*(?:\n|$))"
        r"((?:[ \t]*\|?[^\n|]+\|[^\n]*(?:\n|$))*)"
    )

    def save_markdown_table(match):
        raw = match.group(0)
        has_trailing_newline = raw.endswith("\n")
        table_raw = raw.strip("\r\n")
        idx = len(code_blocks)
        escaped_table = html.escape(table_raw)
        replacement = f"<pre><code>{escaped_table}</code></pre>"
        code_blocks.append(replacement)
        return f"\x00CODEBLOCK{idx}\x00\n" if has_trailing_newline else f"\x00CODEBLOCK{idx}\x00"

    text = table_pattern.sub(save_markdown_table, text)

    # 3. Simpan inline code (`...`)
    inline_codes = []
    def save_inline_code(match):
        code = match.group(1)
        idx = len(inline_codes)
        escaped_code = html.escape(code)
        inline_codes.append(f"<code>{escaped_code}</code>")
        return f"\x00INLINECODE{idx}\x00"

    text = re.sub(r"`([^`\n]+)`", save_inline_code, text)

    # 3. Escape HTML pada sisa teks biasa (&, <, >)
    text = html.escape(text)

    # 4. Format headers (###, ##, #) menjadi bold tanpa tanda pagar dan tanpa double **
    def format_header(match):
        content = match.group(1).strip()
        clean_content = re.sub(r"\*\*(.*?)\*\*", r"\1", content)
        return f"<b>{clean_content}</b>"

    text = re.sub(r"(?m)^#{1,6}\s*(.*?)$", format_header, text)

    # 5. Format bullet points (* atau - di awal baris) menjadi simbol bullet rapi (• )
    text = re.sub(r"(?m)^[\*\-]\s+", r"• ", text)

    # 6. Format bold (**text** atau __text__) -> <b>text</b>
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)

    # 7. Format italic (*text* atau _text_)
    text = re.sub(r"(?<!\w)\*([^\*\n]+?)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!\w)_([^_\n]+?)_(?!\w)", r"<i>\1</i>", text)

    # 8. Format blockquote (> text) -> <blockquote>text</blockquote>
    text = re.sub(r"(?m)^&gt;\s*(.*?)$", r"<blockquote>\1</blockquote>", text)

    # 9. Kembalikan inline codes dan code blocks
    for idx, replacement in enumerate(inline_codes):
        text = text.replace(f"\x00INLINECODE{idx}\x00", replacement)

    for idx, replacement in enumerate(code_blocks):
        text = text.replace(f"\x00CODEBLOCK{idx}\x00", replacement)

    return text

async def safe_send_message(
    bot,
    chat_id: int,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: Optional[str] = ParseMode.HTML,
    disable_notification: bool = False,
    reply_to_message_id: Optional[int] = None
) -> Optional[Message]:
    """
    Mengirim pesan Telegram dengan fallback otomatis ke teks polos jika parsing HTML/Markdown gagal.
    Mendukung silent notification (disable_notification) dan quote reply anchoring (reply_to_message_id).
    """
    try:
        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_notification=disable_notification,
            reply_to_message_id=reply_to_message_id
        )
    except BadRequest as e:
        logger.warning(f"Error saat send_message ({e}). Mengirim ulang sebagai plain text...")
        effective_reply_to = reply_to_message_id
        if "repl" in str(e).lower():
            effective_reply_to = None
        try:
            return await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=None,
                disable_notification=disable_notification,
                reply_to_message_id=effective_reply_to
            )
        except BadRequest as e2:
            if "repl" in str(e2).lower() and effective_reply_to is not None:
                # Jika pesan yang di-reply sudah dihapus pengguna, coba kirim tanpa reply_to_message_id
                try:
                    return await bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        reply_markup=reply_markup,
                        parse_mode=None,
                        disable_notification=disable_notification
                    )
                except Exception:
                    pass
            logger.error(f"Gagal total mengirim pesan plain text: {e2}")
            return None
        except Exception as e2:
            logger.error(f"Gagal total mengirim pesan plain text: {e2}")
            return None
    except Exception as e:
        logger.error(f"Error tidak terduga di safe_send_message: {e}")
        return None

async def safe_edit_message(
    msg: Message,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: Optional[str] = ParseMode.HTML
) -> bool:
    """
    Mengedit pesan Telegram dengan fallback otomatis ke plain text jika formatting invalid.
    """
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
            if "not modified" in str(e2).lower():
                return True
            logger.error(f"Gagal mengedit pesan plain text: {e2}")
            return False
    except Exception as e:
        if "not modified" in str(e).lower():
            return True
        logger.error(f"Error tidak terduga di safe_edit_message: {e}")
        return False

def extract_media_paths(text: str, workspace_dir: str = WORKSPACE_DIR) -> List[str]:
    """
    Mendeteksi sintaks MEDIA:/path/ke/file atau [MEDIA: /path/ke/file] di dalam output.
    Memvalidasi apakah file benar-benar ada di filesystem.
    """
    pattern = r"(?:\[MEDIA:\s*([^\]]+)\]|(?:^|\s)MEDIA:\s*([^\s\r\n]+))"
    matches = re.findall(pattern, text, re.IGNORECASE)
    found_paths = []

    for m in matches:
        raw_path = (m[0] or m[1]).strip().strip("\"'")
        p = Path(raw_path)
        if not p.is_absolute():
            candidate = Path(workspace_dir) / raw_path
            if candidate.exists() and candidate.is_file():
                found_paths.append(str(candidate.resolve()))
                continue
        if p.exists() and p.is_file():
            found_paths.append(str(p.resolve()))

    return list(dict.fromkeys(found_paths))

async def send_typing_and_progress(
    bot,
    chat_id: int,
    status_msg: Optional[Message],
    stop_event: asyncio.Event
):
    """Mengirim status 'typing...' dan update timer durasi setiap 4 detik."""
    start_time = time.time()
    while not stop_event.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
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
                    f"_Kirim /cancel untuk menghentikan proses kapan saja._"
                )

# ==============================================================================
# APPROVAL WORKFLOW (ALA HERMES)
# ==============================================================================
async def request_user_approval(
    bot,
    chat_id: int,
    user_id: int,
    prompt: str,
    reason: str
) -> bool:
    """
    Mengirimkan Inline Keyboard konfirmasi (Approve/Deny) ke Telegram dan menunggu respons.
    Menggunakan asyncio.Future dengan mekanisme fail-closed timeout.
    """
    request_id = str(uuid.uuid4())[:8]
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Setujui (Approve)", callback_data=f"appr:{request_id}"),
            InlineKeyboardButton("❌ Tolak (Deny)", callback_data=f"deny:{request_id}")
        ]
    ])

    pesan_approval = (
        "⚠️ **PERMINTAAN PERSETUJUAN EKSEKUSI (Hermes Guard)**\n\n"
        f"• **Alasan Deteksi**: {reason}\n"
        f"• **Instruksi Akang**:\n```bash\n{prompt[:1000]}\n```\n"
        f"⏱️ *Batas Waktu:* `{APPROVAL_TIMEOUT_SECONDS} detik (Fail-Closed)`\n\n"
        "Apakah Akang yakin ingin mengizinkan eksekusi instruksi ini?"
    )

    msg = await safe_send_message(
        bot=bot,
        chat_id=chat_id,
        text=pesan_approval,
        reply_markup=keyboard
    )

    pending_approvals[request_id] = {
        "future": future,
        "user_id": user_id,
        "message": msg,
        "text": pesan_approval
    }

    try:
        approved = await asyncio.wait_for(future, timeout=APPROVAL_TIMEOUT_SECONDS)
        status_label = "✅ **DISETUJUI (APPROVED)**" if approved else "❌ **DITOLAK (DENIED)**"
        if msg:
            await safe_edit_message(msg, f"{pesan_approval}\n\nStatus: {status_label}")
        return approved
    except asyncio.TimeoutError:
        logger.warning(f"Approval request {request_id} timed out (Fail-closed).")
        if msg:
            await safe_edit_message(
                msg,
                f"{pesan_approval}\n\nStatus: ⏱️ **KADALUWARSA (TIMEOUT - DITOLAK OTOMATIS)**"
            )
        return False
    except asyncio.CancelledError:
        logger.info(f"Approval request {request_id} dibatalkan.")
        if msg:
            await safe_edit_message(
                msg,
                f"{pesan_approval}\n\nStatus: 🛑 **DIBATALKAN VIA /cancel**"
            )
        return False
    finally:
        pending_approvals.pop(request_id, None)

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menangani interaksi klik tombol Inline Keyboard Approve / Deny."""
    query = update.callback_query
    if query is None:
        return

    await query.answer()

    if not is_authorized(update):
        await query.edit_message_text("⛔ Anda tidak berwenang menekan tombol ini.")
        return

    data = query.data or ""
    action, _, request_id = data.partition(":")

    if request_id in pending_approvals:
        entry = pending_approvals[request_id]
        future = entry.get("future")
        if future and not future.done():
            if action == "appr":
                future.set_result(True)
            elif action == "deny":
                future.set_result(False)

# ==============================================================================
# TRANSCRIPT DISCOVERY & FALLBACK RECOVERY
# ==============================================================================
def get_transcript_path(conv_id: Optional[str]) -> Tuple[Optional[Path], Optional[str]]:
    """
    Mencari lokasi berkas transcript.jsonl untuk conv_id tertentu.
    Jika conv_id adalah None, otomatis mencari sesi percakapan terbaru di folder brain.
    Mengembalikan tuple: (transcript_path, resolved_conv_id).
    """
    candidate_bases: List[Path] = []

    # 1. Direktori home pengguna saat ini
    home = Path.home()
    candidate_bases.extend([
        home / ".gemini" / "antigravity-cli" / "brain",
        home / ".gemini" / "antigravity" / "brain",
    ])

    # 2. Path standar VPS Linux (/home/ubuntu)
    candidate_bases.extend([
        Path("/home/ubuntu/.gemini/antigravity-cli/brain"),
        Path("/home/ubuntu/.gemini/antigravity/brain"),
    ])

    # 3. Path dari workspace jika ada folder brain
    candidate_bases.append(Path(WORKSPACE_DIR) / ".gemini" / "brain")

    # Filter direktori basis yang valid dan ada di filesystem
    valid_bases = [b for b in candidate_bases if b.is_dir()]

    if conv_id:
        for base in valid_bases:
            cand = base / conv_id / ".system_generated" / "logs" / "transcript.jsonl"
            if cand.is_file():
                return cand, conv_id
        return None, conv_id

    # Jika conv_id is None (sesi baru yang hang sebelum ID tercatat), cari direktori termutakhir
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
            logger.warning(f"Gagal memeriksa direktori brain {base}: {e}")

    if newest_file and found_conv_id:
        return newest_file, found_conv_id

    return None, None


def recover_last_response_from_transcript(conv_id: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Membaca jawaban terakhir model AI dari transcript.jsonl jika subprocess agy hang/timeout.
    Dilengkapi Anti-Stale Turn Protection: jika menemukan USER_INPUT sebelum PLANNER_RESPONSE,
    berarti model belum merespons giliran ini (hindari mengembalikan jawaban basi dari turn sebelumnya).
    Mengembalikan tuple: (recovered_text, actual_conv_id).
    """
    transcript_path, resolved_conv_id = get_transcript_path(conv_id)
    if not transcript_path or not transcript_path.is_file():
        return None, resolved_conv_id

    try:
        content = transcript_path.read_text(encoding="utf-8", errors="replace")
        lines = [line.strip() for line in content.splitlines() if line.strip()]

        for line in reversed(lines):
            try:
                data = json.loads(line)
            except Exception:
                continue

            # Anti-Stale Turn Protection: Jika menabrak giliran USER_INPUT terbaru sebelum ada PLANNER_RESPONSE bertarget,
            # berarti giliran saat ini belum sempat menghasilkan teks balasan.
            if data.get("source") in ("USER_EXPLICIT", "USER") and data.get("type") == "USER_INPUT":
                logger.info(
                    f"Menemukan USER_INPUT sebelum PLANNER_RESPONSE di transkrip ({transcript_path}). "
                    f"Tidak ada balasan baru untuk giliran ini."
                )
                break

            if (
                data.get("source") == "MODEL"
                and data.get("type") == "PLANNER_RESPONSE"
                and data.get("status") == "DONE"
                and data.get("content")
            ):
                recovered_content = str(data["content"]).strip()
                if recovered_content:
                    logger.info(
                        f"Berhasil me-recover balasan model ({len(recovered_content)} karakter) "
                        f"dari {transcript_path} (Conv: {resolved_conv_id})"
                    )
                    return recovered_content, resolved_conv_id

    except Exception as e:
        logger.error(f"Gagal membaca fallback transcript dari {transcript_path}: {e}")

    return None, resolved_conv_id


# ==============================================================================
# SUBPROCESS AGY CLI RUNNER
# ==============================================================================
async def run_agy_cli(
    user_id: int,
    prompt: str,
    conv_id: Optional[str] = None,
    cwd: str = WORKSPACE_DIR
) -> Tuple[str, Optional[str]]:
    """
    Mengeksekusi binary agy CLI sebagai asinkron subprocess.
    Menggunakan --output-format json untuk mengekstrak conversation_id resmi & respons teks.
    Dilengkapi timeout protection & fallback transcript recovery.
    Mengembalikan tuple: (response_text, new_or_existing_conv_id).
    """
    if not os.path.exists(AGY_BIN_PATH) and not shutil.which(AGY_BIN_PATH):
        raise FileNotFoundError(
            f"Binary agy tidak ditemukan di path: '{AGY_BIN_PATH}'. "
            f"Pastikan agy sudah terpasang atau sesuaikan variabel AGY_BIN_PATH di file .env."
        )

    cmd = [AGY_BIN_PATH]
    if conv_id:
        cmd.extend(["--conversation", conv_id])

    # Sisipkan instruksi sistem headless VPS pada prompt
    full_prompt = build_cli_prompt(prompt)

    cmd.extend([
        "-p", full_prompt,
        "--print-timeout", f"{AGY_TIMEOUT_SECONDS}s",
        "--dangerously-skip-permissions",
        "--output-format", "json"
    ])

    env = os.environ.copy()
    # Tambahkan path instalasi standar Antigravity CLI jika ada
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
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    user_processes[user_id] = proc
    stdout_bytes = b""
    stderr_bytes = b""
    timed_out = False

    try:
        # Berikan buffer toleransi 10 detik di atas print-timeout agar agy sempat menyelesaikan output formatting
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
        except (asyncio.TimeoutError, Exception):
            try:
                k_res = proc.kill()
                if asyncio.iscoroutine(k_res):
                    await k_res
            except Exception:
                pass
    finally:
        user_processes.pop(user_id, None)

    if timed_out:
        # Fallback Robust: Cek apakah model AI sebenarnya sudah menuliskan balasan di transcript.jsonl
        recovered_text, found_conv_id = recover_last_response_from_transcript(conv_id)
        effective_conv = found_conv_id or conv_id
        if recovered_text:
            return (
                f"{recovered_text}\n\n"
                f"⏱️ <i>(Catatan: Subprocess agy melebihi batas waktu {AGY_TIMEOUT_SECONDS} detik dan dihentikan, "
                f"namun jawaban berhasil dipulihkan dari log transkrip sistem.)</i>",
                effective_conv
            )

        return (
            f"⏱️ **Waktu eksekusi habis (Timeout {AGY_TIMEOUT_SECONDS} detik).**\n"
            f"Subprocess Antigravity telah dihentikan secara aman demi kestabilan sistem.\n\n"
            f"💡 *Jika tugas memerlukan waktu lebih lama, Anda dapat memperbesar nilai `AGY_TIMEOUT_SECONDS` di file `.env`.*",
            effective_conv
        )

    stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
    stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()

    # Coba parsing output sebagai JSON terstruktur
    parsed_json = None
    if stdout_text:
        # Beberapa output CLI mungkin memiliki warning sebelum baris JSON
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
        returned_conv_id = parsed_json.get("conversation_id") or conv_id
        response_body = parsed_json.get("response", "").strip()
        status = parsed_json.get("status", "")
        error_msg = parsed_json.get("error", "").strip()

        if status == "ERROR" and error_msg:
            return f"❌ **Error dari agy:**\n```text\n{error_msg}\n```", returned_conv_id

        if response_body:
            return response_body, returned_conv_id
        elif error_msg:
            return f"⚠️ **Output agy:**\n```text\n{error_msg}\n```", returned_conv_id

    # Fallback jika CLI menghasilkan output teks biasa
    if stdout_text:
        return stdout_text, conv_id
    elif stderr_text:
        return f"⚠️ Output (stderr):\n```text\n{stderr_text}\n```", conv_id

    return "(agy menyelesaikan tugas tanpa balasan output teks)", conv_id


# ==============================================================================
# MODEL QUOTA & USAGE UTILITIES
# ==============================================================================
def format_progress_bar(fraction: float, length: int = 10) -> str:
    """Membuat visual progress bar terminal-style: [████████░░] 78.5%"""
    fraction = max(0.0, min(1.0, fraction))
    filled = int(round(fraction * length))
    empty = length - filled
    return f"[{'█' * filled}{'░' * empty}] {fraction * 100:.1f}%"


def format_relative_time(reset_time_iso: str) -> str:
    """Mengubah timestamp ISO UTC menjadi format sisa waktu relatif yang ramah."""
    if not reset_time_iso:
        return ""
    try:
        clean_iso = reset_time_iso.replace("Z", "+00:00")
        target_dt = datetime.fromisoformat(clean_iso)
        now_utc = datetime.now(timezone.utc)
        diff = target_dt - now_utc

        total_seconds = int(diff.total_seconds())
        if total_seconds <= 0:
            return "Quota available"

        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60

        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0 and days == 0:
            parts.append(f"{minutes}m")

        time_str = " ".join(parts) if parts else "< 1m"
        return f"Refreshes in {time_str}"
    except Exception:
        return reset_time_iso


def format_usage_data(raw_output: str) -> str:
    """
    Memformat output JSON atau teks dari 'agy -p /usage' menjadi pesan Telegram HTML yang rapi.
    """
    if not raw_output or not raw_output.strip():
        return "⚠️ Tidak ada data kuota yang diterima dari Antigravity CLI."

    parsed = None
    for line in raw_output.splitlines():
        line_str = line.strip()
        if line_str.startswith("{") and line_str.endswith("}"):
            try:
                parsed = json.loads(line_str)
                break
            except Exception:
                continue

    if not parsed:
        try:
            parsed = json.loads(raw_output)
        except Exception:
            pass

    if parsed and isinstance(parsed, dict):
        cmd_data = parsed.get("command", {}).get("data", {})
        groups = cmd_data.get("groups", [])
        if groups:
            lines = [
                "📊 <b>Models &amp; Quota (Antigravity CLI)</b>",
                "━━━━━━━━━━━━━━━━━━━━"
            ]

            group_icons = {
                "gemini": "🤖",
                "claude": "🔮",
            }

            for group in groups:
                g_name = group.get("name", "Model Group")
                g_desc = group.get("description", "")

                icon = "✨"
                for k, ic in group_icons.items():
                    if k in g_name.lower():
                        icon = ic
                        break

                lines.append(f"\n{icon} <b>{html.escape(g_name.upper())}</b>")
                if g_desc:
                    lines.append(f"<i>{html.escape(g_desc)}</i>")

                buckets = group.get("buckets", [])
                for bucket in buckets:
                    b_name = bucket.get("name", "Limit")
                    fraction = float(bucket.get("remaining_fraction", 1.0))
                    reset_time = bucket.get("reset_time", "")

                    bar = format_progress_bar(fraction)
                    rel_time = format_relative_time(reset_time) if fraction < 0.999 else "Quota available"

                    status_icon = "🟢" if fraction > 0.5 else ("🟡" if fraction > 0.2 else "🔴")
                    time_icon = "⏱" if fraction < 0.999 else "✅"

                    lines.append(f"\n• {status_icon} <b>{html.escape(b_name)}</b>")
                    lines.append(f"  <code>{bar}</code>")
                    lines.append(f"  {time_icon} <i>{html.escape(rel_time)}</i>")

            lines.append("\n━━━━━━━━━━━━━━━━━━━━")
            lines.append("💡 <i>Model Flash mengonsumsi kuota lebih hemat dan memiliki limit lebih tinggi.</i>")
            return "\n".join(lines)

    # Fallback TSV atau teks biasa
    lines = [
        "📊 <b>Models &amp; Quota (Antigravity CLI)</b>",
        "━━━━━━━━━━━━━━━━━━━━"
    ]
    has_content = False
    for line in raw_output.splitlines():
        parts = [p.strip() for p in line.split("\t") if p.strip()]
        if len(parts) >= 3:
            grp = parts[0]
            limit_name = parts[1]
            pct = parts[2]
            extra = f" (Refreshes: {parts[3]})" if len(parts) >= 4 else ""
            lines.append(f"• <b>{html.escape(grp)}</b> - {html.escape(limit_name)}: <code>{html.escape(pct)}</code>{html.escape(extra)}")
            has_content = True
        elif line.strip() and not line.strip().startswith("{"):
            lines.append(html.escape(line.strip()))
            has_content = True

    if not has_content:
        return f"📊 <b>Output Kuota:</b>\n<pre>{html.escape(raw_output[:1000])}</pre>"

    return "\n".join(lines)


async def fetch_agy_usage_report() -> str:
    """
    Menjalankan 'agy -p "/usage" --output-format json' via subprocess
    dan memformat hasilnya menjadi laporan kuota Telegram yang rapi.
    """
    if not os.path.exists(AGY_BIN_PATH) and not shutil.which(AGY_BIN_PATH):
        return (
            "❌ <b>Binary agy tidak ditemukan di sistem!</b>\n"
            f"Path: <code>{html.escape(AGY_BIN_PATH)}</code>\n"
            "Pastikan binary agy sudah terpasang dan path diatur dengan benar di .env."
        )

    cmd = [
        AGY_BIN_PATH,
        "-p", "/usage",
        "--output-format", "json"
    ]

    env = os.environ.copy()
    extra_paths = [
        "/home/ubuntu/.gemini/antigravity-cli/bin",
        "/home/ubuntu/.local/bin",
        "/usr/local/bin"
    ]
    env["PATH"] = os.pathsep.join(extra_paths + [env.get("PATH", "")])

    logger.info("Mengambil data kuota model via 'agy -p /usage'...")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=WORKSPACE_DIR,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30.0)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return "⚠️ <b>Timeout:</b> Gagal mengambil data kuota dari agy CLI dalam 30 detik."
    except Exception as e:
        logger.error(f"Error menjalankan subprocess agy usage: {e}", exc_info=True)
        return f"❌ <b>Error eksekusi agy:</b>\n<code>{html.escape(str(e))}</code>"

    stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
    return format_usage_data(stdout_text)


def is_quota_inquiry(text: str) -> bool:
    """
    Mendeteksi apakah pesan pengguna menanyakan kuota/limit akun Antigravity,
    bukan instruksi koding (seperti 'SELECT * ... LIMIT 10' atau 'set rate limit').
    """
    clean = text.strip().lower()
    if not clean:
        return False

    # Perintah langsung
    if clean in ("/usage", "/limit", "usage", "limit", "kuota", "quota"):
        return True

    # Frasa langsung yang sering ditanyakan user
    direct_phrases = [
        "usage limit", "sisa limit", "sisa kuota", "kuota sisa", "limit sisa",
        "cek kuota", "cek limit", "cek usage", "status kuota", "status limit",
        "kuota agy", "limit agy", "limit akun", "kuota akun", "quota limit",
        "remaining quota", "remaining limit", "quota remaining", "usage remaining",
        "kuota model", "limit model"
    ]
    for phrase in direct_phrases:
        if phrase in clean:
            # Pastikan bukan query database seperti SELECT ... LIMIT
            if not re.search(r"\bselect\b.*\blimit\b", clean):
                return True

    # Pola kombinasi: kata tanya/cek/sisa + kata kuota/limit/usage
    has_inquiry_word = bool(re.search(r"\b(sisa|berapa|cek|check|info|status|lihat|tampilkan|ada|habis|kurang)\b", clean))
    has_quota_word = bool(re.search(r"\b(kuota|quota|limit|usage)\b", clean))

    # Abaikan jika ada indikasi instruksi pemrograman teknis
    is_coding = bool(re.search(
        r"\b(sql|query|table|database|mysql|postgres|select|css|div|width|height|rate[\s_-]?limit|pagination|offset)\b",
        clean
    ))

    if has_inquiry_word and has_quota_word and not is_coding:
        return True

    return False

# ==============================================================================
# TELEGRAM COMMAND HANDLERS
# ==============================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("⛔ Akses ditolak. Bot ini privat untuk pemilik sistem.")
        return

    user_id = update.effective_user.id
    current_conv = user_conversations.get(user_id, "Belum dimulai (Akan dibuat saat pesan pertama)")

    welcome_text = (
        "🤖 **Halo Kang! Antigravity Telegram Bot Aktif.**\n\n"
        "Bot ini terhubung langsung ke **Native `agy` CLI Engine** di VPS/Host dengan sesi login Google Antigravity Akang.\n"
        "Dilengkapi fitur **Hermes Guard (Interactive Approval)** untuk mencegah eksekusi instruksi katastropik.\n\n"
        "**Perintah Tersedia:**\n"
        "• `/usage`  - Cek kuota & sisa limit model (Gemini, Claude, GPT)\n"
        "• `/status` - Cek engine, memory ID, workspace, dan status proses\n"
        "• `/cancel` - Hentikan paksa proses `agy` yang sedang berjalan\n"
        "• `/reset`  - Hapus memori percakapan & mulai sesi baru\n"
        "• `/help`   - Panduan lengkap fitur & media transfer\n\n"
        "Silakan kirim pesan atau instruksi koding/perintah apa pun langsung di sini."
    )
    await safe_send_message(context.bot, update.effective_chat.id, welcome_text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    help_text = (
        "📖 **Panduan Penggunaan Antigravity Telegram Bot**\n\n"
        "**1. Perintah Bot:**\n"
        "• `/usage`  : Menampilkan sisa kuota dan waktu refresh limit model secara real-time.\n"
        "• `/status` : Informasi engine, binary path, memori multi-turn, dan PID proses.\n"
        "• `/cancel` : Mematikan proses `agy` yang sedang berjalan secara instan (`SIGTERM`/`SIGKILL`).\n"
        "• `/reset`  : Menghapus `conversation_id` dan memulai sesi baru yang segar.\n\n"
        "**2. Keamanan & Approval (Ala Hermes):**\n"
        "• Perintah berisiko tinggi (hapus database, drop table, rm -rf, git push force) akan memunculkan tombol "
        "`[ ✅ Approve ]` dan `[ ❌ Deny ]`.\n"
        "• Batas waktu approval adalah 120 detik (otomatis dibatalkan jika tidak direspons).\n"
        "• Perintah katastropik OS (`rm -rf /`, `mkfs`, `dd`, `shutdown`) **DIBLOKIR TOTAL** tanpa konfirmasi.\n\n"
        "**3. Pengiriman Berkas & Media:**\n"
        "Jika hasil pekerjaan menghasilkan berkas gambar/dokumen, bot akan otomatis mengirimkannya ke chat Telegram pengguna."
    )
    await safe_send_message(context.bot, update.effective_chat.id, help_text)

async def usage_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk perintah /usage dan /limit."""
    if not is_authorized(update):
        return

    chat_id = update.effective_chat.id
    status_msg = await safe_send_message(
        context.bot,
        chat_id,
        "⏳ *Mengambil data kuota model dari Antigravity CLI...*",
        parse_mode=ParseMode.MARKDOWN
    )

    try:
        report_html = await fetch_agy_usage_report()
        if status_msg:
            await safe_edit_message(status_msg, report_html, parse_mode=ParseMode.HTML)
        else:
            await safe_send_message(context.bot, chat_id, report_html, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error handling /usage: {e}", exc_info=True)
        err_msg = f"❌ Gagal mengambil data kuota:\n<code>{html.escape(str(e))}</code>"
        if status_msg:
            await safe_edit_message(status_msg, err_msg, parse_mode=ParseMode.HTML)
        else:
            await safe_send_message(context.bot, chat_id, err_msg, parse_mode=ParseMode.HTML)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    user_id = update.effective_user.id
    current_conv = user_conversations.get(user_id)
    running_proc = user_processes.get(user_id)
    is_proc_running = running_proc is not None and running_proc.returncode is None
    bin_exists = os.path.exists(AGY_BIN_PATH) or shutil.which(AGY_BIN_PATH) is not None

    status_text = (
        "📊 **Status Sistem Antigravity Bot (CLI Engine)**\n\n"
        f"• **Engine**: `Native agy CLI Subprocess`\n"
        f"• **Binary Path**: `{AGY_BIN_PATH}` ({'✅ Ditemukan' if bin_exists else '❌ Tidak Ditemukan!'})\n"
        f"• **Workspace Path**: `{WORKSPACE_DIR}`\n"
        f"• **Approval Mode**: `{APPROVAL_MODE}`\n"
        f"• **Approval Timeout**: `{APPROVAL_TIMEOUT_SECONDS} detik`\n"
        f"• **Execution Timeout**: `{AGY_TIMEOUT_SECONDS} detik`\n"
        f"• **Sesi Percakapan**: `{current_conv if current_conv else 'Belum ada (Fresh)'}`\n"
        f"• **Status Tugas Saat Ini**: `{'⏳ Sedang Berjalan (PID: ' + str(running_proc.pid) + ')' if is_proc_running else '💤 Idle'}`\n"
        f"• **Whitelist User ID**: `{user_id}` (Terverifikasi)\n"
        f"• **Pending Approvals**: `{len(pending_approvals)}`"
    )
    await safe_send_message(context.bot, update.effective_chat.id, status_text)

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    user_id = update.effective_user.id

    # 1. Hentikan proses yang sedang berjalan jika ada
    proc = user_processes.get(user_id)
    if proc and proc.returncode is None:
        try:
            proc.terminate()
        except Exception:
            pass

    task = user_tasks.get(user_id)
    if task and not task.done():
        task.cancel()

    # 2. Hapus memori percakapan
    old_conv = user_conversations.pop(user_id, None)

    # 3. Batalkan approval yang tertunda
    for req_id, info in list(pending_approvals.items()):
        if info.get("user_id") == user_id:
            fut = info.get("future")
            if fut and not fut.done():
                fut.cancel()

    await safe_send_message(
        context.bot,
        update.effective_chat.id,
        "🔄 **Sesi Percakapan Direset!**\n"
        f"Riwayat percakapan lama ({old_conv[:8] + '...' if old_conv else 'None'}) telah dibersihkan. "
        "Instruksi berikutnya akan memulai percakapan baru di `agy`."
    )

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    user_id = update.effective_user.id
    cancelled_anything = False

    # 1. Matikan subprocess agy aktif
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

    # 2. Batalkan async task
    task = user_tasks.get(user_id)
    if task and not task.done():
        task.cancel()
        cancelled_anything = True

    # 3. Batalkan pending approvals
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
            update.effective_chat.id,
            "🛑 **Tugas berhasil dibatalkan!** Subprocess `agy` dan eksekusi telah dihentikan."
        )
    else:
        await safe_send_message(
            context.bot,
            update.effective_chat.id,
            "ℹ️ Tidak ada tugas atau proses `agy` yang sedang berjalan saat ini."
        )

# ==============================================================================
# AGENT WORKFLOW & MESSAGE PROCESSING
# ==============================================================================
async def execute_agent_turn(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_text: str
):
    """
    Eksekusi satu putaran instruksi ke agy CLI subprocess.
    Dijalankan di dalam user mutex lock dan terlindungi oleh /cancel.
    """
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    reply_id = update.message.message_id if update.message else None

    # 1. Evaluasi Hardline Security Blocklist
    if is_hardline_blocked(user_text):
        logger.error(f"🚨 HARDLINE SECURITY BLOCKLIST TRIGGERED: {user_text}")
        await safe_send_message(
            bot=context.bot,
            chat_id=chat_id,
            text=(
                f"🚨 **HARDLINE SECURITY BLOCKLIST TRIGGERED!**\n\n"
                f"Instruksi berikut terdeteksi berisiko katastropik dan **DIBLOKIR TOTAL** demi integritas sistem:\n"
                f"```bash\n{user_text}\n```"
            ),
            reply_to_message_id=reply_id
        )
        return

    # 2. Evaluasi Hermes Interactive Approval (Intent Guard)
    is_destruct, reason = is_destructive_prompt(user_text)
    if is_destruct:
        approved = await request_user_approval(
            bot=context.bot,
            chat_id=chat_id,
            user_id=user_id,
            prompt=user_text,
            reason=reason
        )
        if not approved:
            await safe_send_message(
                context.bot,
                chat_id,
                "❌ **Instruksi Ditolak.** Eksekusi tidak dijalankan.",
                reply_to_message_id=reply_id
            )
            return

    # 3. Jalankan melalui agy CLI Subprocess (Silent status notification & quote reply anchor)
    status_msg = await safe_send_message(
        context.bot,
        chat_id,
        "⏳ *Antigravity sedang berpikir & memproses...*",
        parse_mode=ParseMode.MARKDOWN,
        disable_notification=True,
        reply_to_message_id=reply_id
    )

    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(
        send_typing_and_progress(context.bot, chat_id, status_msg, stop_typing)
    )

    try:
        active_conv = user_conversations.get(user_id)
        output_text, new_conv_id = await run_agy_cli(
            user_id=user_id,
            prompt=user_text,
            conv_id=active_conv,
            cwd=WORKSPACE_DIR
        )

        # Simpan atau perbarui conversation_id untuk memori multi-turn
        if new_conv_id:
            user_conversations[user_id] = new_conv_id

        # 4. Deteksi dan kirim berkas media jika ada
        media_paths = extract_media_paths(output_text, workspace_dir=WORKSPACE_DIR)

        # 5. Format teks output menggunakan konverter Telegram HTML yang rapi & aman
        formatted_html = markdown_to_telegram_html(output_text)

        # 6. Potong teks agar muat di batas limit Telegram (4000 char)
        chunks = split_message(formatted_html, max_length=4000)

        for i, chunk in enumerate(chunks):
            if i == 0 and status_msg:
                await safe_edit_message(status_msg, chunk, parse_mode=ParseMode.HTML)
            else:
                await safe_send_message(
                    context.bot,
                    chat_id,
                    chunk,
                    parse_mode=ParseMode.HTML,
                    reply_to_message_id=reply_id if i == 0 else None
                )

        # Kirim file media yang terdeteksi
        for file_path in media_paths:
            try:
                file_name = os.path.basename(file_path)
                with open(file_path, "rb") as doc_file:
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=doc_file,
                        caption=f"📄 Berkas: `{file_name}`",
                        parse_mode=ParseMode.MARKDOWN
                    )
                logger.info(f"Berhasil mengirim dokumen Telegram: {file_path}")
            except Exception as e:
                logger.error(f"Gagal mengirim berkas {file_path} via Telegram: {e}")
                await safe_send_message(
                    context.bot,
                    chat_id,
                    f"⚠️ Gagal mengirim berkas `{os.path.basename(file_path)}`: {str(e)}"
                )

    except asyncio.CancelledError:
        logger.info(f"Task user {user_id} dibatalkan.")
        if status_msg:
            await safe_edit_message(status_msg, "🛑 *Tugas dibatalkan oleh pengguna via /cancel.*")
        raise
    except Exception as e:
        logger.error(f"Error saat mengeksekusi agy: {e}", exc_info=True)
        err_msg = (
            f"❌ **Terjadi kesalahan saat memproses permintaan:**\n"
            f"```text\n{str(e)[:1000]}\n```\n\n"
            f"💡 *Petunjuk:* Pastikan binary `agy` terpasang di path yang sesuai atau gunakan `/reset` untuk me-restart sesi."
        )
        if status_msg:
            await safe_edit_message(status_msg, err_msg)
        else:
            await safe_send_message(context.bot, chat_id, err_msg, reply_to_message_id=reply_id)
    finally:
        stop_typing.set()
        typing_task.cancel()

async def _dispatch_agent_turn(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    prompt: str
):
    """
    Helper untuk mengeksekusi prompt ke agy CLI subprocess
    dalam kendali mutex lock dan pelacakan task asinkron per pengguna.
    """
    user_id = update.effective_user.id

    # Cek apakah ada tugas yang masih berjalan untuk user ini
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


async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk gambar / screenshot yang dikirimkan oleh pengguna."""
    if not is_authorized(update):
        logger.warning(
            f"Unauthorized photo upload attempt from User ID: "
            f"{update.effective_user.id if update.effective_user else 'Unknown'}"
        )
        return

    if not update.message or not update.message.photo:
        return

    user_id = update.effective_user.id
    # Ambil foto dengan resolusi tertinggi (elemen terakhir dalam daftar photo)
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
            await update.message.reply_text(f"❌ Gagal mengunduh foto: {e}")
        return

    caption = (update.message.caption or "").strip()
    caption_prompt = caption if caption else "Tolong periksa dan analisis gambar terlampir ini."

    prompt_text = (
        f"[PENGGUNA MENGIRIMKAN GAMBAR / SCREENSHOT]\n"
        f"Berkas gambar telah disimpan di: {dest_path}\n\n"
        f"[INSTRUKSI / CAPTION PENGGUNA]:\n"
        f"{caption_prompt}"
    )

    await _dispatch_agent_turn(update, context, prompt_text)


async def handle_document_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk berkas dokumen / kode / log yang dikirimkan oleh pengguna."""
    if not is_authorized(update):
        logger.warning(
            f"Unauthorized document upload attempt from User ID: "
            f"{update.effective_user.id if update.effective_user else 'Unknown'}"
        )
        return

    if not update.message or not update.message.document:
        return

    user_id = update.effective_user.id
    doc = update.message.document

    try:
        file_obj = await doc.get_file()
        orig_name = doc.file_name or f"doc_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        safe_name = Path(orig_name).name
        if not safe_name:
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
            await update.message.reply_text(f"❌ Gagal mengunduh dokumen: {e}")
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

    await _dispatch_agent_turn(update, context, prompt_text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Router utama untuk pesan teks yang masuk dari pengguna."""
    if not is_authorized(update):
        logger.warning(
            f"Unauthorized access attempt from User ID: "
            f"{update.effective_user.id if update.effective_user else 'Unknown'}"
        )
        return

    user_id = update.effective_user.id
    user_text = update.message.text if update.message else ""

    if not user_text.strip():
        return

    # Deteksi Intent Cek Kuota / Limit Akun (Smart Interceptor)
    if is_quota_inquiry(user_text):
        logger.info(f"User {user_id} menanyakan kuota/limit via pesan: '{user_text}'")
        status_msg = await safe_send_message(
            context.bot,
            update.effective_chat.id,
            "⏳ *Mengambil data kuota model dari Antigravity CLI...*",
            parse_mode=ParseMode.MARKDOWN
        )
        try:
            report_html = await fetch_agy_usage_report()
            if status_msg:
                await safe_edit_message(status_msg, report_html, parse_mode=ParseMode.HTML)
            else:
                await safe_send_message(context.bot, update.effective_chat.id, report_html, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Error saat mengambil data kuota untuk user {user_id}: {e}", exc_info=True)
            err_msg = f"❌ Gagal mengambil data kuota:\n<code>{html.escape(str(e))}</code>"
            if status_msg:
                await safe_edit_message(status_msg, err_msg, parse_mode=ParseMode.HTML)
            else:
                await safe_send_message(context.bot, update.effective_chat.id, err_msg, parse_mode=ParseMode.HTML)
        return

    await _dispatch_agent_turn(update, context, user_text)

# ==============================================================================
# MAIN APPLICATION ENTRYPOINT & LIFECYCLE HOOKS
# ==============================================================================
async def post_init(application):
    """Mendaftarkan menu perintah bot secara otomatis ke Telegram API saat startup."""
    commands = [
        BotCommand("usage", "📊 Cek kuota model & sisa limit"),
        BotCommand("status", "ℹ️ Status engine, PID, & memori"),
        BotCommand("cancel", "🛑 Hentikan tugas aktif seketika"),
        BotCommand("reset", "🔄 Mulai sesi percakapan baru"),
        BotCommand("help", "📖 Panduan bantuan & perintah"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("✓ BotCommand menu resmi berhasil didaftarkan ke Telegram API.")
    except Exception as e:
        logger.warning(f"Gagal mendaftarkan bot commands: {e}")

async def post_shutdown(application):
    """Membersihkan seluruh subprocess agy yang masih berjalan saat bot dimatikan."""
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
        logger.warning("⚠️ ALLOWED_USER_ID belum diatur atau bernilai 0. Tidak ada pengguna yang dapat mengakses bot!")

    logger.info(f"🤖 Antigravity Engine : Subprocess agy CLI")
    logger.info(f"📂 Binary Path        : {AGY_BIN_PATH}")
    logger.info(f"🛡️ Whitelist User IDs : {ALLOWED_USER_IDS}")
    logger.info(f"⚙️ Approval Mode      : {APPROVAL_MODE} (Timeout: {APPROVAL_TIMEOUT_SECONDS}s)")
    logger.info(f"📁 Workspace Path     : {WORKSPACE_DIR}")

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Daftarkan command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler(["usage", "limit"], usage_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("cancel", cancel_command))

    # Daftarkan handler tombol konfirmasi Approve / Deny
    app.add_handler(CallbackQueryHandler(handle_callback_query))

    # Daftarkan handler pesan media & dokumen
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_message))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document_message))

    # Daftarkan handler pesan teks
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🚀 Antigravity CLI Telegram Bot siap berjalan...")
    app.run_polling()

if __name__ == "__main__":
    main()
