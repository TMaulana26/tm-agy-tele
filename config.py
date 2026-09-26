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
from typing import Set
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
AGY_TIMEOUT_SECONDS: int = int(os.getenv("AGY_TIMEOUT_SECONDS", "180"))
AGY_SKIP_PERMISSIONS: bool = os.getenv("AGY_SKIP_PERMISSIONS", "true").strip().lower() in ("true", "1", "yes")

# ==============================================================================
# NETWORK & RESILIENCE
# ==============================================================================
TELEGRAM_FALLBACK_TRANSPORT: bool = os.getenv(
    "TELEGRAM_FALLBACK_TRANSPORT", "true"
).strip().lower() in ("true", "1", "yes")

TELEGRAM_PROXY: str = os.getenv("TELEGRAM_PROXY", "").strip()

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
    "3. Selalu selesaikan eksekusi perintah terminal sebelum mengakhiri giliran Anda."
)

def build_cli_prompt(prompt: str) -> str:
    """Injects headless VPS operational instructions into the user prompt."""
    return f"[INSTRUKSI SISTEM]\n{SYSTEM_INSTRUCTIONS}\n\n[PERMINTAAN PENGGUNA]\n{prompt}"
