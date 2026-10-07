#!/usr/bin/env python3
"""
Telegram HTML Formatter & Markdown Normalizer.
Converts AI Markdown output into rich, robust Telegram HTML supporting code blocks,
pipe tables, blockquotes, GitHub alerts, duration badges, and raw code tags.
"""

from __future__ import annotations

import re
import html
from typing import Optional, List, Tuple


def markdown_to_telegram_html(text: str) -> str:
    """
    Mengonversi output Markdown dari AI ke format Telegram HTML yang valid dan rapi.
    Sangat tahan banting terhadap underscore (seperti nama container Docker),
    karakter khusus, regex, dan format heading.
    """
    if not text:
        return ""

    # Normalisasi line endings (CRLF -> LF) agar regex multiline konsisten di semua OS
    text = text.replace("\r\n", "\n").replace("\r", "\n")

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
        r"(?m)^([ \t]*\|?[^\n]*\|[^\n]*\n"
        r"[ \t]*\|?(?:[ \t]*:?-+:?[ \t]*\|)+[ \t]*:?-+:?[ \t]*\|?[ \t]*(?:\n|\Z))"
        r"((?:[ \t]*\|?[^\n]*\|[^\n]*(?:\n|\Z))*)"
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

    inline_codes = []

    # 3. Simpan link file:/// lokal SEBELUM inline code agar nested backtick [`file`](file:///...) tidak konflik
    def clean_file_link(match):
        raw_label = match.group(1).strip()
        label = raw_label.strip("`").strip()
        idx = len(inline_codes)
        escaped_label = html.escape(label)
        inline_codes.append(f"<code>{escaped_label}</code>")
        return f"\x00INLINECODE{idx}\x00"

    text = re.sub(r"\[([^\]]+)\]\(file:///[^)]+\)", clean_file_link, text)

    # 4. Simpan tautan web standar [label](https://...) SEBELUM inline code
    def save_web_link(match):
        raw_label = match.group(1).strip()
        has_code = raw_label.startswith("`") and raw_label.endswith("`")
        label = raw_label.strip("`").strip()
        url = match.group(2).strip()
        idx = len(inline_codes)
        escaped_label = html.escape(label)
        escaped_url = html.escape(url, quote=True)
        if has_code:
            replacement = f'<a href="{escaped_url}"><code>{escaped_label}</code></a>'
        else:
            replacement = f'<a href="{escaped_url}">{escaped_label}</a>'
        inline_codes.append(replacement)
        return f"\x00INLINECODE{idx}\x00"

    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", save_web_link, text)

    # 5. Simpan inline code standar (`...`)
    def save_inline_code(match):
        code = match.group(1)
        idx = len(inline_codes)
        escaped_code = html.escape(code)
        inline_codes.append(f"<code>{escaped_code}</code>")
        return f"\x00INLINECODE{idx}\x00"

    text = re.sub(r"`([^`\n]+)`", save_inline_code, text)

    # 6. Tangkap tag <code>...</code> mentah yang ditulis langsung tanpa backtick
    def preserve_raw_code_tag(match):
        content = match.group(1)
        idx = len(inline_codes)
        escaped_content = html.escape(content)
        inline_codes.append(f"<code>{escaped_content}</code>")
        return f"\x00INLINECODE{idx}\x00"

    text = re.sub(r"<code>([\s\S]*?)</code>", preserve_raw_code_tag, text, flags=re.IGNORECASE)

    # 7. Escape HTML pada sisa teks biasa (&, <, >)
    text = html.escape(text)

    # 8. Format headers (###, ##, #) menjadi bold tanpa tanda pagar dan tanpa double **
    def format_header(match):
        content = match.group(1).strip()
        clean_content = re.sub(r"\*\*(.*?)\*\*", r"\1", content)
        return f"<b>{clean_content}</b>"

    text = re.sub(r"(?m)^#{1,6}\s*(.*?)$", format_header, text)

    # 8b. Format list item temuan keparahan (misal: 1. [HIGH] ... atau 4. [MEDIUM] ...) agar bold konsisten
    def format_severity_header(match):
        content = match.group(1).strip()
        clean_content = re.sub(r"\*\*(.*?)\*\*", r"\1", content)
        return f"<b>{clean_content}</b>"

    text = re.sub(
        r"(?m)^(\d+\.\s+\[(?:CRITICAL|HIGH|MEDIUM|LOW|INFO)[^\]]*\][^\n]+)$",
        format_severity_header,
        text
    )

    # 9. Format garis pembatas horizontal (---, ***, ___) menjadi garis tipis elegan Telegram
    text = re.sub(r"(?m)^[ \t]*([*\-_~]){3,}[ \t]*$", r"───────────────", text)

    # 10. Format bullet points (* atau - di awal baris, termasuk ber-indentasi spasi/tab) menjadi simbol bullet rapi (• )
    text = re.sub(r"(?m)^([ \t]*)[\*\-]\s+", r"\1• ", text)

    # 10b. Format triple bold + italic (***text*** atau ___text___) -> <b><i>text</i></b>
    text = re.sub(r"\*\*\*([^\*\n]+?)\*\*\*", r"<b><i>\1</i></b>", text)
    text = re.sub(r"___([^_\n]+?)___", r"<b><i>\1</i></b>", text)

    # 11. Format bold (**text** atau __text__) -> <b>text</b>
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)

    # 12. Format italic (*text* atau _text_)
    text = re.sub(r"(?<!\w)\*([^\*\n]+?)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!\w)_([^_\n]+?)_(?!\w)", r"<i>\1</i>", text)

    # 13. Format blockquote berturut-turut (> text) menjadi satu <blockquote>...</blockquote>
    def format_contiguous_blockquotes(match):
        block = match.group(0)
        lines = []
        for line in block.splitlines():
            cleaned = re.sub(r"^&gt;\s?", "", line).strip()
            lines.append(cleaned)
        content = "\n".join(lines).strip()

        # Konversi GitHub-style alerts ([!NOTE], [!TIP], [!IMPORTANT], [!WARNING], [!CAUTION])
        alert_map = {
            "[!NOTE]": "💡 <b>Catatan:</b>\n",
            "[!TIP]": "💡 <b>Tips:</b>\n",
            "[!IMPORTANT]": "📌 <b>Penting:</b>\n",
            "[!WARNING]": "⚠️ <b>Peringatan:</b>\n",
            "[!CAUTION]": "🛑 <b>Perhatian:</b>\n",
        }
        for tag, header in alert_map.items():
            if content.startswith(tag):
                content = header + content[len(tag):].lstrip()
                break

        return f"<blockquote>{content}</blockquote>\n"

    text = re.sub(r"(?m)(?:^&gt;.*$\n?)+", format_contiguous_blockquotes, text)

    # 14. Kembalikan inline codes dan code blocks secara multi-pass agar tidak ada marker tersisa
    for _ in range(5):
        replaced = False
        for idx, replacement in enumerate(inline_codes):
            marker = f"\x00INLINECODE{idx}\x00"
            if marker in text:
                text = text.replace(marker, replacement)
                replaced = True
        for idx, replacement in enumerate(code_blocks):
            marker = f"\x00CODEBLOCK{idx}\x00"
            if marker in text:
                text = text.replace(marker, replacement)
                replaced = True
        if not replaced:
            break

    # Sanitasi darurat: jika masih ada placeholder yang bocor karena alasan anomali, bersihkan
    text = re.sub(r"\x00?(?:INLINECODE|CODEBLOCK)\d+\x00?", "", text)

    # 15. Pastikan semua tag HTML Telegram seimbang, valid, dan tidak bersilangan
    return sanitize_and_balance_html(text.strip())


