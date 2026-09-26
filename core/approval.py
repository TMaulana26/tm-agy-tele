#!/usr/bin/env python3
"""
Hardline Security Blocklist & Interactive Approval Workflows (Hermes Guard).
Intercepts catastrophic commands outright and requests confirmation for destructive actions.
"""

from __future__ import annotations

import re
import uuid
import asyncio
import logging
from typing import Dict, Tuple, Optional, Any
from telegram import Bot, Message, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import APPROVAL_MODE, APPROVAL_TIMEOUT_SECONDS

logger = logging.getLogger("antigravity-tele-bot.approval")

# ==============================================================================
# HARDLINE SECURITY BLOCKLIST (ZERO-TOLERANCE)
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
    """Checks whether prompt contains catastrophic system-level commands."""
    p_clean = prompt.strip()
    for pattern in HARDLINE_BLOCKLIST:
        if re.search(pattern, p_clean, re.IGNORECASE):
            return True
    return False


# ==============================================================================
# INTENT GUARD (MUTATIVE / DESTRUCTIVE)
# ==============================================================================
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
    """Evaluates if prompt requests high-risk mutative actions."""
    if APPROVAL_MODE == "auto_approve":
        return False, ""

    prompt_low = prompt.strip().lower()
    for pattern in DESTRUCTIVE_PATTERNS:
        match = re.search(pattern, prompt_low)
        if match:
            trigger = match.group(0)
            return True, f"Terdeteksi kata/perintah berisiko: `{trigger}`"

    return False, ""


# Pending approval registry:
# { request_id: {"future": asyncio.Future, "user_id": int, "message": Message, "text": str} }
pending_approvals: Dict[str, dict] = {}


async def request_user_approval(
    bot: Bot,
    chat_id: int,
    user_id: int,
    prompt: str,
    reason: str,
    message_thread_id: Optional[int] = None
) -> bool:
    """
    Sends interactive Approve/Deny buttons to Telegram and awaits user response.
    Uses asyncio.Future with fail-closed timeout.
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

    kwargs: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": pesan_approval,
        "reply_markup": keyboard,
        "parse_mode": ParseMode.MARKDOWN,
    }
    if message_thread_id is not None:
        kwargs["message_thread_id"] = message_thread_id

    try:
        msg = await bot.send_message(**kwargs)
    except Exception as e:
        logger.error(f"Failed to send approval prompt: {e}")
        return False

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
            try:
                await msg.edit_text(f"{pesan_approval}\n\nStatus: {status_label}", parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass
        return approved
    except asyncio.TimeoutError:
        logger.warning(f"Approval request {request_id} timed out (Fail-closed).")
        if msg:
            try:
                await msg.edit_text(
                    f"{pesan_approval}\n\nStatus: ⏱️ **KADALUWARSA (TIMEOUT - DITOLAK OTOMATIS)**",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
        return False
    except asyncio.CancelledError:
        logger.info(f"Approval request {request_id} dibatalkan.")
        if msg:
            try:
                await msg.edit_text(
                    f"{pesan_approval}\n\nStatus: 🛑 **DIBATALKAN VIA /cancel**",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
        return False
    finally:
        pending_approvals.pop(request_id, None)


async def handle_approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles clicks on the [Approve] / [Deny] inline buttons."""
    query = update.callback_query
    if query is None or not query.data:
        return

    await query.answer()
    data = query.data
    action, _, request_id = data.partition(":")

    if request_id in pending_approvals:
        entry = pending_approvals[request_id]
        future = entry.get("future")
        if future and not future.done():
            if action == "appr":
                future.set_result(True)
            elif action == "deny":
                future.set_result(False)
