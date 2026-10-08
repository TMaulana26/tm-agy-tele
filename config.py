#!/usr/bin/env python3
"""
Central configuration for Antigravity Telegram Bot.
Handles environment variables, default settings, and system paths.
"""

from __future__ import annotations

import os
import shutil
import logging
from pathlib import Path
from typing import List, Set
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("antigravity-tele-bot.config")

# ==============================================================================
# TELEGRAM CREDENTIALS & PERMISSIONS
# ==============================================================================
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

raw_allowed = os.getenv("ALLOWED_USER_ID", "0").strip()
ALLOWED_USER_IDS: Set[int] = set()
for part in raw_allowed.split(","):
    part = part.strip()
    if part.isdigit() and int(part) != 0:
        ALLOWED_USER_IDS.add(int(part))

# ==============================================================================
# ENGINE & BINARY PATH
# ==============================================================================
AGY_BIN_PATH: str = os.getenv(
    "AGY_BIN_PATH",
    shutil.which("agy") or shutil.which("agy.exe") or "/home/ubuntu/.local/bin/agy"
).strip()

DEFAULT_MODEL: str = os.getenv("DEFAULT_MODEL", "gemini-3.8-flash-high").strip()

def resolve_workspace_dir() -> str:
    """
    Validates and resolves the primary workspace directory.
    Fallback priority: env WORKSPACE_DIR -> user home / projects -> cwd / workspace.
    NEVER falls back directly to the root of /home/ubuntu or root home directory
    to prevent unintended media leaks of sensitive user dotfiles (.bash_history, .config, etc.).
    """
    raw = os.getenv("WORKSPACE_DIR", "").strip()
    if raw and os.path.exists(raw) and os.path.isdir(raw):
        return raw

    # Safe isolated subfolder fallback
    home = Path.home()
    safe_projects = home / "projects"
    try:
        safe_projects.mkdir(parents=True, exist_ok=True)
        if raw and raw != str(safe_projects):
            logger.warning(f"WORKSPACE_DIR '{raw}' tidak ditemukan. Menggunakan folder terisolasi: {safe_projects}")
        return str(safe_projects)
    except Exception:
        fallback_cwd = Path.cwd() / "workspace"
        fallback_cwd.mkdir(parents=True, exist_ok=True)
        if raw and raw != str(fallback_cwd):
            logger.warning(f"WORKSPACE_DIR '{raw}' tidak ditemukan. Menggunakan folder terisolasi: {fallback_cwd}")
        return str(fallback_cwd)

WORKSPACE_DIR: str = resolve_workspace_dir()


def resolve_topic_workspace_roots() -> List[str]:
    """
    Directories under which a forum topic may bind a custom workspace (/topic --path=...).
    Configured via TOPIC_WORKSPACE_ROOTS (comma-separated). Defaults to WORKSPACE_DIR and
    the bot user's home directory.
    """
    raw = os.getenv("TOPIC_WORKSPACE_ROOTS", "").strip()
    entries = [p.strip() for p in raw.split(",") if p.strip()] if raw else [WORKSPACE_DIR, str(Path.home())]
    roots: List[str] = []
    for entry in entries:
        try:
            resolved = str(Path(entry).expanduser().resolve())
        except (OSError, RuntimeError):
            continue
        if resolved not in roots:
            roots.append(resolved)
    return roots


TOPIC_WORKSPACE_ROOTS: List[str] = resolve_topic_workspace_roots()

def get_upload_dir() -> Path:
    """Returns directory for Telegram uploads."""
    upload_dir = Path(WORKSPACE_DIR) / ".telegram_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir

def get_data_dir() -> Path:
    """Returns directory for SQLite state and persistent receipts."""
    data_dir = Path(WORKSPACE_DIR) / ".telegram_state"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir

# ==============================================================================
# APPROVAL & TIMEOUTS
# ==============================================================================
APPROVAL_MODE: str = os.getenv("APPROVAL_MODE", "ask_destructive").strip().lower()
APPROVAL_TIMEOUT_SECONDS: int = int(os.getenv("APPROVAL_TIMEOUT_SECONDS", "120"))
AGY_TIMEOUT_SECONDS: int = int(os.getenv("AGY_TIMEOUT_SECONDS", "300"))
AGY_SKIP_PERMISSIONS: bool = os.getenv("AGY_SKIP_PERMISSIONS", "true").strip().lower() in ("true", "1", "yes")

# ==============================================================================
# NETWORK & RESILIENCE
# ==============================================================================
TELEGRAM_FALLBACK_TRANSPORT: bool = os.getenv(
    "TELEGRAM_FALLBACK_TRANSPORT", "true"
).strip().lower() in ("true", "1", "yes")

TELEGRAM_PROXY: str = os.getenv("TELEGRAM_PROXY", "").strip()

