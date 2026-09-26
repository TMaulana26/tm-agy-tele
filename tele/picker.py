#!/usr/bin/env python3
"""
Interactive /model Picker with Paginated Inline Keyboard.
Allows switching models on-the-fly per-user or per-topic using official agy models.
Adapted from hermes-agent/plugins/platforms/telegram/adapter.py.
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Dict, Optional, Tuple, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import AGY_BIN_PATH, DEFAULT_MODEL
from database.state import get_db

logger = logging.getLogger("antigravity-tele-bot.picker")

FALLBACK_MODELS = [
    {"id": "gemini-3.8-flash-high", "name": "Gemini 3.8 Flash (High)"},
    {"id": "gemini-3.8-flash-medium", "name": "Gemini 3.8 Flash (Medium)"},
    {"id": "gemini-3.1-pro-high", "name": "Gemini 3.1 Pro (High)"},
    {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6 (Thinking)"},
    {"id": "claude-opus-4-6-thinking", "name": "Claude Opus 4.6 (Thinking)"},
    {"id": "gpt-oss-120b-medium", "name": "GPT-OSS 120B (Medium)"},
]

PAGE_SIZE = 5


async def fetch_available_models() -> List[Dict[str, str]]:
    """Runs 'agy models' asynchronously to fetch installed AI models."""
    try:
        proc = await asyncio.create_subprocess_exec(
            AGY_BIN_PATH, "models",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8.0)
        lines = stdout.decode("utf-8", errors="replace").splitlines()
        models = []
        for line in lines:
            parts = line.strip().split("\t", 1)
            if len(parts) == 2 and not line.startswith("Fetching"):
                models.append({"id": parts[0].strip(), "name": parts[1].strip()})
        if models:
            return models
    except Exception as e:
        logger.debug(f"Could not fetch models via 'agy models': {e}")

    return FALLBACK_MODELS


def build_model_keyboard(
    models: List[Dict[str, str]],
    active_model: str,
    page: int = 0
) -> InlineKeyboardMarkup:
    """Builds a paginated inline keyboard for model selection."""
    total_models = len(models)
    total_pages = max(1, (total_models + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    page_models = models[start:end]

    keyboard = []
    for m in page_models:
        is_active = (m["id"] == active_model)
        label = f"{'✓ ' if is_active else ''}{m['name']}"
        keyboard.append([
            InlineKeyboardButton(label, callback_data=f"model_set:{m['id']}")
        ])

    # Navigation buttons
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀ Sebelumnya", callback_data=f"model_page:{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="model_noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Selanjutnya ▶", callback_data=f"model_page:{page + 1}"))

    keyboard.append(nav_row)
    return InlineKeyboardMarkup(keyboard)


async def handle_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /model command."""
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not update.message:
        return

    thread_id = getattr(update.message, "message_thread_id", None) or "root"
    args = context.args or []

    db = get_db()
    current_model = db.get_user_model(user.id, chat.id, thread_id) or DEFAULT_MODEL
    models = await fetch_available_models()

    if args:
        chosen = args[0].strip().lower()
        matched = next((m for m in models if m["id"].lower() == chosen or chosen in m["name"].lower()), None)
        if matched:
            db.set_user_model(user.id, chat.id, matched["id"], thread_id)
            await update.message.reply_text(
                f"✅ **Model Berhasil Diubah!**\n\n"
                f"• **Model Aktif**: `{matched['name']}` (`{matched['id']}`)\n"
                f"• **Lingkup**: `{'Topik Ini' if thread_id != 'root' else 'Global / Root DM'}`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        else:
            await update.message.reply_text(
                f"⚠️ Model `{chosen}` tidak ditemukan. Silakan pilih dari menu di bawah.",
                parse_mode=ParseMode.MARKDOWN
            )

    keyboard = build_model_keyboard(models, current_model, page=0)
    await update.message.reply_text(
        f"🤖 **Pilih Model AI untuk Sesi Ini:**\n"
        f"Model saat ini: `{(next((m['name'] for m in models if m['id'] == current_model), current_model))}`\n\n"
        f"_Klik tombol di bawah untuk mengganti model:_ ",
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN
    )


async def handle_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles pagination and selection clicks in the /model picker."""
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()
    data = query.data
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    thread_id = getattr(query.message, "message_thread_id", None) or "root"
    db = get_db()
    models = await fetch_available_models()

    if data.startswith("model_page:"):
        page = int(data.split(":")[1])
        current_model = db.get_user_model(user.id, chat.id, thread_id) or DEFAULT_MODEL
        kb = build_model_keyboard(models, current_model, page=page)
        await query.edit_message_reply_markup(reply_markup=kb)

    elif data.startswith("model_set:"):
        new_model_id = data.split(":", 1)[1]
        db.set_user_model(user.id, chat.id, new_model_id, thread_id)
        matched = next((m for m in models if m["id"] == new_model_id), None)
        model_name = matched["name"] if matched else new_model_id

        # Update keyboard with new checkmark
        kb = build_model_keyboard(models, new_model_id, page=0)
        await query.edit_message_text(
            f"✅ **Model Aktif Telah Diperbarui!**\n\n"
            f"• **Model**: `{model_name}`\n"
            f"• **Lingkup**: `{'Topik Ini' if thread_id != 'root' else 'Global / Root DM'}`\n\n"
            f"_Instruksi Anda selanjutnya akan diproses menggunakan model ini._",
            reply_markup=kb,
            parse_mode=ParseMode.MARKDOWN
        )
