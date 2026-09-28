#!/usr/bin/env python3
"""
Ingress Coalescing & Debouncing Engine.
1. TextDebouncer: Buffers rapid-fire consecutive text messages from the same user/thread within a sliding window.
2. MediaGroupCollector: Batches multi-photo albums sharing the same media_group_id into a single unified agent turn.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger("antigravity-tele-bot.coalescing")


class TextDebouncer:
    """
    Buffers and debounces rapid-fire text messages from the same (chat_id, user_id, thread_id).
    Combines fragmented thoughts into a single prompt before dispatching to the agent engine.
    """

    def __init__(
        self,
        debounce_seconds: float = 1.2,
        dispatch_callback: Optional[Callable[[Update, ContextTypes.DEFAULT_TYPE, str], Coroutine[Any, Any, None]]] = None,
        is_busy_func: Optional[Callable[[int], bool]] = None
    ):
        self.debounce_seconds = max(0.01, float(debounce_seconds))
        self.dispatch_callback = dispatch_callback
        self.is_busy_func = is_busy_func
        # State: key -> {"texts": [...], "update": Update, "context": Context, "timer_task": Task, "start_time": float}
        self._buffers: Dict[Tuple[int, int, Optional[int]], Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    def _make_key(self, chat_id: int, user_id: int, thread_id: Optional[int]) -> Tuple[int, int, Optional[int]]:
        return (chat_id, user_id, thread_id if isinstance(thread_id, int) else None)

    async def enqueue(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        text: str,
        chat_id: int,
        user_id: int,
        thread_id: Optional[int] = None
    ) -> bool:
        """
        Enqueues incoming text.
        If user is currently busy running a turn, returns False so caller can dispatch immediately.
        Otherwise buffers text and resets/extends the debounce timer.
        """
        if self.debounce_seconds <= 0:
            return False

        if self.is_busy_func and self.is_busy_func(user_id):
            return False

        key = self._make_key(chat_id, user_id, thread_id)
        async with self._lock:
            buf = self._buffers.get(key)
            if buf:
                # Cancel existing timer task
                old_timer = buf.get("timer_task")
                if old_timer and not old_timer.done():
                    old_timer.cancel()
                buf["texts"].append(text)
                buf["update"] = update
                buf["context"] = context
            else:
                buf = {
                    "texts": [text],
                    "update": update,
                    "context": context,
                    "start_time": time.time(),
                }
                self._buffers[key] = buf

            # Schedule new delayed flush task
            buf["timer_task"] = asyncio.create_task(self._delay_flush(key))
            return True

    async def _delay_flush(self, key: Tuple[int, int, Optional[int]]) -> None:
        try:
            await asyncio.sleep(self.debounce_seconds)
            await self._flush_key(key)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in text debounce timer for key {key}: {e}", exc_info=True)

    async def _flush_key(self, key: Tuple[int, int, Optional[int]]) -> None:
        buf = None
        async with self._lock:
            buf = self._buffers.pop(key, None)

        if not buf:
            return

        texts = buf.get("texts", [])
        if not texts:
            return

        combined_text = "\n\n".join(t.strip() for t in texts if t.strip())
        update = buf["update"]
        context = buf["context"]

        if self.dispatch_callback and combined_text:
            try:
                await self.dispatch_callback(update, context, combined_text)
            except Exception as e:
                logger.error(f"Error executing debounced dispatch callback: {e}", exc_info=True)

    async def cancel_all(self) -> None:
        """Cancels all active debounce timers on shutdown."""
        async with self._lock:
            for buf in self._buffers.values():
                timer = buf.get("timer_task")
                if timer and not timer.done():
                    timer.cancel()
            self._buffers.clear()


class MediaGroupCollector:
    """
    Batches multi-photo albums sharing the same media_group_id into a single unified agent turn.
    Collects photos over a short window, downloads them, and formats a combined prompt.
    """

    def __init__(
        self,
        window_seconds: float = 1.5,
        upload_dir_func: Optional[Callable[[], Path]] = None,
        dispatch_callback: Optional[Callable[[Update, ContextTypes.DEFAULT_TYPE, str], Coroutine[Any, Any, None]]] = None,
        is_busy_func: Optional[Callable[[int], bool]] = None
    ):
        self.window_seconds = max(0.01, float(window_seconds))
        self.upload_dir_func = upload_dir_func
        self.dispatch_callback = dispatch_callback
        self.is_busy_func = is_busy_func
        # State: (chat_id, media_group_id) -> {"paths": [...], "caption": str, "update": Update, "context": Context, "timer_task": Task}
        self._albums: Dict[Tuple[int, str], Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def enqueue(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        user_id: int,
        media_group_id: str
    ) -> bool:
        """
        Enqueues a photo from a media group album.
        Downloads photo file asynchronously and schedules batch dispatch.
        """
        if self.is_busy_func and self.is_busy_func(user_id):
            return False

        if not update.message or not update.message.photo:
            return False

        photo = update.message.photo[-1]
        upload_dir = self.upload_dir_func() if self.upload_dir_func else Path(".telegram_uploads")
        upload_dir.mkdir(parents=True, exist_ok=True)
        dest_filename = f"album_{media_group_id}_{int(time.time())}_{uuid.uuid4().hex[:4]}.jpg"
        dest_path = (upload_dir / dest_filename).resolve()

        # Download photo
        try:
            file_obj = await photo.get_file()
            await file_obj.download_to_drive(custom_path=dest_path)
            logger.info(f"Media group photo saved to: {dest_path}")
        except Exception as e:
            logger.error(f"Gagal mengunduh foto media group {media_group_id}: {e}", exc_info=True)
            return False

        key = (chat_id, media_group_id)
        caption = (update.message.caption or "").strip()

        async with self._lock:
            album = self._albums.get(key)
            if album:
                old_timer = album.get("timer_task")
                if old_timer and not old_timer.done():
                    old_timer.cancel()
                album["paths"].append(str(dest_path))
                if caption and not album.get("caption"):
                    album["caption"] = caption
                album["update"] = update
                album["context"] = context
            else:
                album = {
                    "paths": [str(dest_path)],
                    "caption": caption,
                    "update": update,
                    "context": context,
                    "start_time": time.time(),
                }
                self._albums[key] = album

            album["timer_task"] = asyncio.create_task(self._delay_flush(key))
            return True

    async def _delay_flush(self, key: Tuple[int, str]) -> None:
        try:
            await asyncio.sleep(self.window_seconds)
            await self._flush_album(key)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in media group timer for key {key}: {e}", exc_info=True)

    async def _flush_album(self, key: Tuple[int, str]) -> None:
        album = None
        async with self._lock:
            album = self._albums.pop(key, None)

        if not album:
            return

        paths = album.get("paths", [])
        if not paths:
            return

        caption = album.get("caption", "").strip()
        update = album["update"]
        context = album["context"]

        formatted_paths = "\n".join(f"- {p}" for p in paths)
        prompt_text = (
            f"[PENGGUNA MENGIRIMKAN ALBUM / KUMPULAN GAMBAR ({len(paths)} berkas)]\n"
            f"Berkas gambar telah disimpan di:\n"
            f"{formatted_paths}\n\n"
            f"[INSTRUKSI / CAPTION PENGGUNA]:\n"
            f"{caption if caption else 'Tolong periksa dan analisis kumpulan gambar terlampir ini.'}"
        )

        if self.dispatch_callback:
            try:
                await self.dispatch_callback(update, context, prompt_text)
            except Exception as e:
                logger.error(f"Error executing media group dispatch callback: {e}", exc_info=True)

    async def cancel_all(self) -> None:
        """Cancels all active media group timers on shutdown."""
        async with self._lock:
            for album in self._albums.values():
                timer = album.get("timer_task")
                if timer and not timer.done():
                    timer.cancel()
            self._albums.clear()
