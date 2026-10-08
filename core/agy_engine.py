#!/usr/bin/env python3
"""
Antigravity CLI Subprocess Engine & Transcript Recovery.
Spawns the native agy binary asynchronously, manages multi-turn sessions, models,
transcript-based error recovery, and process-tree cancellation.

This module is the single source of truth for the engine; bot.py re-exports its names.
"""

from __future__ import annotations

import os
import json
import time
import shutil
import signal
import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple, List

from config import (
    AGY_BIN_PATH,
    WORKSPACE_DIR,
    AGY_TIMEOUT_SECONDS,
    DEFAULT_MODEL,
    AGY_SKIP_PERMISSIONS,
    build_cli_prompt,
)

logger = logging.getLogger("antigravity-tele-bot.engine")

# Global registries for state and cancellation (shared with bot.py by reference)
user_conversations: Dict[int, str] = {}
user_processes: Dict[int, asyncio.subprocess.Process] = {}
user_locks: Dict[int, asyncio.Lock] = {}
user_tasks: Dict[int, asyncio.Task] = {}


def get_user_lock(user_id: int) -> asyncio.Lock:
    """Returns mutex lock per user to prevent concurrent race conditions."""
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]


# ==============================================================================
# PROCESS TREE TERMINATION
# ==============================================================================
def terminate_process_tree(proc, force: bool = False) -> None:
    """
    Sends SIGTERM (or SIGKILL when force=True) to the agy process and its children.
    On POSIX the subprocess is started in its own session, so signalling the process
    group also stops tool commands spawned by agy. Falls back to the single process.
    """
    if os.name != "nt" and isinstance(proc, asyncio.subprocess.Process):
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL if force else signal.SIGTERM)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        if force:
            proc.kill()
        else:
            proc.terminate()
    except ProcessLookupError:
        pass


# ==============================================================================
# TRANSCRIPT DISCOVERY & RECOVERY
# ==============================================================================
def _safe_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except (PermissionError, OSError):
        return False


def list_brain_bases() -> List[Path]:
    """Lists existing agy brain directories (bounded-depth scan, no recursive glob)."""
    home = Path.home()
    workspace_gemini = Path(WORKSPACE_DIR) / ".gemini"
    candidates: List[Path] = [
        home / ".gemini" / "antigravity-cli" / "brain",
        home / ".gemini" / "antigravity" / "brain",
        home / ".gemini" / "brain",
        Path("/home/ubuntu/.gemini/antigravity-cli/brain"),
        Path("/home/ubuntu/.gemini/antigravity/brain"),
        Path("/home/ubuntu/.gemini/brain"),
        Path("/root/.gemini/brain"),
        workspace_gemini / "brain",
    ]

    for parent in (home / ".gemini", Path("/home/ubuntu/.gemini"), workspace_gemini):
        if not _safe_is_dir(parent):
            continue
        for pattern in ("*/brain", "*/*/brain"):
            try:
                for b_dir in parent.glob(pattern):
                    if b_dir not in candidates:
                        candidates.append(b_dir)
            except (PermissionError, OSError):
                continue

    return [b for b in candidates if _safe_is_dir(b)]


def _newest_transcript_in(base: Path, since: Optional[float]) -> Tuple[Optional[Path], Optional[str], float]:
    newest_file: Optional[Path] = None
    newest_id: Optional[str] = None
    newest_mtime = -1.0
    try:
        items = list(base.iterdir())
    except (PermissionError, OSError):
        return None, None, newest_mtime

    for item in items:
        try:
            if not item.is_dir():
                continue
            cand = item / ".system_generated" / "logs" / "transcript.jsonl"
            if not cand.is_file():
                continue
            mtime = cand.stat().st_mtime
        except (PermissionError, OSError):
            continue
        if since is not None and mtime < since:
            continue
        if mtime > newest_mtime:
            newest_mtime = mtime
            newest_file = cand
            newest_id = item.name
    return newest_file, newest_id, newest_mtime


def get_transcript_path(
    conv_id: Optional[str],
    since: Optional[float] = None
) -> Tuple[Optional[Path], Optional[str]]:
    """
    Searches for transcript.jsonl for a given conversation ID or, when conv_id is None,
    the newest session. `since` (epoch seconds) restricts the newest-session fallback to
    transcripts written during the current turn, so an unrelated older session is never
    picked up.
    Returns (transcript_path, resolved_conv_id).
    """
    bases = list_brain_bases()

    if conv_id:
        for base in bases:
            try:
                cand = base / conv_id / ".system_generated" / "logs" / "transcript.jsonl"
                if cand.is_file():
                    return cand, conv_id
            except (PermissionError, OSError):
                continue
        return None, conv_id

    # Prefer the current workspace brain, then every other base
    ws_brain = Path(WORKSPACE_DIR) / ".gemini" / "brain"
    if _safe_is_dir(ws_brain):
        found, found_id, _ = _newest_transcript_in(ws_brain, since)
        if found and found_id:
            return found, found_id

    best_file: Optional[Path] = None
    best_id: Optional[str] = None
    best_mtime = -1.0
    for base in bases:
        found, found_id, mtime = _newest_transcript_in(base, since)
        if found and mtime > best_mtime:
            best_file, best_id, best_mtime = found, found_id, mtime

    return best_file, best_id


