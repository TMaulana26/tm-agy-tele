#!/usr/bin/env python3
"""
Interactive Question & Clarification Buttons (Ala Hermes feat/clarify-gateway-buttons).
Transforms structured multiple-choice questions into interactive Telegram Inline Keyboard buttons.
Adapted from hermes-agent gateway/platforms/telegram/adapter.py and test_telegram_clarify_buttons.py.
"""

from __future__ import annotations

import re
import uuid
import logging
from typing import Optional, List, Tuple, Dict, Any, Union
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger("antigravity-tele-bot.clarify")

# Store in-flight clarify prompts:
# clarify_id -> {"user_id": int, "chat_id": int, "thread_id": Optional[int], "choices": List[str]}
_pending_clarifications: Dict[str, Dict[str, Any]] = {}


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
    Only a list that closes the message and is introduced as a choice (a choice keyword
    or a question mark) qualifies, so ordinary bulleted reports do not grow buttons.
    """
    if not text:
        return None

    # 1. Numbered list items (e.g. 1. ..., 2. ...) numbered sequentially from 1
    block = _trailing_block(text, _NUMBERED_RE)
    if block and [int(m.group(1)) for m in block] == list(range(1, len(block) + 1)):
        question = _choice_question(text, block)
        if question:
            return question, [m.group(2).strip() for m in block]

    # 2. Checkbox or bullet items (e.g. - [ ] ..., * [ ] ..., or - ..., * ...)
    block = _trailing_block(text, _BULLET_RE)
    if block:
        question = _choice_question(text, block)
        if question:
            return question, [m.group(1).strip() for m in block]

    return None


_NUMBERED_RE = re.compile(r"(?m)^[ \t]*([1-9]\d?)\.\s+([^\n]+)$")
_BULLET_RE = re.compile(r"(?m)^[ \t]*[-*]\s+(?:\[[ xX]?\]\s+)?([^\n]+)$")
_CHOICE_INTRO_RE = re.compile(
    r"\b(?:pilih|pilihan|opsi|mana|mau|ingin|lanjutkan|choose|select|pick|options?|which|prefer)\b",
    re.IGNORECASE,
)
_MAX_TRAILING_QUESTION_CHARS = 200


def _trailing_block(text: str, pattern: re.Pattern) -> Optional[List[re.Match]]:
    """Returns the last run of consecutive list lines if it holds 2-8 items."""
    matches = list(pattern.finditer(text))
    if not matches:
        return None
    block = [matches[-1]]
    for m in reversed(matches[:-1]):
        if text[m.end():block[0].start()].strip():
            break
        block.insert(0, m)
    return block if 2 <= len(block) <= 8 else None


def _choice_question(text: str, block: List[re.Match]) -> Optional[str]:
    """Returns the question introducing the list, or None if the list is not a choice."""
    trailing = text[block[-1].end():].strip()
    trailing_is_question = (
        bool(trailing) and trailing.endswith("?") and len(trailing) <= _MAX_TRAILING_QUESTION_CHARS
    )
    if trailing and not trailing_is_question:
        return None

    intro = text[:block[0].start()].strip().splitlines()
    question = intro[-1].strip() if intro else ""
    if not (trailing_is_question or question.endswith("?") or _CHOICE_INTRO_RE.search(question)):
        return None
    return re.sub(r"^[*\-#\s:]+", "", question).strip() or "Pilih salah satu opsi berikut:"


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
