#!/usr/bin/env python3
"""
Telegram entity extraction and message pre-processing.
Expands hidden text_link URLs into plain text and cleans bot mentions.
Adapted from hermes-agent/plugins/platforms/telegram/telegram_entities.py.
"""

from __future__ import annotations

import re
from typing import Any, List, Tuple, Optional


def _utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _code_point_index(text: str, utf16_offset: int) -> Optional[int]:
    """Python index for a UTF-16 code-unit offset; None when it splits a surrogate pair."""
    try:
        return len(text.encode("utf-16-le")[: utf16_offset * 2].decode("utf-16-le"))
    except UnicodeDecodeError:
        return None


def expand_link_entities(message: Any) -> str:
    """
    Message text (or caption) with every hidden text_link URL inlined after its anchor.
    Entity offsets are UTF-16 code units (emoji before the anchor count twice), so they
    are mapped to code-point indices before slicing.
    """
    text = getattr(message, "text", None)
    if text and isinstance(text, str):
        entities = getattr(message, "entities", None) or []
    else:
        caption = getattr(message, "caption", None)
        text = caption if isinstance(caption, str) else ""
        entities = getattr(message, "caption_entities", None) or []

    if not text or not isinstance(text, str) or not entities:
        return text if isinstance(text, str) else ""

    utf16_length = _utf16_length(text)
    links: List[Tuple[int, int, str]] = []

    for entity in entities:
        entity_type = str(getattr(entity, "type", "")).split(".")[-1].lower()
        raw_url = getattr(entity, "url", None)
        url = raw_url.strip() if isinstance(raw_url, str) else ""
        if entity_type != "text_link" or not url:
            continue
        try:
            offset = int(getattr(entity, "offset", -1))
            length = int(getattr(entity, "length", 0))
        except (TypeError, ValueError):
            continue
        if offset < 0 or length <= 0 or offset + length > utf16_length:
            continue
        start = _code_point_index(text, offset)
        end = _code_point_index(text, offset + length)
        if start is None or end is None or end <= start:
            continue
        if text[start:end].strip() == url:
            continue
        links.append((start, end, url))

    expanded = text
    for _start, end, url in sorted(links, reverse=True):
        inline = f" ({url})"
        if expanded[end:].startswith(inline):
            continue
        expanded = f"{expanded[:end]}{inline}{expanded[end:]}"

    return expanded


def clean_bot_mentions(text: Optional[str], bot_username: str = "") -> str:
    """Strips @bot_username mentions from message prompt."""
    if not text or not isinstance(text, str):
        return ""
    if bot_username:
        clean = re.sub(rf"@{re.escape(bot_username)}\b", "", text, flags=re.IGNORECASE)
    else:
        clean = re.sub(r"@[a-zA-Z0-9_]+bot\b", "", text, flags=re.IGNORECASE)
    return clean.strip()
