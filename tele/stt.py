#!/usr/bin/env python3
"""
Speech-to-Text (STT) Transcription Engine for Inbound Telegram Voice Notes.
Converts voice memos (.ogg / Opus) into prompt text via local Whisper or transcription tools.
Adapted from hermes-agent gateway voice pipelines.
"""

from __future__ import annotations

import os
import shutil
import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("antigravity-tele-bot.stt")


async def transcribe_audio_file(
    audio_path: str | Path,
    model_name: str = "base",
    timeout_seconds: float = 60.0
) -> Optional[str]:
    """
    Transcribes an audio file (e.g. .ogg, .mp3, .wav) into text.
    1. Attempts native 'whisper' CLI subprocess if available in PATH.
    2. Attempts in-process faster_whisper or whisper python packages if installed.
    3. Returns transcribed string or None if transcription unavailable / failed.
    """
    audio_file = Path(audio_path).resolve()
    if not audio_file.is_file():
        logger.warning(f"Audio file not found for STT: {audio_file}")
        return None

    # 1. Try local 'whisper' CLI
    whisper_bin = shutil.which("whisper") or shutil.which("whisper.exe")
    if whisper_bin:
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                proc = await asyncio.create_subprocess_exec(
                    whisper_bin,
                    str(audio_file),
                    "--model", model_name,
                    "--output_format", "txt",
                    "--output_dir", tmp_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
                if proc.returncode == 0:
                    txt_file = Path(tmp_dir) / f"{audio_file.stem}.txt"
                    if txt_file.is_file():
                        transcript = txt_file.read_text(encoding="utf-8").strip()
                        if transcript:
                            logger.info(f"STT CLI transcribed {audio_file.name}: {transcript[:60]}...")
                            return transcript
        except Exception as e:
            logger.debug(f"Whisper CLI transcription attempt failed: {e}")

    # 2. Try python packages: faster_whisper
    try:
        from faster_whisper import WhisperModel
        loop = asyncio.get_running_loop()

        def _run_faster():
            model = WhisperModel(model_name, device="auto", compute_type="default")
            segments, _ = model.transcribe(str(audio_file))
            return " ".join([seg.text.strip() for seg in segments if seg.text.strip()])

        transcript = await asyncio.wait_for(loop.run_in_executor(None, _run_faster), timeout=timeout_seconds)
        if transcript:
            return transcript.strip()
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"faster_whisper transcription failed: {e}")

    # 3. Try python package: whisper (openai-whisper)
    try:
        import whisper
        loop = asyncio.get_running_loop()

        def _run_whisper():
            model = whisper.load_model(model_name)
            result = model.transcribe(str(audio_file))
            return result.get("text", "").strip()

        transcript = await asyncio.wait_for(loop.run_in_executor(None, _run_whisper), timeout=timeout_seconds)
        if transcript:
            return transcript.strip()
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"openai-whisper transcription failed: {e}")

    return None
