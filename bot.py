#!/usr/bin/env python3
"""
Antigravity Telegram Bot (Production-Grade)
Inspired by NousResearch/hermes-agent & plan.md architecture.

Features:
- Whitelist authorization check (ALLOWED_USER_ID)
- Multi-turn stateful session memory per user
- Dangerous Command Approval System (Hardline Blocklist + Inline Keyboard + Fail-closed Timeout)
- Task interrupt & cancellation via /cancel
- Native file/media delivery (MEDIA:/path detection and Telegram send_document)
- Live ephemeral progress updates & periodic typing indicator
- Safe message chunking (< 4000 chars) with markdown parsing fallback
- Command handlers: /start, /help, /status, /reset, /cancel
"""

import os
import sys
import re
import asyncio
import logging
import uuid
from pathlib import Path
from typing import Dict, Optional, Set, List, Tuple, Any

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.constants import ChatAction, ParseMode
from telegram.error import TelegramError, BadRequest
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
)

from google.antigravity import Agent, LocalAgentConfig, CapabilitiesConfig
from google.antigravity.hooks.hooks import PreToolCallDecideHook, HookResult, HookContext
from google.antigravity.hooks.policy import Policy, Decision
from google.antigravity.types import ToolCall, Text, ToolResult

load_dotenv()

# ==============================================================================
# LOGGING SETUP
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("antigravity-bot")

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

WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "/workspace").strip()
APPROVAL_MODE = os.getenv("APPROVAL_MODE", "ask_destructive").strip().lower()
APPROVAL_TIMEOUT_SECONDS = int(os.getenv("APPROVAL_TIMEOUT_SECONDS", "120"))

SYSTEM_INSTRUCTIONS = (
    "Anda adalah asisten AI Antigravity berstandar produksi yang melayani pemilik sistem di VPS/Server. "
    f"Direktori kerja utama adalah: {WORKSPACE_DIR}. "
    "Bantu jawab pertanyaan arsitektur, navigasi codebase, inspeksi berkas, "
    "edit kode, atau jalankan perintah dengan aman dan teliti.\n\n"
    "PANDUAN PENTING:\n"
    "1. Berkomunikasilah dalam Bahasa Indonesia yang ramah, sopan, ringkas, dan profesional (friendly yet technical).\n"
    "2. Jika Anda membuat, mengubah, atau menemukan file yang ingin Anda kirimkan langsung kepada pengguna sebagai dokumen Telegram, "
    "sertakan baris terpisah dengan format:\n"
    "MEDIA:/path/ke/file\n"
    "Sistem bot akan secara otomatis mengunggah dan mengirimkan dokumen tersebut ke chat Telegram pengguna.\n"
    "3. Selalu periksa kembali perubahan kode sebelum mengeksekusi."
)

# ==============================================================================
# HARDLINE SECURITY BLOCKLIST
# ==============================================================================
# Perintah-perintah katastropik berikut DILARANG KERAS dieksekusi dalam kondisi apa pun!
# Jika terdeteksi, bot akan langsung MENOLAK tanpa memunculkan tombol Approve.
HARDLINE_BLOCKLIST = [
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
    r"mkfs(?:\.[a-z0-9]+)?\s+",
    r"dd\s+if=.*of=/dev/(?:sd[a-z]|nvme[0-9]|hd[a-z]|vd[a-z]|mmcblk)",
    r"fdisk\s+/dev/",
    r">\s*/dev/(?:sd[a-z]|nvme[0-9]|hd[a-z]|vd[a-z])",
    r"chmod\s+-[rR]\s+777\s+/(?:\s|$)",
    r"chown\s+-[rR]\s+.*\s+/(?:\s|$)",
    r"(?:^|[\s;&|])(?:shutdown|reboot|poweroff|halt|init\s+0)(?:$|[\s;&|])",
]