def sanitize_and_balance_html(text: str) -> str:
    """
    Ensures that HTML tags for Telegram are strictly balanced and valid.
    1. Tracks opened Telegram tags using a stack.
    2. Resolves overlapping/crossing tags (e.g. <b>...<i>...</b></i> -> <b>...<i>...</i></b>).
    3. Drops orphaned closing tags (e.g. </b> without <b>).
    4. Escapes unsupported HTML tags (&lt;...&gt;).
    5. Closes any remaining unclosed tags at the end of the text.
    """
    if not text:
        return ""

    allowed_tags = {
        "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
        "span", "tg-spoiler", "tg-emoji", "a", "code", "pre", "blockquote"
    }

    tag_re = re.compile(r"<(/)?([a-zA-Z0-9_-]+)((?:\s+[^>]*)?)>")

    output_parts: List[str] = []
    open_tags: List[Tuple[str, str]] = []  # list of (tag_name, full_open_tag)
    last_idx = 0

    for match in tag_re.finditer(text):
        start, end = match.span()
        if start > last_idx:
            output_parts.append(text[last_idx:start])
        last_idx = end

        is_closing = bool(match.group(1))
        tag_name = match.group(2).lower()
        attributes = match.group(3) or ""
        full_match = match.group(0)

        if tag_name not in allowed_tags:
            # Escape unsupported tag so it doesn't break Telegram API parser
            output_parts.append(f"&lt;{match.group(1) or ''}{tag_name}{attributes}&gt;")
            continue

        if not is_closing:
            open_tags.append((tag_name, full_match))
            output_parts.append(full_match)
        else:
            if not open_tags:
                # Orphaned closing tag, discard it
                continue

            if open_tags[-1][0] == tag_name:
                open_tags.pop()
                output_parts.append(f"</{tag_name}>")
            else:
                matching_indices = [i for i, (t, _) in enumerate(open_tags) if t == tag_name]
                if matching_indices:
                    idx = matching_indices[-1]
                    tags_to_close = open_tags[idx + 1:]
                    for t_name, _ in reversed(tags_to_close):
                        output_parts.append(f"</{t_name}>")
                    output_parts.append(f"</{tag_name}>")
                    open_tags = open_tags[:idx]
                else:
                    # Tag not in stack, orphaned closing tag: discard it
                    continue

    if last_idx < len(text):
        output_parts.append(text[last_idx:])

    # Close any unclosed tags at the end in reverse order
    for tag_name, _ in reversed(open_tags):
        output_parts.append(f"</{tag_name}>")

    return "".join(output_parts)


