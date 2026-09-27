#!/usr/bin/env python3
"""
Antigravity CLI Subprocess Engine & Transcript Recovery.
Spawns native agy binary asynchronously, manages multi-turn sessions, models,
transcript-based error recovery, and process cancellation.
"""

from __future__ import annotations

import os
import sys
import json
import time
import shutil
import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple, List, Any

from config import (
    AGY_BIN_PATH,
    WORKSPACE_DIR,
    AGY_TIMEOUT_SECONDS,
    DEFAULT_MODEL,
    AGY_SKIP_PERMISSIONS,
    build_cli_prompt,
)

logger = logging.getLogger("antigravity-tele-bot.engine")

# Global registries for state and cancellation
user_conversations: Dict[int, str] = {}
user_processes: Dict[int, asyncio.subprocess.Process] = {}
user_locks: Dict[int, asyncio.Lock] = {}
user_tasks: Dict[int, asyncio.Task] = {}


def get_user_lock(user_id: int) -> asyncio.Lock:
    """Returns mutex lock per user to prevent concurrent race conditions."""
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]


def get_transcript_path(conv_id: Optional[str]) -> Tuple[Optional[Path], Optional[str]]:
    """
    Searches for transcript.jsonl for a given conversation ID or the newest session.
    Returns (transcript_path, resolved_conv_id).
    """
    candidate_bases: List[Path] = []
    home = Path.home()
    candidate_bases.extend([
        home / ".gemini" / "antigravity-cli" / "brain",
        home / ".gemini" / "antigravity" / "brain",
        home / ".gemini" / "brain",
        Path("/home/ubuntu/.gemini/antigravity-cli/brain"),
        Path("/home/ubuntu/.gemini/antigravity/brain"),
        Path("/home/ubuntu/.gemini/brain"),
        Path("/root/.gemini/brain"),
        Path(WORKSPACE_DIR) / ".gemini" / "brain",
    ])

    # Dynamic recursive scan under ~/.gemini for any brain folders
    search_parents = [home / ".gemini", Path("/home/ubuntu/.gemini"), Path(WORKSPACE_DIR) / ".gemini"]
    for sp in search_parents:
        if sp.is_dir():
            try:
                for b_dir in sp.glob("**/brain"):
                    if b_dir.is_dir() and b_dir not in candidate_bases:
                        candidate_bases.append(b_dir)
            except Exception:
                pass

    valid_bases = [b for b in candidate_bases if b.is_dir()]

    if conv_id:
        for base in valid_bases:
            cand = base / conv_id / ".system_generated" / "logs" / "transcript.jsonl"
            if cand.is_file():
                return cand, conv_id
        return None, conv_id

    # Fallback to newest conversation folder: prioritize current workspace first
    ws_brain = Path(WORKSPACE_DIR) / ".gemini" / "brain"
    if ws_brain.is_dir():
        ws_newest_file = None
        ws_newest_mtime = -1.0
        ws_conv_id = None
        try:
            for item in ws_brain.iterdir():
                if item.is_dir():
                    cand = item / ".system_generated" / "logs" / "transcript.jsonl"
                    if cand.is_file():
                        mtime = cand.stat().st_mtime
                        if mtime > ws_newest_mtime:
                            ws_newest_mtime = mtime
                            ws_newest_file = cand
                            ws_conv_id = item.name
            if ws_newest_file and ws_conv_id:
                return ws_newest_file, ws_conv_id
        except Exception as e:
            logger.debug(f"Error checking workspace brain dir {ws_brain}: {e}")

    # Fallback to newest conversation folder across all candidate bases
    newest_file: Optional[Path] = None
    newest_mtime = -1.0
    found_conv_id: Optional[str] = None

    for base in valid_bases:
        try:
            for item in base.iterdir():
                if item.is_dir():
                    cand = item / ".system_generated" / "logs" / "transcript.jsonl"
                    if cand.is_file():
                        mtime = cand.stat().st_mtime
                        if mtime > newest_mtime:
                            newest_mtime = mtime
                            newest_file = cand
                            found_conv_id = item.name
        except Exception as e:
            logger.debug(f"Error checking brain dir {base}: {e}")

    if newest_file and found_conv_id:
        return newest_file, found_conv_id

    return None, None


