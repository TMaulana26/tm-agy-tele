#!/usr/bin/env python3
"""
Model Quota & Account Limits Utility.
Parses Antigravity CLI quota data and formats real-time usage reports.
"""

from __future__ import annotations

import os
import re
import html
import json
import shutil
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from config import AGY_BIN_PATH

logger = logging.getLogger("antigravity-tele-bot.usage")


def format_progress_bar(fraction: float, length: int = 10) -> str:
    """Creates a visual terminal progress bar: [████████░░] 78.5%"""
    fraction = max(0.0, min(1.0, fraction))
    filled = int(round(fraction * length))
    empty = length - filled
    return f"[{'█' * filled}{'░' * empty}] {fraction * 100:.1f}%"


def format_relative_time(reset_time_iso: str) -> str:
    """Converts UTC ISO timestamp to user-friendly relative countdown string."""
    if not reset_time_iso:
        return ""
    try:
        clean_iso = reset_time_iso.replace("Z", "+00:00")
        target_dt = datetime.fromisoformat(clean_iso)
        now_utc = datetime.now(timezone.utc)
        diff = target_dt - now_utc

        total_seconds = int(diff.total_seconds())
        if total_seconds <= 0:
            return "Quota available"

        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60

        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0 and days == 0:
            parts.append(f"{minutes}m")

        time_str = " ".join(parts) if parts else "< 1m"
        return f"Refreshes in {time_str}"
    except Exception:
        return reset_time_iso


def format_usage_data(raw_output: str) -> str:
    """Formats JSON or text output from 'agy -p /usage' into clean Telegram HTML."""
    if not raw_output or not raw_output.strip():
        return "⚠️ Tidak ada data kuota yang diterima dari Antigravity CLI."

    parsed = None
    for line in raw_output.splitlines():
        line_str = line.strip()
        if line_str.startswith("{") and line_str.endswith("}"):
            try:
                parsed = json.loads(line_str)
                break
            except Exception:
                continue

    if not parsed:
        try:
            parsed = json.loads(raw_output)
        except Exception:
            pass

    if parsed and isinstance(parsed, dict):
        cmd_data = parsed.get("command", {}).get("data", {})
        groups = cmd_data.get("groups", [])
        if groups:
            lines = [
                "📊 <b>Models &amp; Quota (Antigravity CLI)</b>",
                "━━━━━━━━━━━━━━━━━━━━"
            ]

            group_icons = {
                "gemini": "🤖",
                "claude": "🔮",
            }

            for group in groups:
                g_name = group.get("name", "Model Group")
                g_desc = group.get("description", "")

                icon = "✨"
                for k, ic in group_icons.items():
                    if k in g_name.lower():
                        icon = ic
                        break

                lines.append(f"\n{icon} <b>{html.escape(g_name.upper())}</b>")
                if g_desc:
                    lines.append(f"<i>{html.escape(g_desc)}</i>")

                buckets = group.get("buckets", [])
                for bucket in buckets:
                    b_name = bucket.get("name", "Limit")
                    fraction = float(bucket.get("remaining_fraction", 1.0))
                    reset_time = bucket.get("reset_time", "")

                    bar = format_progress_bar(fraction)
                    rel_time = format_relative_time(reset_time) if fraction < 0.999 else "Quota available"

                    status_icon = "🟢" if fraction > 0.5 else ("🟡" if fraction > 0.2 else "🔴")
                    time_icon = "⏱" if fraction < 0.999 else "✅"

                    lines.append(f"\n• {status_icon} <b>{html.escape(b_name)}</b>")
                    lines.append(f"  <code>{bar}</code>")
                    lines.append(f"  {time_icon} <i>{html.escape(rel_time)}</i>")

            lines.append("\n━━━━━━━━━━━━━━━━━━━━")
            lines.append("💡 <i>Model Flash mengonsumsi kuota lebih hemat dan memiliki limit lebih tinggi.</i>")
            return "\n".join(lines)

    # Fallback TSV or raw lines
    lines = [
        "📊 <b>Models &amp; Quota (Antigravity CLI)</b>",
        "━━━━━━━━━━━━━━━━━━━━"
    ]
    has_content = False
    for line in raw_output.splitlines():
        parts = [p.strip() for p in line.split("\t") if p.strip()]
        if len(parts) >= 3:
            grp = parts[0]
            limit_name = parts[1]
            pct = parts[2]
            extra = f" (Refreshes: {parts[3]})" if len(parts) >= 4 else ""
            lines.append(f"• <b>{html.escape(grp)}</b> - {html.escape(limit_name)}: <code>{html.escape(pct)}</code>{html.escape(extra)}")
            has_content = True
        elif line.strip() and not line.strip().startswith("{"):
            lines.append(html.escape(line.strip()))
            has_content = True

    if not has_content:
        return f"📊 <b>Output Kuota:</b>\n<pre>{html.escape(raw_output[:1000])}</pre>"

    return "\n".join(lines)