# Webhook Dual-Mode & Secrets (GHSA-3vpc-7q5r-276h guard)
TELEGRAM_WEBHOOK_URL: str = os.getenv("TELEGRAM_WEBHOOK_URL", "").strip()
TELEGRAM_WEBHOOK_SECRET: str = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
TELEGRAM_WEBHOOK_PORT: int = int(os.getenv("TELEGRAM_WEBHOOK_PORT", os.getenv("PORT", "8443")))
TELEGRAM_WEBHOOK_LISTEN: str = os.getenv("TELEGRAM_WEBHOOK_LISTEN", "0.0.0.0").strip()

# Watchdog & Health Guards
TELEGRAM_POLLING_STALL_TIMEOUT: float = float(os.getenv("TELEGRAM_POLLING_STALL_TIMEOUT", "120.0"))

# Media & Speech Intelligence
TELEGRAM_IMAGE_PRECOMPRESS: bool = os.getenv("TELEGRAM_IMAGE_PRECOMPRESS", "true").strip().lower() in ("true", "1", "yes")
TELEGRAM_STT_ENABLED: bool = os.getenv("TELEGRAM_STT_ENABLED", "true").strip().lower() in ("true", "1", "yes")
TELEGRAM_WHISPER_MODEL: str = os.getenv("TELEGRAM_WHISPER_MODEL", "base").strip()

# ==============================================================================
# INGRESS COALESCING & GROUP GATING
# ==============================================================================
TELEGRAM_DEBOUNCE_SECONDS: float = float(os.getenv("TELEGRAM_DEBOUNCE_SECONDS", "1.2"))
TELEGRAM_MEDIA_GROUP_SECONDS: float = float(os.getenv("TELEGRAM_MEDIA_GROUP_SECONDS", "1.5"))
TELEGRAM_REQUIRE_MENTION_IN_GROUPS: bool = os.getenv(
    "TELEGRAM_REQUIRE_MENTION_IN_GROUPS", "true"
).strip().lower() in ("true", "1", "yes")

# ==============================================================================
# HEADLESS SYSTEM INSTRUCTIONS
# ==============================================================================
SYSTEM_INSTRUCTIONS: str = (
    "Anda adalah asisten AI Antigravity yang terhubung melalui Telegram Bot di VPS Linux. "
    f"Direktori kerja utama: {WORKSPACE_DIR}.\n\n"
    "ATURAN OPERASIONAL PENTING:\n"
    "1. Anda berjalan dalam sesi headless non-interaktif (print mode).\n"
    "2. JANGAN PERNAH menggunakan tool internal `schedule` untuk recurring cron atau background timers. "
    "Jika pengguna meminta cron job atau penjadwalan otomatis, buat script yang relevan dan tanyakan/konfirmasi kepada pengguna sebelum memasang ke crontab host.\n"
    "3. Selalu selesaikan eksekusi perintah terminal sebelum mengakhiri giliran Anda.\n"
    "4. PENGIRIMAN FILE KE TELEGRAM: Jika pengguna meminta file/dokumen (misal: 'kirim file X', 'kirim laporan', 'kirim berkas') "
    "atau Anda membuat/mengubah berkas yang ingin diserahkan langsung ke pengguna, sertakan baris tersendiri di dalam respons Anda:\n"
    "MEDIA:/path/ke/file\n"
    "Sistem bot akan secara otomatis mendeteksi directive tersebut dan mengirimkan berkas asli ke chat Telegram pengguna.\n"
    "5. BATAS WAKTU PERINTAH JARINGAN (ANTI-HANG): Saat menjalankan perintah terminal pengujian jaringan atau probe socket/streaming "
    "(seperti curl, wget, nc, websocket probe), SELALU gunakan batas waktu ketat (misal: curl --max-time 10 ...) "
    "agar proses tidak menggantung tanpa batas waktu.\n"
    "6. LAPORAN & DOKUMEN PANJANG (ANTI-TOKEN EXHAUSTION): Jika tugas menghasilkan laporan pengujian/audit yang panjang, "
    "kode implementasi masif, atau analisis teknis mendalam:\n"
    "   - Tuliskan laporan teknis lengkap ke file Markdown (.md) di direktori kerja menggunakan tool pembuatan berkas.\n"
    "   - Pada balasan teks terminal ke Telegram, HANYA berikan ringkasan eksekutif singkat dan sertakan baris directive:\n"
    "     MEDIA:/path/ke/laporan.md\n"
    "   - JANGAN PERNAH mencetak seluruh laporan teknis berhalaman-halaman hanya pada satu respon teks terminal agar output tidak terpotong batas token."
)

def build_cli_prompt(prompt: str) -> str:
    """Injects headless VPS operational instructions into the user prompt."""
    return f"[INSTRUKSI SISTEM]\n{SYSTEM_INSTRUCTIONS}\n\n[PERMINTAAN PENGGUNA]\n{prompt}"