def recover_last_response_from_transcript(conv_id: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Recovers the model's last response from transcript.jsonl if subprocess timed out.
    Enforces anti-stale turn protection (checks USER_INPUT before PLANNER_RESPONSE).
    """
    transcript_path, resolved_conv_id = get_transcript_path(conv_id)
    if not transcript_path or not transcript_path.is_file():
        return None, resolved_conv_id

    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            lines = [line.strip() for line in f if line.strip()]

        last_planner_content = None
        user_input_seen_after_planner = False

        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except Exception:
                continue

            entry_type = entry.get("type", "")
            if entry_type == "USER_INPUT":
                if last_planner_content is None:
                    # Model has not replied yet for this turn
                    user_input_seen_after_planner = True
                    break

            if entry_type == "PLANNER_RESPONSE" and last_planner_content is None:
                content = entry.get("content", "").strip()
                if content:
                    last_planner_content = content

        if user_input_seen_after_planner:
            # Cegah mengambil PLANNER_RESPONSE lama dari giliran sebelumnya
            last_planner_content = None

        # Check if an artifact (.md) was generated in conv_dir if last_planner_content is empty
        if not last_planner_content:
            candidate_dirs = [
                transcript_path.parent.parent.parent,
                transcript_path.parent.parent,
            ]
            for c_dir in candidate_dirs:
                if c_dir and c_dir.is_dir():
                    artifacts = [
                        f for f in c_dir.glob("*.md")
                        if f.is_file() and not f.name.startswith(".")
                    ]
                    if artifacts:
                        artifacts.sort(key=lambda f: f.stat().st_mtime, reverse=True)
                        try:
                            art_text = artifacts[0].read_text(encoding="utf-8", errors="replace").strip()
                            if art_text:
                                logger.info(f"Berhasil me-recover artefak '{artifacts[0].name}' dari {c_dir}")
                                last_planner_content = art_text
                                break
                        except Exception as e_art:
                            logger.debug(f"Gagal membaca artefak {artifacts[0]}: {e_art}")

        if last_planner_content:
            logger.info(
                f"Berhasil me-recover balasan model ({len(last_planner_content)} karakter) dari "
                f"{transcript_path} (Conv: {resolved_conv_id})"
            )
            return last_planner_content, resolved_conv_id

        return None, resolved_conv_id
    except Exception as e:
        logger.warning(f"Error reading transcript {transcript_path}: {e}")
        return None, resolved_conv_id


async def run_agy_cli(
    user_id: int,
    prompt: str,
    conv_id: Optional[str] = None,
    model: Optional[str] = None,
    cwd: str = WORKSPACE_DIR
) -> Tuple[str, Optional[str]]:
    """
    Executes agy CLI as an asynchronous subprocess.
    Extracts conversation_id and response body with timeout & transcript recovery.
    Returns (response_text, new_or_existing_conv_id).
    """
    if not os.path.exists(AGY_BIN_PATH) and not shutil.which(AGY_BIN_PATH):
        raise FileNotFoundError(
            f"Binary agy tidak ditemukan di: '{AGY_BIN_PATH}'. "
            f"Periksa variabel AGY_BIN_PATH di file .env."
        )

    cmd = [AGY_BIN_PATH]
    if conv_id:
        cmd.extend(["--conversation", conv_id])

    active_model = model or DEFAULT_MODEL
    if active_model:
        cmd.extend(["--model", active_model])

    full_prompt = build_cli_prompt(prompt)
    cmd.extend([
        "-p", full_prompt,
        "--print-timeout", f"{AGY_TIMEOUT_SECONDS}s",
    ])
    if AGY_SKIP_PERMISSIONS:
        cmd.append("--dangerously-skip-permissions")
    cmd.extend(["--output-format", "json"])

    env = os.environ.copy()
    extra_paths = [
        "/home/ubuntu/.gemini/antigravity-cli/bin",
        "/home/ubuntu/.local/bin",
        "/usr/local/bin"
    ]
    env["PATH"] = os.pathsep.join(extra_paths + [env.get("PATH", "")])

    logger.info(
        f"Executing agy subprocess for user {user_id} "
        f"(Conv: {conv_id or 'New'}, Model: {active_model}, Timeout: {AGY_TIMEOUT_SECONDS}s)..."
    )

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    user_processes[user_id] = proc
    stdout_bytes = b""
    stderr_bytes = b""
    timed_out = False

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=float(AGY_TIMEOUT_SECONDS + 10)
        )
    except asyncio.TimeoutError:
        timed_out = True
        logger.warning(f"Subprocess agy user {user_id} timed out. Terminating...")
        try:
            res = proc.terminate()
            if asyncio.iscoroutine(res):
                await res
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except Exception:
            try:
                k_res = proc.kill()
                if asyncio.iscoroutine(k_res):
                    await k_res
            except Exception:
                pass
    finally:
        user_processes.pop(user_id, None)

    if timed_out:
        recovered, found_id = recover_last_response_from_transcript(conv_id)
        eff_id = found_id or conv_id
        if recovered:
            return (
                f"{recovered}\n\n"
                f"⏱️ <i>(Catatan: Subprocess agy melebihi batas waktu {AGY_TIMEOUT_SECONDS}s, "
                f"jawaban berhasil dipulihkan dari transkrip sistem.)</i>",
                eff_id
            )
        return (
            f"⏱️ **Waktu eksekusi habis (Timeout {AGY_TIMEOUT_SECONDS} detik).**\n"
            f"Subprocess Antigravity telah dihentikan secara aman demi kestabilan sistem.",
            eff_id
        )

    stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
    stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()

    parsed_json = None
    if stdout_text:
        for line in stdout_text.splitlines():
            line_str = line.strip()
            if line_str.startswith("{") and line_str.endswith("}"):
                try:
                    parsed_json = json.loads(line_str)
                    break
                except Exception:
                    continue
        if not parsed_json:
            try:
                parsed_json = json.loads(stdout_text)
            except Exception:
                pass

    if parsed_json and isinstance(parsed_json, dict):
        ret_conv = parsed_json.get("conversation_id") or conv_id
        resp = parsed_json.get("response", "").strip()
        status = parsed_json.get("status", "")
        err = parsed_json.get("error", "").strip()
        duration = parsed_json.get("duration_seconds")
        num_turns = parsed_json.get("num_turns")

        if status == "ERROR" and err:
            return f"❌ **Error dari agy:**\n```text\n{err}\n```", ret_conv

        if not resp:
            # Fallback: Recover from transcript or artifacts if response was empty
            recover_fn = getattr(sys.modules[__name__], "recover_last_response_from_transcript", recover_last_response_from_transcript)
            recovered, found_id = recover_fn(ret_conv)
            if recovered and recovered.strip():
                resp = recovered.strip()
                if found_id:
                    ret_conv = found_id

        if resp:
            return resp, ret_conv
        elif err:
            return f"⚠️ **Output agy:**\n```text\n{err}\n```", ret_conv
        else:
            dur_str = f" ({duration:.1f}s)" if isinstance(duration, (int, float)) else ""
            turns_str = f" ({num_turns} turns)" if isinstance(num_turns, int) and num_turns > 1 else ""
            timeout_hint = ""
            if isinstance(duration, (int, float)) and duration >= (AGY_TIMEOUT_SECONDS - 5):
                timeout_hint = (
                    f"\n\n⏱️ *Catatan:* Proses selesai di batas waktu `{AGY_TIMEOUT_SECONDS}s`. "
                    f"Jika tugas membutuhkan analisis lebih panjang, perbesar nilai `AGY_TIMEOUT_SECONDS` di file `.env`."
                )
            return (
                f"✅ **Tugas Selesai!** Antigravity telah menyelesaikan seluruh langkah eksekusi di latar belakang{dur_str}{turns_str}, namun tidak ada pesan balasan teks langsung.{timeout_hint}",
                ret_conv
            )

    if stdout_text:
        clean_stdout = stdout_text.strip()
        if clean_stdout.startswith("{") and clean_stdout.endswith("}"):
            try:
                data = json.loads(clean_stdout)
                if "conversation_id" in data or "status" in data:
                    return "✅ **Tugas Selesai!** Antigravity telah menyelesaikan tugas sistem tanpa balasan teks.", conv_id
            except Exception:
                pass
        return stdout_text, conv_id
    elif stderr_text:
        return f"⚠️ Output (stderr):\n```text\n{stderr_text}\n```", conv_id

    return "(agy menyelesaikan tugas tanpa balasan output teks)", conv_id
