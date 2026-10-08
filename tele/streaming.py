#!/usr/bin/env python3
"""
Telegram-safe HTML message splitter.
agy runs in print mode (no token stream), so replies are delivered as balanced chunks
once the turn completes.
Adapted from hermes-agent/plugins/platforms/telegram/adapter.py.
"""

from __future__ import annotations

import re
from typing import List, Tuple


TAG_RE = re.compile(r"<(/)?([a-zA-Z0-9_\-]+)(?:\s+[^>]*)?>")


def split_message(text: str, max_length: int = 4000) -> List[str]:
    """
    Safely splits long text so it does not exceed Telegram's limit (4096).
    HTML-aware & tag-balancing:
    - Never breaks in the middle of an HTML tag (<...>) or entity (&...;)
    - Prioritizes splitting outside open tags (at section dividers, paragraph breaks, newlines)
    - Automatically closes open tags at chunk boundary and reopens them in the next chunk
    - Backward-compatible with plain text and standard Markdown
    """
    if not text:
        return ["(Tidak ada output teks dari agy)"]
    if len(text) <= max_length:
        return [text]

    chunks = []
    remaining = text
    active_tags: List[Tuple[str, str]] = []

    while remaining:
        prefix = "".join(t[1] for t in active_tags)

        # Check if entire remaining text fits in one chunk
        if len(prefix) + len(remaining) <= max_length:
            temp_tags = list(active_tags)
            for m in TAG_RE.finditer(remaining):
                is_close, name = m.group(1), m.group(2).lower()
                if is_close:
                    for i in range(len(temp_tags) - 1, -1, -1):
                        if temp_tags[i][0] == name:
                            temp_tags.pop(i)
                            break
                else:
                    temp_tags.append((name, m.group(0)))
            suffix = "".join(f"</{t[0]}>" for t in reversed(temp_tags))
            if len(prefix) + len(remaining) + len(suffix) <= max_length:
                chunks.append(prefix + remaining + suffix)
                break

        # Calculate search budget
        budget = max_length - len(prefix)
        search_window = remaining[:budget]

        # Prevent splitting inside a tag <...>
        last_lt = search_window.rfind("<")
        last_gt = search_window.rfind(">")
        if last_lt > last_gt:
            search_limit = last_lt
        else:
            search_limit = len(search_window)

        # Prevent splitting inside an entity &...;
        last_amp = search_window[:search_limit].rfind("&")
        last_semi = search_window[:search_limit].rfind(";")
        if last_amp > last_semi and (search_limit - last_amp) < 12:
            search_limit = last_amp

        if search_limit <= 0:
            search_limit = max(1, budget - 20)

        # Precompute tag events within search_limit
        tag_events = []
        for m in TAG_RE.finditer(remaining[:search_limit]):
            is_close, name = m.group(1), m.group(2).lower()
            tag_events.append((m.start(), m.end(), bool(is_close), name, m.group(0)))

        def get_active_tags_at(idx: int) -> List[Tuple[str, str]]:
            tags = list(active_tags)
            for start, end, is_close, name, full in tag_events:
                if end <= idx:
                    if is_close:
                        for j in range(len(tags) - 1, -1, -1):
                            if tags[j][0] == name:
                                tags.pop(j)
                                break
                    else:
                        tags.append((name, full))
            return tags

        def is_inside_tag(idx: int) -> bool:
            for start, end, _, _, _ in tag_events:
                if start < idx < end:
                    return True
            return False

        # Gather split candidates
        candidates = []

        # Newlines
        for m in re.finditer(r"\n", search_window[:search_limit]):
            pos = m.start()
            if not is_inside_tag(pos):
                score = 50
                if pos > 0 and remaining[pos - 1] == "\n":
                    score = 100
                elif remaining[max(0, pos - 15):pos] == "───────────────":
                    score = 150
                candidates.append((pos, score))

        # Spaces
        for m in re.finditer(r" ", search_window[:search_limit]):
            pos = m.start()
            if not is_inside_tag(pos):
                candidates.append((pos, 10))

        candidates.sort(key=lambda x: x[0], reverse=True)

        best_split = -1
        best_score = -1

        for pos, base_score in candidates:
            tags_at_pos = get_active_tags_at(pos)
            closing_str = "".join(f"</{t[0]}>" for t in reversed(tags_at_pos))
            if len(prefix) + pos + len(closing_str) > max_length:
                continue

            score = base_score
            if len(tags_at_pos) == 0:
                score += 500
            elif any(t[0] in ("pre", "blockquote") for t in tags_at_pos):
                score -= 100

            ratio = pos / search_limit
            final_score = score + (ratio * 50)
            if final_score > best_score:
                best_score = final_score
                best_split = pos
                if score >= 550 and ratio > 0.6:
                    break

        if best_split == -1 or best_split < (search_limit // 4):
            best_split = search_limit
            while best_split > 1:
                tags_at_pos = get_active_tags_at(best_split)
                closing_str = "".join(f"</{t[0]}>" for t in reversed(tags_at_pos))
                if len(prefix) + best_split + len(closing_str) <= max_length:
                    break
                best_split -= 1

        chunk_text = remaining[:best_split]
        tags_at_split = get_active_tags_at(best_split)
        closing_str = "".join(f"</{t[0]}>" for t in reversed(tags_at_split))

        chunks.append(prefix + chunk_text + closing_str)
        active_tags = tags_at_split
        remaining = remaining[best_split:].lstrip("\r\n")

    return chunks
