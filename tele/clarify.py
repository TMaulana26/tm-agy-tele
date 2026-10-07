#!/usr/bin/env python3
"""
Interactive Question & Clarification Buttons (Ala Hermes feat/clarify-gateway-buttons).
Transforms structured multiple-choice questions into interactive Telegram Inline Keyboard buttons.
Also provides interactive confirmation dialogs for destructive slash commands (/reset, /cancel).
Adapted from hermes-agent gateway/platforms/telegram/adapter.py and test_telegram_clarify_buttons.py.
"""

from __future__ import annotations

import re
import html
import uuid
import logging
from typing import Optional, List, Tuple, Dict, Any, Union
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger("antigravity-tele-bot.clarify")

# Store in-flight clarify and slash confirm prompts:
# clarify_id -> {"user_id": int, "chat_id": int, "thread_id": Optional[int], "choices": List[str], "created_at": float}
_pending_clarifications: Dict[str, Dict[str, Any]] = {}

# confirm_id -> {"user_id": int, "chat_id": int, "thread_id": Optional[int], "command": str, "future": asyncio.Future}
_pending_slash_confirms: Dict[str, Dict[str, Any]] = {}


def detect_clarify_options(text: str) -> Optional[Tuple[str, List[str]]]:
    """
    Detects whether the model's text response ends with or contains a structured multiple-choice question.
    Returns (question_intro, list_of_options) or None if no valid options detected.
    Example pattern:
      "Pilih salah satu tindakan:
       1. Jalankan unit test
       2. Lakukan git commit
       3. Buat file baru"
    Or checkbox list:
       "- [ ] Option A
        - [ ] Option B"
    """
    if not text:
        return None

    # 1. Look for numbered list items (e.g. 1. ..., 2. ...)
    pattern_num = re.compile(r"(?m)^[ \t]*([1-9]\d?)\.\s+([^\n]+)$")
    matches = list(pattern_num.finditer(text))
    if 2 <= len(matches) <= 8:
        expected = 1
        options = []
        is_seq = True
        for m in matches:
            if int(m.group(1)) != expected:
                is_seq = False
                break
            options.append(m.group(2).strip())
            expected += 1
        if is_seq:
            first_match_start = matches[0].start()
            intro = text[:first_match_start].strip().splitlines()
            question = intro[-1].strip() if intro else "Pilih salah satu opsi berikut:"
            question = re.sub(r"^[*\-#\s:]+", "", question).strip() or "Pilih salah satu opsi berikut:"
            return question, options

    # 2. Look for checkbox or bullet items (e.g. - [ ] ..., * [ ] ..., or - ..., * ...)
    pattern_bullet = re.compile(r"(?m)^[ \t]*[-*]\s+(?:\[[ xX]?\]\s+)?([^\n]+)$")
    matches_b = list(pattern_bullet.finditer(text))
    if 2 <= len(matches_b) <= 8:
        options = [m.group(1).strip() for m in matches_b]
        first_match_start = matches_b[0].start()
        intro = text[:first_match_start].strip().splitlines()
        question = intro[-1].strip() if intro else "Pilih salah satu opsi berikut:"
        question = re.sub(r"^[*\-#\s:]+", "", question).strip() or "Pilih salah satu opsi berikut:"
        return question, options

    return None


def build_clarify_keyboard(
    clarify_id: Union[str, List[str]],
    choices: Optional[List[str]] = None
) -> Tuple[InlineKeyboardMarkup, str] | InlineKeyboardMarkup:
    """
    Constructs an InlineKeyboardMarkup for clarify questions.
    If called as build_clarify_keyboard(choices), auto-generates clarify_id and registers it.
    """
    generated_id = None
    if choices is None and isinstance(clarify_id, list):
        choices = clarify_id
        generated_id = str(uuid.uuid4())[:8]
        cid = generated_id
        register_clarification(cid, {"choices": choices, "options": choices})
    else:
        cid = str(clarify_id)

    rows: List[List[InlineKeyboardButton]] = []
    for idx, choice in enumerate(choices or []):
        clean_choice = re.sub(r"[*_`]", "", choice)
        label = f"{idx + 1}. {clean_choice[:32]}..." if len(clean_choice) > 35 else f"{idx + 1}. {clean_choice}"
        rows.append([InlineKeyboardButton(label, callback_data=f"cl:{cid}:{idx}")])

    rows.append([InlineKeyboardButton("✏️ Opsi Lain (Ketik Manual)", callback_data=f"cl:{cid}:other")])
    kb = InlineKeyboardMarkup(rows)
    if generated_id is not None:
        return kb, generated_id
    return kb


def build_slash_confirm_keyboard(confirm_id: str) -> InlineKeyboardMarkup:
    """Constructs confirmation keyboard [ ✅ Ya, Lanjutkan ] [ ❌ Batal ] for slash commands."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Ya, Lanjutkan", callback_data=f"sc:{confirm_id}:approve"),
            InlineKeyboardButton("❌ Batal", callback_data=f"sc:{confirm_id}:deny"),
        ]
    ])


def register_clarification(clarify_id: str, data: Dict[str, Any]) -> None:
    """Registers pending clarification session."""
    _pending_clarifications[clarify_id] = data
    if len(_pending_clarifications) > 512:
        # Prune oldest
        oldest_key = next(iter(_pending_clarifications))
        _pending_clarifications.pop(oldest_key, None)


def get_clarification(clarify_id: str) -> Optional[Dict[str, Any]]:
    return _pending_clarifications.get(clarify_id)


def pop_clarification(clarify_id: str) -> Optional[Dict[str, Any]]:
    return _pending_clarifications.pop(clarify_id, None)


def register_slash_confirm(confirm_id: str, data: Dict[str, Any]) -> None:
    _pending_slash_confirms[confirm_id] = data
    if len(_pending_slash_confirms) > 256:
        oldest_key = next(iter(_pending_slash_confirms))
        _pending_slash_confirms.pop(oldest_key, None)


def get_slash_confirm(confirm_id: str) -> Optional[Dict[str, Any]]:
    return _pending_slash_confirms.get(confirm_id)


def pop_slash_confirm(confirm_id: str) -> Optional[Dict[str, Any]]:
    return _pending_slash_confirms.pop(confirm_id, None)
