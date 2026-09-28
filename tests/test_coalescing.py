#!/usr/bin/env python3
"""
Unit tests for Ingress Coalescing (TextDebouncer & MediaGroupCollector)
and Group Chat Mention/Reply Gating.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from tele.coalescing import TextDebouncer, MediaGroupCollector
from tele.gating import should_process_chat_message


class TestCoalescing(unittest.IsolatedAsyncioTestCase):

    async def test_text_debouncer_single_message(self):
        dispatched = []

        async def mock_dispatch(update, context, prompt):
            dispatched.append(prompt)

        debouncer = TextDebouncer(debounce_seconds=0.1, dispatch_callback=mock_dispatch)
        mock_update = MagicMock()
        mock_context = MagicMock()

        enqueued = await debouncer.enqueue(mock_update, mock_context, "Halo bot", chat_id=100, user_id=200, thread_id=None)
        self.assertTrue(enqueued)
        self.assertEqual(len(dispatched), 0)

        # Wait for debounce timer to fire
        await asyncio.sleep(0.18)
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0], "Halo bot")

    async def test_text_debouncer_rapid_messages_coalesced(self):
        dispatched = []

        async def mock_dispatch(update, context, prompt):
            dispatched.append(prompt)

        debouncer = TextDebouncer(debounce_seconds=0.15, dispatch_callback=mock_dispatch)
        mock_update = MagicMock()
        mock_context = MagicMock()

        # Send 3 rapid messages within the debounce window
        await debouncer.enqueue(mock_update, mock_context, "Kang tolong cek file deploy.sh", chat_id=100, user_id=200)
        await asyncio.sleep(0.04)
        await debouncer.enqueue(mock_update, mock_context, "sekalian permission nya ya", chat_id=100, user_id=200)
        await asyncio.sleep(0.04)
        await debouncer.enqueue(mock_update, mock_context, "apakah ada yang 500 error", chat_id=100, user_id=200)

        self.assertEqual(len(dispatched), 0)

        # Wait for window to expire
        await asyncio.sleep(0.25)
        self.assertEqual(len(dispatched), 1)
        expected = "Kang tolong cek file deploy.sh\n\nsekalian permission nya ya\n\napakah ada yang 500 error"
        self.assertEqual(dispatched[0], expected)

    async def test_text_debouncer_different_threads_isolated(self):
        dispatched = []

        async def mock_dispatch(update, context, prompt):
            dispatched.append(prompt)

        debouncer = TextDebouncer(debounce_seconds=0.1, dispatch_callback=mock_dispatch)
        mock_update = MagicMock()
        mock_context = MagicMock()

        # Topic 1 and Topic 2 messages sent at same time
        await debouncer.enqueue(mock_update, mock_context, "Pesan Topic 1", chat_id=100, user_id=200, thread_id=1)
        await debouncer.enqueue(mock_update, mock_context, "Pesan Topic 2", chat_id=100, user_id=200, thread_id=2)

        await asyncio.sleep(0.18)
        self.assertEqual(len(dispatched), 2)
        self.assertIn("Pesan Topic 1", dispatched)
        self.assertIn("Pesan Topic 2", dispatched)

    async def test_text_debouncer_busy_user_bypasses(self):
        debouncer = TextDebouncer(
            debounce_seconds=0.1,
            dispatch_callback=AsyncMock(),
            is_busy_func=lambda uid: uid == 999
        )
        enqueued = await debouncer.enqueue(MagicMock(), MagicMock(), "test", chat_id=100, user_id=999)
        self.assertFalse(enqueued)

    async def test_text_debouncer_cancel_all(self):
        dispatched = []

        async def mock_dispatch(update, context, prompt):
            dispatched.append(prompt)

        debouncer = TextDebouncer(debounce_seconds=0.2, dispatch_callback=mock_dispatch)
        await debouncer.enqueue(MagicMock(), MagicMock(), "Pesan batal", chat_id=100, user_id=200)
        await debouncer.cancel_all()

        await asyncio.sleep(0.25)
        self.assertEqual(len(dispatched), 0)

    async def test_media_group_collector_batches_album(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            dispatched = []

            async def mock_dispatch(update, context, prompt):
                dispatched.append(prompt)

            collector = MediaGroupCollector(
                window_seconds=0.15,
                upload_dir_func=lambda: tmppath,
                dispatch_callback=mock_dispatch
            )

            # Create mock updates for 2 photos in same album
            mock_file1 = AsyncMock()
            async def fake_dl1(custom_path):
                Path(custom_path).write_text("photo1")
            mock_file1.download_to_drive.side_effect = fake_dl1

            mock_photo1 = MagicMock()
            mock_photo1.get_file = AsyncMock(return_value=mock_file1)

            update1 = MagicMock()
            update1.message.photo = [mock_photo1]
            update1.message.caption = "Cek log screenshot ini kang"
            update1.message.media_group_id = "album_123"

            mock_file2 = AsyncMock()
            async def fake_dl2(custom_path):
                Path(custom_path).write_text("photo2")
            mock_file2.download_to_drive.side_effect = fake_dl2

            mock_photo2 = MagicMock()
            mock_photo2.get_file = AsyncMock(return_value=mock_file2)

            update2 = MagicMock()
            update2.message.photo = [mock_photo2]
            update2.message.caption = None
            update2.message.media_group_id = "album_123"

            # Enqueue both
            enq1 = await collector.enqueue(update1, MagicMock(), chat_id=100, user_id=200, media_group_id="album_123")
            self.assertTrue(enq1)
            enq2 = await collector.enqueue(update2, MagicMock(), chat_id=100, user_id=200, media_group_id="album_123")
            self.assertTrue(enq2)

            self.assertEqual(len(dispatched), 0)

            # Wait for album window to fire
            await asyncio.sleep(0.25)
            self.assertEqual(len(dispatched), 1)
            prompt = dispatched[0]
            self.assertIn("PENGGUNA MENGIRIMKAN ALBUM", prompt)
            self.assertIn("2 berkas", prompt)
            self.assertIn("Cek log screenshot ini kang", prompt)


class TestGroupGating(unittest.TestCase):

    def test_private_chat_always_allowed(self):
        update = MagicMock()
        update.effective_chat.type = "private"
        update.effective_message.text = "halo"
        should_proc, cleaned = should_process_chat_message(
            update, bot_id=123, bot_username="my_bot", require_mention=True, text="halo"
        )
        self.assertTrue(should_proc)
        self.assertEqual(cleaned, "halo")

    def test_group_without_mention_rejected(self):
        update = MagicMock()
        update.effective_chat.type = "group"
        update.effective_message.text = "ngobrol antar anggota"
        update.effective_message.reply_to_message = None
        update.effective_message.entities = []
        should_proc, _ = should_process_chat_message(
            update, bot_id=123, bot_username="my_bot", require_mention=True, text="ngobrol antar anggota"
        )
        self.assertFalse(should_proc)

    def test_group_with_mention_allowed_and_cleaned(self):
        update = MagicMock()
        update.effective_chat.type = "supergroup"
        update.effective_message.text = "@my_bot tolong deploy sekarang"
        update.effective_message.reply_to_message = None
        update.effective_message.entities = []
        should_proc, cleaned = should_process_chat_message(
            update, bot_id=123, bot_username="my_bot", require_mention=True, text="@my_bot tolong deploy sekarang"
        )
        self.assertTrue(should_proc)
        self.assertEqual(cleaned, "tolong deploy sekarang")

    def test_group_with_reply_to_bot_allowed(self):
        update = MagicMock()
        update.effective_chat.type = "group"
        update.effective_message.text = "ini tanggapan saya"
        reply_user = MagicMock()
        reply_user.is_bot = True
        reply_user.id = 123
        update.effective_message.reply_to_message.from_user = reply_user
        update.effective_message.entities = []

        should_proc, cleaned = should_process_chat_message(
            update, bot_id=123, bot_username="my_bot", require_mention=True, text="ini tanggapan saya"
        )
        self.assertTrue(should_proc)
        self.assertEqual(cleaned, "ini tanggapan saya")

    def test_group_with_reply_to_human_rejected(self):
        update = MagicMock()
        update.effective_chat.type = "group"
        update.effective_message.text = "halo bro"
        reply_user = MagicMock()
        reply_user.is_bot = False
        reply_user.id = 999
        update.effective_message.reply_to_message.from_user = reply_user
        update.effective_message.entities = []

        should_proc, _ = should_process_chat_message(
            update, bot_id=123, bot_username="my_bot", require_mention=True, text="halo bro"
        )
        self.assertFalse(should_proc)


if __name__ == "__main__":
    unittest.main()