def is_hardline_blocked(command: str) -> bool:
    """Mengecek apakah perintah terminal melanggar Hardline Blocklist."""
    cmd_clean = command.strip()

    # Cek penghapusan rekursif pada root / atau /* (contoh: rm -rf /, rm -r -f /, rm --recursive /)
    if re.search(r"\brm\s+", cmd_clean, re.IGNORECASE):
        if re.search(r"(?:^|\s)/(?:\*|\s|$)", cmd_clean):
            if re.search(r"-[a-zA-Z0-9]*[rR]|--recursive", cmd_clean):
                return True

    for pattern in HARDLINE_BLOCKLIST:
        if re.search(pattern, cmd_clean, re.IGNORECASE):
            return True
    return False

def is_command_safe(cmd: str) -> bool:
    """
    Mengevaluasi apakah perintah terminal termasuk read-only yang aman di-auto-approve.
    Jika ada redirection (>) atau sub-command yang bukan read-only, harus minta persetujuan.
    """
    if ">" in cmd:
        return False

    safe_prefixes = (
        "ls", "dir", "pwd", "git status", "git log", "git diff", "git branch", "git show",
        "cat", "head", "tail", "grep", "find", "which", "where", "python --version",
        "python -v", "node -v", "npm -v", "echo", "whoami", "date", "uname", "ps", "df", "du", "free"
    )

    parts = cmd.replace("&&", ";").replace("||", ";").replace("|", ";").split(";")
    for part in parts:
        clean = part.strip().lower()
        if not clean:
            continue
        if not any(clean.startswith(prefix) for prefix in safe_prefixes):
            return False
    return True

def is_destructive_action(tool_name: str, args: dict) -> Tuple[bool, str]:
    """
    Mengklasifikasikan apakah aksi tool membutuhkan persetujuan interaktif (Approve/Deny).
    Returns (is_destructive, details_string).
    """
    tool_lower = tool_name.lower()

    # Perintah terminal
    if tool_lower in ["run_command", "terminal", "execute", "execute_command"]:
        cmd = str(args.get("command", "") or args.get("CommandLine", "") or args.get("cmd", "")).strip()
        details = f"Command: {cmd}"
        if APPROVAL_MODE == "ask_all":
            return True, details
        if APPROVAL_MODE == "auto_approve":
            return False, details
        # ask_destructive:
        if is_command_safe(cmd):
            return False, details
        return True, details

    # Modifikasi file
    if tool_lower in ["create_file", "write_to_file", "write_file", "edit_file", "replace_file_content", "delete_file"]:
        target = args.get("TargetFile") or args.get("file_path") or args.get("path") or "(file)"
        details = f"Tool: {tool_name}\nTarget: {target}"
        if APPROVAL_MODE == "auto_approve":
            return False, details
        return True, details

    # Tool lainnya
    if APPROVAL_MODE == "ask_all":
        return True, f"Tool: {tool_name}\nArgs: {str(args)[:300]}"

    return False, f"Tool: {tool_name}"

# ==============================================================================
# STATE & REGISTRY
# ==============================================================================
# Sesi percakapan Antigravity per user
user_agents: Dict[int, Agent] = {}
user_approval_hooks: Dict[int, "TelegramApprovalHook"] = {}
user_locks: Dict[int, asyncio.Lock] = {}
user_tasks: Dict[int, asyncio.Task] = {}

# Pending approval registry:
# { request_id: {"future": asyncio.Future, "user_id": int, "message": Message, "text": str} }
pending_approvals: Dict[str, dict] = {}

# ==============================================================================
# TELEGRAM HELPER UTILITIES
# ==============================================================================
def is_authorized(update: Update) -> bool:
    """Verifikasi apakah pengguna ada di whitelist ALLOWED_USER_IDS."""
    user = update.effective_user
    if user is None:
        return False
    return user.id in ALLOWED_USER_IDS

def get_user_lock(user_id: int) -> asyncio.Lock:
    """Mendapatkan mutex lock per user untuk mencegah race condition."""
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]

def split_message(text: str, max_length: int = 4000) -> List[str]:
    """
    Memecah teks panjang secara aman agar tidak melampaui limit karakter Telegram (4096).
    Memotong pada boundary baris baru (\n) atau spasi jika memungkinkan.
    """
    if not text:
        return ["(Tidak ada output)"]
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