def append_duration_badge(formatted_html: str, elapsed_seconds: int) -> str:
    """Appends duration badge if not already present in the response."""
    non_empty = [l.strip() for l in formatted_html.splitlines() if l.strip()]
    last_line = non_empty[-1].lower() if non_empty else ""
    has_footer = bool(re.search(r"^⏱️.*(?:respons dalam|waktu respons|durasi pengerjaan)", last_line))
    if has_footer:
        return formatted_html

    sec = max(1, elapsed_seconds)
    duration_str = f"~{sec}s" if sec < 60 else f"~{sec // 60}m {sec % 60}s"
    return f"{formatted_html.rstrip()}\n\n⏱️ <i>Respons dalam {duration_str}</i>"


def strip_html_for_plain_text(html_text: str) -> str:
    """
    Strips Telegram HTML tags and unescapes entities for safe, clean plain-text fallback.
    Prevents raw <b>, <code>, <pre>, &amp;, &#x27; from polluting user chat if Telegram rejects formatting.
    """
    if not html_text:
        return ""
    # Strip Telegram-supported HTML tags
    clean = re.sub(
        r"</?(?:b|strong|i|em|u|ins|s|strike|del|span|tg-spoiler|tg-emoji|code|pre|blockquote|a)(?:\s+[^>]*)?>",
        "",
        html_text
    )
    # Unescape HTML entities (&amp; -> &, &lt; -> <, &gt; -> >, &#x27; -> ', etc.)
    return html.unescape(clean)


# ==============================================================================
# TREE-AWARE MARKDOWN CHUNKING & CODE BLOCK PRESERVATION (ALA HERMES)
# ==============================================================================
def fence_state_after(text: str, in_code: bool = False, lang: str = "") -> Tuple[bool, str]:
    """
    Walk text line by line toggling on ``` lines.
    Returns (in_code, language_tag).
    """
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            tag = stripped[3:].split()
            in_code, lang = (False, "") if in_code else (True, tag[0] if tag else "")
    return in_code, lang


def truncate_message(
    content: str,
    max_length: int = 4000,
    len_fn: Optional[Any] = None
) -> List[str]:
    """
    Splits a long Markdown message into chunks preserving code fences:
    A split inside a code block closes the fence at the chunk end (```) and
    reopens it with the same language tag (```{lang}) in the next chunk.
    Multi-chunk output receives pagination indicators (1/N).
    Adapted from hermes-agent gateway/platforms/base.py.
    """
    _len = len_fn or len
    if _len(content) <= max_length:
        return [content]

    INDICATOR_RESERVE = 16
    FENCE_CLOSE = "\n```"
    chunks: List[str] = []
    remaining = content
    carry_lang: Optional[str] = None

    while remaining:
        prefix = f"```{carry_lang}\n" if carry_lang is not None else ""
        headroom = max_length - INDICATOR_RESERVE - _len(prefix) - _len(FENCE_CLOSE)
        if headroom < 1:
            headroom = max(1, max_length // 2)

        if _len(prefix) + _len(remaining) <= max_length - INDICATOR_RESERVE:
            final_chunk = prefix + remaining
            if carry_lang is not None and fence_state_after(remaining, True, carry_lang)[0]:
                final_chunk += FENCE_CLOSE
            chunks.append(final_chunk)
            break

        region = remaining[:headroom]
        split_at = region.rfind("\n")
        if split_at < headroom // 2:
            split_at = region.rfind(" ")
        if split_at < 1:
            split_at = max(1, headroom)

        # Avoid splitting inside inline code span
        candidate = remaining[:split_at]
        backtick_count = candidate.count("`") - candidate.count("\\`")
        if backtick_count % 2 == 1:
            last_bt = candidate.rfind("`")
            while last_bt > 0 and candidate[last_bt - 1] == "\\":
                last_bt = candidate.rfind("`", 0, last_bt)
            if last_bt > 0:
                safe_split = max(candidate.rfind(" ", 0, last_bt), candidate.rfind("\n", 0, last_bt))
                if safe_split > headroom // 4:
                    split_at = safe_split

        chunk_body = remaining[:split_at]
        remaining = remaining[split_at:].lstrip()
        full_chunk = prefix + chunk_body

        in_code, lang = fence_state_after(chunk_body, carry_lang is not None, carry_lang or "")
        carry_lang = lang if in_code else None

        chunks.append(full_chunk + FENCE_CLOSE if in_code else full_chunk)

    if len(chunks) > 1:
        chunks = [f"{chunk} ({i + 1}/{len(chunks)})" for i, chunk in enumerate(chunks)]
    return chunks