def is_quota_inquiry(text: str) -> bool:
    """
    Mendeteksi apakah pesan pengguna menanyakan kuota/limit akun Antigravity,
    bukan instruksi koding (seperti 'SELECT * ... LIMIT 10' atau 'set rate limit').
    """
    if not text:
        return False
    clean = text.strip().lower()
    if not clean:
        return False

    # Perintah langsung
    if clean in ("/usage", "/limit", "usage", "limit", "kuota", "quota"):
        return True

    # Frasa langsung yang sering ditanyakan user
    direct_phrases = [
        "usage limit", "sisa limit", "sisa kuota", "kuota sisa", "limit sisa",
        "cek kuota", "cek limit", "cek usage", "status kuota", "status limit",
        "kuota agy", "limit agy", "limit akun", "kuota akun", "quota limit",
        "remaining quota", "remaining limit", "quota remaining", "usage remaining",
        "kuota model", "limit model"
    ]
    for phrase in direct_phrases:
        if phrase in clean:
            # Pastikan bukan query database seperti SELECT ... LIMIT
            if not re.search(r"\bselect\b.*\blimit\b", clean):
                return True

    # Pola kombinasi: kata tanya/cek/sisa + kata kuota/limit/usage
    has_inquiry_word = bool(re.search(r"\b(sisa|berapa|cek|check|info|status|lihat|tampilkan|ada|habis|kurang)\b", clean))
    has_quota_word = bool(re.search(r"\b(kuota|quota|limit|usage)\b", clean))

    # Abaikan jika ada indikasi instruksi pemrograman teknis
    is_coding = bool(re.search(
        r"\b(sql|query|table|database|mysql|postgres|select|css|div|width|height|rate[\s_-]?limit|pagination|offset)\b",
        clean
    ))

    if has_inquiry_word and has_quota_word and not is_coding:
        return True

    return False


async def fetch_agy_usage_report() -> str:
    """
    Executes 'agy -p /usage --output-format json' via subprocess
    and formats result into a clean Telegram HTML message.
    """
    if not os.path.exists(AGY_BIN_PATH) and not shutil.which(AGY_BIN_PATH):
        return (
            "❌ <b>Binary agy tidak ditemukan di sistem!</b>\n"
            f"Path: <code>{html.escape(AGY_BIN_PATH)}</code>\n"
            "Pastikan binary agy sudah terpasang dan path diatur dengan benar di .env."
        )

    cmd = [
        AGY_BIN_PATH,
        "-p", "/usage",
        "--output-format", "json"
    ]

    env = os.environ.copy()
    extra_paths = [
        "/home/ubuntu/.gemini/antigravity-cli/bin",
        "/home/ubuntu/.local/bin",
        "/usr/local/bin"
    ]
    env["PATH"] = os.pathsep.join(extra_paths + [env.get("PATH", "")])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()

        if stdout_text:
            return format_usage_data(stdout_text)
        elif stderr_text:
            return f"⚠️ <b>Output agy /usage (stderr):</b>\n<pre>{html.escape(stderr_text[:1000])}</pre>"
        else:
            return "⚠️ Tidak ada output dari agy /usage."
    except asyncio.TimeoutError:
        return "⏱️ <b>Waktu tunggu habis (Timeout).</b> Gagal mengambil data kuota dari agy."
    except Exception as e:
        logger.error(f"Error fetching agy usage: {e}", exc_info=True)
        return f"❌ <b>Error saat memanggil agy /usage:</b>\n<code>{html.escape(str(e))}</code>"