async def safe_send_message(
    bot,
    chat_id: int,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: Optional[str] = ParseMode.MARKDOWN
) -> Optional[Message]:
    """
    Mengirim pesan Telegram dengan fallback otomatis ke teks polos jika parsing markdown gagal.
    """
    try:
        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    except BadRequest as e:
        logger.warning(f"Markdown parse error saat send_message ({e}). Mengirim ulang sebagai plain text.")
        try:
            return await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=None
            )
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
    parse_mode: Optional[str] = ParseMode.MARKDOWN
) -> bool:
    """
    Mengedit pesan Telegram dengan fallback otomatis ke plain text jika markdown invalid.
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
        logger.warning(f"Markdown parse error saat edit_message ({e}). Mengedit ulang sebagai plain text.")
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

async def send_typing_periodically(bot, chat_id: int, stop_event: asyncio.Event):
    """Mengirim status 'typing...' setiap 4 detik selama agen sedang memproses."""
    while not stop_event.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=4.0)
        except asyncio.TimeoutError:
            pass

# ==============================================================================
# APPROVAL WORKFLOW (ALA HERMES)
# ==============================================================================
async def request_user_approval(
    bot,
    chat_id: int,
    user_id: int,
    action_name: str,
    action_details: str
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
        "⚠️ **Permintaan Persetujuan Eksekusi (Ala Hermes):**\n\n"
        f"• **Aksi**: `{action_name}`\n"
        f"• **Detail**:\n```bash\n{action_details[:1000]}\n```\n"
        f"⏱️ *Batas Waktu:* `{APPROVAL_TIMEOUT_SECONDS} detik (Fail-Closed)`"
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
                f"{pesan_approval}\n\nStatus: 🛑 **DIBATALKAN (CANCELLED)**"
            )
        return False
    finally:
        pending_approvals.pop(request_id, None)

class TelegramApprovalHook(PreToolCallDecideHook):
    """
    Hook bawaan Antigravity yang mencegat eksekusi tool sebelum dijalankan.
    Menerapkan Hardline Blocklist & Interactive Approval langsung ke Telegram.
    """
    def __init__(self, bot, chat_id: int, user_id: int):
        super().__init__()
        self.bot = bot
        self.chat_id = chat_id
        self.user_id = user_id
        self.status_msg: Optional[Message] = None

    def update_context(self, bot, chat_id: int, user_id: int, status_msg: Optional[Message] = None):
        self.bot = bot
        self.chat_id = chat_id
        self.user_id = user_id
        self.status_msg = status_msg

    async def run(self, context: HookContext, data: ToolCall) -> HookResult:
        tool_name = data.name
        args = data.args or {}

        # 1. Evaluasi Hardline Blocklist jika tool adalah eksekusi perintah terminal
        if tool_name.lower() in ["run_command", "terminal", "execute", "execute_command"]:
            cmd = str(args.get("command", "") or args.get("CommandLine", "") or args.get("cmd", ""))
            if is_hardline_blocked(cmd):
                logger.error(f"🚨 HARDLINE BLOCKLIST TRIGGERED: {cmd}")
                await safe_send_message(
                    bot=self.bot,
                    chat_id=self.chat_id,
                    text=(
                        f"🚨 **HARDLINE SECURITY BLOCKLIST TRIGGERED!**\n\n"
                        f"Perintah berikut terdeteksi berisiko katastropik dan **DIBLOKIR TOTAL** demi keamanan:\n"
                        f"```bash\n{cmd}\n```"
                    )
                )
                return HookResult(
                    allow=False,
                    message="DIBLOKIR: Perintah melanggar Hardline Security Blocklist dan tidak dapat dijalankan."
                )

        # 2. Cek apakah aksi memerlukan persetujuan interaktif (destructive)
        is_destruct, details = is_destructive_action(tool_name, args)
        if not is_destruct:
            if self.status_msg:
                await safe_edit_message(self.status_msg, f"⚙️ *Menjalankan tool:* `{tool_name}`...")
            return HookResult(allow=True)

        # 3. Minta persetujuan interaktif ke user Telegram
        approved = await request_user_approval(
            bot=self.bot,
            chat_id=self.chat_id,
            user_id=self.user_id,
            action_name=tool_name,
            action_details=details
        )

        if approved:
            if self.status_msg:
                await safe_edit_message(self.status_msg, f"⚙️ *Mengeksekusi (Disetujui):* `{tool_name}`...")
            return HookResult(allow=True)
        else:
            return HookResult(
                allow=False,
                message="User menolak eksekusi aksi ini atau batas waktu persetujuan telah habis."
            )

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
# TELEGRAM COMMAND HANDLERS
# ==============================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("⛔ Akses ditolak. Bot ini privat untuk pemilik sistem.")
        return

    welcome_text = (
        "🤖 **Halo Kang! Antigravity Telegram Bot Aktif.**\n\n"
        "Bot ini terhubung langsung dengan AI Agent Antigravity di lingkungan VPS/Docker.\n"
        "Dilengkapi fitur **Interactive Approval (ala Hermes)** untuk menjamin keamanan eksekusi perintah kritis.\n\n"
        "**Perintah Tersedia:**\n"
        "• `/status` - Cek status bot, memory, approval mode, dan workspace\n"
        "• `/cancel` - Batalkan tugas/eksekusi yang sedang berjalan\n"
        "• `/reset`  - Hapus memori percakapan & mulai sesi baru\n"
        "• `/help`   - Panduan lengkap fitur & media transfer\n\n"
        "Silakan kirim pesan atau instruksi koding/perintah apa pun langsung di sini."
    )
    await safe_send_message(context.bot, update.effective_chat.id, welcome_text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    help_text = (
        "📖 **Panduan Penggunaan Antigravity Bot**\n\n"
        "1. **Tanya Jawab & Diskusi**:\n"
        "   Kirim pertanyaan arsitektur, algoritma, atau konsultasi koding.\n\n"
        "2. **Kelola Proyek & File**:\n"
        f"   Agen dapat membaca, menulis, dan mengedit file di `{WORKSPACE_DIR}`.\n\n"
        "3. **Interactive Approval (Ala Hermes)**:\n"
        "   Saat agen hendak menjalankan perintah mutatif (bash, git push, edit berkas), "
        "bot akan menampilkan tombol konfirmasi:\n"
        "   `[ ✅ Setujui (Approve) ]`  `[ ❌ Tolak (Deny) ]`\n"
        f"   Batas waktu tunggu: `{APPROVAL_TIMEOUT_SECONDS} detik (fail-closed)`.\n\n"
        "4. **Pengiriman File & Media Otomatis**:\n"
        "   Minta agen membuat file atau laporan. Jika agen menyertakan `MEDIA:/path/ke/file`, "
        "bot akan langsung mengirimkannya sebagai dokumen Telegram.\n\n"
        "5. **Membatalkan Tugas**:\n"
        "   Gunakan `/cancel` kapan saja untuk menghentikan proses yang sedang berjalan."
    )
    await safe_send_message(context.bot, update.effective_chat.id, help_text)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    user_id = update.effective_user.id
    has_active_agent = user_id in user_agents
    current_task = user_tasks.get(user_id)
    is_task_running = current_task is not None and not current_task.done()

    status_text = (
        "📊 **Status Sistem Antigravity Bot**\n\n"
        f"• **Environment**: `Docker Container / Linux VPS`\n"
        f"• **Workspace Path**: `{WORKSPACE_DIR}`\n"
        f"• **Approval Mode**: `{APPROVAL_MODE}`\n"
        f"• **Approval Timeout**: `{APPROVAL_TIMEOUT_SECONDS} detik`\n"
        f"• **Sesi Percakapan**: `{'Aktif (Multi-turn Memory)' if has_active_agent else 'Fresh / Idle'}`\n"
        f"• **Status Tugas Saat Ini**: `{'⏳ Sedang Berjalan' if is_task_running else '💤 Idle'}`\n"
        f"• **Whitelist User ID**: `{user_id}` (Terverifikasi)\n"
        f"• **Pending Approvals**: `{len(pending_approvals)}`"
    )
    await safe_send_message(context.bot, update.effective_chat.id, status_text)

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    user_id = update.effective_user.id

    # 1. Batalkan tugas yang sedang berjalan jika ada
    task = user_tasks.get(user_id)
    if task and not task.done():
        task.cancel()

    # 2. Hentikan dan bersihkan sesi Antigravity Agent
    agent = user_agents.pop(user_id, None)
    if agent:
        try:
            await agent.__aexit__(None, None, None)
        except Exception as e:
            logger.warning(f"Error saat menutup sesi agent pada /reset: {e}")

    user_approval_hooks.pop(user_id, None)

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
        "Konteks obrolan dan riwayat sesi telah dibersihkan. Agen siap untuk instruksi baru."
    )

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    user_id = update.effective_user.id
    task = user_tasks.get(user_id)

    cancelled_anything = False

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
            update.effective_chat.id,
            "🛑 **Tugas berhasil dibatalkan!** Eksekusi telah dihentikan."
        )
    else:
        await safe_send_message(
            context.bot,
            update.effective_chat.id,
            "ℹ️ Tidak ada tugas atau perintah yang sedang berjalan saat ini."
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
    Eksekusi satu putaran percakapan dengan Antigravity Agent.
    Dijalankan sebagai asyncio.Task agar dapat di-cancel via /cancel.
    """
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    status_msg = await safe_send_message(
        context.bot,
        chat_id,
        "⏳ *Memproses instruksi ke Antigravity...*"
    )

    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(send_typing_periodically(context.bot, chat_id, stop_typing))

    try:
        # Inisialisasi atau update Approval Hook untuk sesi ini
        if user_id not in user_approval_hooks:
            hook = TelegramApprovalHook(bot=context.bot, chat_id=chat_id, user_id=user_id)
            user_approval_hooks[user_id] = hook
        else:
            hook = user_approval_hooks[user_id]
            hook.update_context(bot=context.bot, chat_id=chat_id, user_id=user_id, status_msg=status_msg)

        # Inisialisasi Agent jika belum ada (Stateful multi-turn memory)
        if user_id not in user_agents:
            agent_config = LocalAgentConfig(
                system_instructions=SYSTEM_INSTRUCTIONS,
                capabilities=CapabilitiesConfig(),
                workspaces=[WORKSPACE_DIR],
                policies=[Policy(tool="*", decision=Decision.APPROVE)],
                hooks=[hook]
            )
            new_agent = Agent(agent_config)
            await new_agent.__aenter__()
            user_agents[user_id] = new_agent

        agent = user_agents[user_id]
        hook.update_context(bot=context.bot, chat_id=chat_id, user_id=user_id, status_msg=status_msg)

        # Kirim prompt ke Antigravity Agent
        response = await agent.chat(user_text)

        # Kumpulkan teks sambil memberikan pembaruan live jika ada ToolCall
        output_text = ""
        async for chunk in response.chunks:
            if isinstance(chunk, ToolCall):
                if status_msg:
                    await safe_edit_message(status_msg, f"⚙️ *Sedang menjalankan:* `{chunk.name}`...")
            elif isinstance(chunk, Text):
                output_text += chunk.text

        if not output_text.strip():
            # Fallback jika model menggunakan direct resolve
            try:
                resolved_text = await response.text()
                if resolved_text.strip():
                    output_text = resolved_text
            except Exception:
                pass

        if not output_text.strip():
            output_text = "(Instruksi selesai tanpa output balasan teks)"

        # 4. Deteksi dan kirim media berkas jika ada (MEDIA:/path)
        media_paths = extract_media_paths(output_text, workspace_dir=WORKSPACE_DIR)

        # 5. Potong pesan agar sesuai batasan karakter Telegram
        chunks = split_message(output_text, max_length=4000)

        # Kirim respons ke Telegram
        for i, chunk in enumerate(chunks):
            if i == 0 and status_msg:
                # Gantikan pesan status ephemeral dengan pesan hasil pertama
                await safe_edit_message(status_msg, chunk)
            else:
                await safe_send_message(context.bot, chat_id, chunk)

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
        logger.info(f"Task for user {user_id} cancelled.")
        if status_msg:
            await safe_edit_message(status_msg, "🛑 *Tugas dibatalkan oleh pengguna via /cancel.*")
        raise
    except Exception as e:
        logger.error(f"Error saat eksekusi Antigravity: {e}", exc_info=True)
        err_msg = (
            f"❌ **Terjadi kesalahan saat memproses permintaan:**\n"
            f"```text\n{str(e)[:1000]}\n```\n\n"
            f"💡 *Petunjuk:* Pastikan kredensial Antigravity (`~/.gemini`) telah tersambung dengan benar "
            f"atau gunakan `/reset` untuk me-restart sesi."
        )
        if status_msg:
            await safe_edit_message(status_msg, err_msg)
        else:
            await safe_send_message(context.bot, chat_id, err_msg)
    finally:
        stop_typing.set()
        typing_task.cancel()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Router utama untuk pesan teks yang masuk."""
    if not is_authorized(update):
        logger.warning(f"Unauthorized access attempt from User ID: {update.effective_user.id if update.effective_user else 'Unknown'}")
        return

    user_id = update.effective_user.id
    user_text = update.message.text if update.message else ""

    if not user_text.strip():
        return

    # Cek apakah ada tugas yang masih berjalan untuk user ini
    existing_task = user_tasks.get(user_id)
    if existing_task and not existing_task.done():
        await update.message.reply_text(
            "⏳ *Agen sedang menyelesaikan tugas sebelumnya.*\n"
            "Kirim `/cancel` jika Anda ingin menghentikan tugas tersebut.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    user_lock = get_user_lock(user_id)
    if user_lock.locked():
        await update.message.reply_text("⏳ Mohon tunggu sebentar, sesi Anda sedang sibuk.")
        return

    async def _locked_runner():
        async with user_lock:
            await execute_agent_turn(update, context, user_text)

    # Jalankan sebagai background task yang dapat diinterupsi via /cancel
    task = asyncio.create_task(_locked_runner())
    user_tasks[user_id] = task

    def _cleanup_task(t):
        if user_tasks.get(user_id) == t:
            user_tasks.pop(user_id, None)

    task.add_done_callback(_cleanup_task)

# ==============================================================================
# MAIN APPLICATION ENTRYPOINT & SHUTDOWN LIFECYCLE
# ==============================================================================
async def post_shutdown(application):
    """Menutup seluruh sesi Antigravity Agent secara rapi saat bot dimatikan."""
    logger.info("Menutup sesi Antigravity Agent...")
    for user_id, agent in list(user_agents.items()):
        try:
            await agent.__aexit__(None, None, None)
        except Exception as e:
            logger.warning(f"Error saat menutup agent {user_id}: {e}")
    user_agents.clear()
    user_approval_hooks.clear()

def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN belum diatur di file .env! Bot tidak dapat berjalan.")
        sys.exit(1)

    if not ALLOWED_USER_IDS:
        logger.warning("⚠️ ALLOWED_USER_ID belum diatur atau bernilai 0. Tidak ada pengguna yang dapat mengakses bot!")

    logger.info(f"🛡️ Whitelist User IDs: {ALLOWED_USER_IDS}")
    logger.info(f"⚙️ Approval Mode: {APPROVAL_MODE} (Timeout: {APPROVAL_TIMEOUT_SECONDS}s)")
    logger.info(f"📂 Workspace Directory: {WORKSPACE_DIR}")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_shutdown(post_shutdown).build()

    # Daftarkan command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("cancel", cancel_command))

    # Daftarkan handler interaksi tombol konfirmasi
    app.add_handler(CallbackQueryHandler(handle_callback_query))

    # Daftarkan handler pesan teks
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🚀 Antigravity Telegram Bot siap berjalan...")
    app.run_polling()

if __name__ == "__main__":
    main()