def _read_full_planner_content(transcript_path: Path, entry: dict) -> Optional[str]:
    full_transcript = transcript_path.parent / "transcript_full.jsonl"
    if not full_transcript.is_file():
        return None
    step_idx = entry.get("step_index")
    try:
        with open(full_transcript, "r", encoding="utf-8", errors="replace") as f_full:
            for full_line in f_full:
                if not full_line.strip():
                    continue
                try:
                    full_entry = json.loads(full_line)
                except Exception:
                    continue
                if (step_idx is not None and full_entry.get("step_index") == step_idx) or (
                    step_idx is None and full_entry.get("type") == "PLANNER_RESPONSE"
                ):
                    full_content = full_entry.get("content", "").strip()
                    if full_content:
                        return full_content
    except Exception as e_full:
        logger.debug(f"Gagal membaca transcript_full.jsonl: {e_full}")
    return None


def recover_last_response_from_transcript(
    conv_id: Optional[str],
    since: Optional[float] = None
) -> Tuple[Optional[str], Optional[str]]:
    """
    Recovers the model's last response from transcript.jsonl if subprocess timed out.
    Enforces anti-stale turn protection (checks USER_INPUT before PLANNER_RESPONSE).
    """
    transcript_path, resolved_conv_id = get_transcript_path(conv_id, since=since)
    if not transcript_path or not transcript_path.is_file():
        return None, resolved_conv_id

    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            lines = [line.strip() for line in f if line.strip()]

        last_planner_content = None
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except Exception:
                continue

            entry_type = entry.get("type", "")
            if entry_type == "USER_INPUT":
                # Reached the start of the current turn: never return an older turn's answer
                break

            if entry_type == "PLANNER_RESPONSE":
                content = entry.get("content", "").strip()
                if "content" in entry.get("truncated_fields", []):
                    content = _read_full_planner_content(transcript_path, entry) or content
                if content:
                    last_planner_content = content
                    break

        # Fall back to a Markdown artifact generated in the conversation directory
        if not last_planner_content:
            for c_dir in (transcript_path.parent.parent.parent, transcript_path.parent.parent):
                if not (c_dir and c_dir.is_dir()):
                    continue
                artifacts = [f for f in c_dir.glob("*.md") if f.is_file() and not f.name.startswith(".")]
                if not artifacts:
                    continue
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


# ==============================================================================
# SUBPROCESS EXECUTION
# ==============================================================================
async def run_agy_cli(
    user_id: int,
    prompt: str,
    conv_id: Optional[str] = None,
    cwd: Optional[str] = None,
    model: Optional[str] = None
) -> Tuple[str, Optional[str]]:
    """
    Executes agy CLI as an asynchronous subprocess.
    Extracts conversation_id and response body with timeout & transcript recovery.
    Returns (response_text, new_or_existing_conv_id).
    """
    effective_cwd = cwd or WORKSPACE_DIR

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

    cmd.extend([
        "-p", build_cli_prompt(prompt),
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
        f"Menjalankan subprocess agy untuk user {user_id} "
        f"(Conv: {conv_id or 'Baru'}, Model: {active_model}, Timeout: {AGY_TIMEOUT_SECONDS}s)..."
    )

    started_at = time.time()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=effective_cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=(os.name != "nt"),
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
        logger.warning(
            f"Proses agy untuk user {user_id} (PID: {getattr(proc, 'pid', 'unknown')}) "
            f"melebihi batas waktu ({AGY_TIMEOUT_SECONDS}s). Menghentikan subprocess..."
        )
        try:
            terminate_process_tree(proc)
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except Exception:
            try:
                terminate_process_tree(proc, force=True)
            except Exception:
                pass
    finally:
        user_processes.pop(user_id, None)

    if timed_out:
        recovered, found_id = recover_last_response_from_transcript(conv_id, since=started_at)
        eff_id = found_id or conv_id
        if recovered:
            return (
                f"{recovered}\n\n"
                f"⏱️ <i>(Catatan: Subprocess agy melebihi batas waktu {AGY_TIMEOUT_SECONDS} detik dan dihentikan, "
                f"namun jawaban berhasil dipulihkan dari log transkrip sistem.)</i>",
                eff_id
            )
        return (
            f"⏱️ **Waktu eksekusi habis (Timeout {AGY_TIMEOUT_SECONDS} detik).**\n"
            f"Subprocess Antigravity telah dihentikan secara aman demi kestabilan sistem.\n\n"
            f"💡 *Jika tugas memerlukan waktu lebih lama, Anda dapat memperbesar nilai `AGY_TIMEOUT_SECONDS` di file `.env`.*",
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
        resp = (parsed_json.get("response") or "").strip()
        status = parsed_json.get("status", "")
        err = (parsed_json.get("error") or "").strip()
        duration = parsed_json.get("duration_seconds")
        num_turns = parsed_json.get("num_turns")

        if status == "ERROR" and err:
            return f"❌ **Error dari agy:**\n```text\n{err}\n```", ret_conv

        if not resp:
            # Fallback: recover from transcript or artifacts if response was empty
            recovered, found_id = recover_last_response_from_transcript(ret_conv, since=started_at)
            if recovered and recovered.strip():
                resp = recovered.strip()
                if found_id:
                    ret_conv = found_id

        if resp:
            return resp, ret_conv
        if err:
            return f"⚠️ **Output agy:**\n```text\n{err}\n```", ret_conv

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
        return stdout_text, conv_id
    if stderr_text:
        return f"⚠️ Output (stderr):\n```text\n{stderr_text}\n```", conv_id

    return "(agy menyelesaikan tugas tanpa balasan output teks)", conv_id
