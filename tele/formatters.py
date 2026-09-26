#!/usr/bin/env python3
"""
Telegram HTML Formatter & Markdown Normalizer.
Converts AI Markdown output into rich, robust Telegram HTML supporting code blocks,
pipe tables, blockquotes, GitHub alerts, duration badges, and raw code tags.
"""

from __future__ import annotations

import re
import html
from typing import Optional


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

    # 9. Format garis pembatas horizontal (---, ***, ___) menjadi garis tipis elegan Telegram
    text = re.sub(r"(?m)^[ \t]*([*\-_~]){3,}[ \t]*$", r"───────────────", text)

    # 10. Format bullet points (* atau - di awal baris, termasuk ber-indentasi spasi/tab) menjadi simbol bullet rapi (• )
    text = re.sub(r"(?m)^([ \t]*)[\*\-]\s+", r"\1• ", text)

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

    return text.strip()


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
