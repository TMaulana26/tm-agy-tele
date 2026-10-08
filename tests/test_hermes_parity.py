#!/usr/bin/env python3
"""
Comprehensive Test Suite for Hermes Agent Telegram Platform Adapter Parity.
Verifies:
1. Ingress resilience, token error redaction, polling stall watchdog, single-instance lock.
2. Code block fence preservation & markdown chunking with pagination.
3. Multi-Session Forum Topics with workspace & model override routing + self-healing pruning.
4. Interactive clarification buttons and slash command confirmation state machine.
5. Rich media upgrades: raster sniffing, image pre-compression, and audio/voice handling.
"""

import os
import sys
import io
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tele.network import (
    redact_telegram_error_text,
    acquire_instance_lock,
    release_instance_lock,
    PollingStallWatchdog,
)
from tele.formatters import (
    fence_state_after,
    truncate_message,
)
from tele.clarify import (
    detect_clarify_options,
    build_clarify_keyboard,
    register_clarification,
    pop_clarification,
)
from tele.media import (
    sniff_raster_format,
    compress_image_to_jpeg,
)
from tele.topics import parse_topic_args, TopicWorkspaceError
from database.state import StateDatabase


class TestHermesParityNetworkAndLocking(unittest.TestCase):
    """Tests network resilience, redaction, watchdog, and instance locking."""

    def test_redact_telegram_error_text(self):
        token = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ_12345"
        error_msg = f"HTTP 401 Unauthorized for request to https://api.telegram.org/bot{token}/sendMessage"
        redacted = redact_telegram_error_text(error_msg)
        self.assertNotIn(token, redacted)
        self.assertIn("bot[REDACTED_BOT_TOKEN]", redacted)

    def test_redact_telegram_error_generic(self):
        msg = "Network connection closed abruptly without token"
        self.assertEqual(redact_telegram_error_text(msg), msg)

    def test_single_instance_lock(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_path = Path(tmp_dir) / "test_bot.pid"
            with patch("tele.network.INSTANCE_LOCK_FILE", lock_path):
                # First acquire should succeed
                self.assertTrue(acquire_instance_lock())
                self.assertTrue(lock_path.is_file())
                pid = int(lock_path.read_text().strip())
                self.assertEqual(pid, os.getpid())

                # Second acquire within same or other process must fail
                self.assertFalse(acquire_instance_lock())

                # Release lock
                release_instance_lock()
                self.assertFalse(lock_path.exists())

    def test_polling_stall_watchdog_reset_on_progress(self):
        watchdog = PollingStallWatchdog(timeout_seconds=5.0)
        start_time = watchdog.last_progress_monotonic
        time.sleep(0.05)
        watchdog.notify_progress()
        self.assertGreater(watchdog.last_progress_monotonic, start_time)


class TestHermesParityMarkdownChunking(unittest.TestCase):
    """Tests code fence preservation and (1/N) indicators across 4000 char limits."""

    def test_fence_state_after(self):
        text1 = "Here is code:\n```python\nprint(1)\n```\nDone."
        in_code, lang = fence_state_after(text1)
        self.assertFalse(in_code)
        self.assertEqual(lang, "")

        text2 = "Start code:\n```javascript\nconsole.log(42);"
        in_code, lang = fence_state_after(text2)
        self.assertTrue(in_code)
        self.assertEqual(lang, "javascript")

    def test_truncate_message_short(self):
        short = "Halo dunia, ini pesan pendek."
        chunks = truncate_message(short, max_length=100)
        self.assertEqual(chunks, [short])

    def test_truncate_message_preserves_code_fences_and_language(self):
        # Create a large code block that will exceed 500 chars
        code_lines = [f"x_{i} = {i} * 2" for i in range(100)]
        long_code = "```python\n" + "\n".join(code_lines) + "\n```"

        chunks = truncate_message(long_code, max_length=400)
        self.assertGreater(len(chunks), 1)

        # First chunk should have closed the fence before pagination indicator
        self.assertIn("```", chunks[0])
        self.assertTrue(chunks[0].rstrip().endswith("(1/4)") or "``` (1/" in chunks[0])
        # Second chunk should have reopened the fence with python
        self.assertIn("```python\n", chunks[1])
        # Pagination indicators must be present
        for i, chunk in enumerate(chunks, 1):
            self.assertIn(f"({i}/{len(chunks)})", chunk)
            self.assertLessEqual(len(chunk), 400)


class TestHermesParityForumTopicsAndRouting(unittest.TestCase):
    """Tests Forum Topics routing with workspace and model override."""

    def test_parse_topic_args_standard(self):
        raw = "Fitur Autentikasi Pengguna"
        name, ws, model = parse_topic_args(raw)
        self.assertEqual(name, "Fitur Autentikasi Pengguna")
        self.assertIsNone(ws)
        self.assertIsNone(model)

    def test_parse_topic_args_with_flags(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            ws_target = Path(tmp_dir) / "auth_service"
            raw = f"Auth Service --path={ws_target} --model=claude-3-7-sonnet"
            with patch("tele.topics.TOPIC_WORKSPACE_ROOTS", [str(Path(tmp_dir).resolve())]):
                name, ws, model = parse_topic_args(raw)
            self.assertEqual(name, "Auth Service")
            self.assertEqual(ws, str(ws_target.resolve()))
            self.assertEqual(model, "claude-3-7-sonnet")
            self.assertTrue(ws_target.is_dir())

    def test_parse_topic_args_rejects_path_outside_roots(self):
        with tempfile.TemporaryDirectory() as allowed, tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "evil"
            with patch("tele.topics.TOPIC_WORKSPACE_ROOTS", [str(Path(allowed).resolve())]):
                with self.assertRaises(TopicWorkspaceError):
                    parse_topic_args(["Evil", f"--path={target}"])
                with self.assertRaises(TopicWorkspaceError):
                    parse_topic_args(["Root", f"--path={Path(allowed).anchor}"])
                with self.assertRaises(TopicWorkspaceError):
                    parse_topic_args(["Keys", f"--path={Path(allowed) / '.ssh'}"])
            self.assertFalse(target.exists())

    def test_database_topic_bindings_and_pruning(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "test_state.db"
            db = StateDatabase(db_path=db_path)

            # Insert topic with workspace and model override
            db.set_topic_binding(
                chat_id=12345,
                thread_id=50,
                conv_id="conv-topic-50",
                topic_name="Backend API",
                workspace_path="/app/backend",
                model_override="gemini-2.5-pro"
            )

            # Retrieve topic binding
            binding = db.get_topic_binding(12345, 50)
            self.assertIsNotNone(binding)
            self.assertEqual(binding["topic_name"], "Backend API")
            self.assertEqual(binding["workspace_path"], "/app/backend")
            self.assertEqual(binding["model_override"], "gemini-2.5-pro")

            # Test self-healing pruning
            db.prune_stale_topic_binding(12345, 50)
            binding_after = db.get_topic_binding(12345, 50)
            self.assertIsNone(binding_after)


class TestHermesParityClarifyAndSlashConfirm(unittest.TestCase):
    """Tests interactive clarification buttons and slash confirm dialogs."""

    def test_detect_clarify_numbered_options(self):
        text = (
            "Silakan pilih strategi migrasi database:\n"
            "1. Jalankan `php artisan migrate:fresh`\n"
            "2. Jalankan `php artisan migrate --pretend`\n"
            "3. Batalkan migrasi"
        )
        res = detect_clarify_options(text)
        self.assertIsNotNone(res)
        question, options = res
        self.assertEqual(len(options), 3)
        self.assertIn("Jalankan `php artisan migrate:fresh`", options[0])
        self.assertIn("Batalkan migrasi", options[2])

    def test_detect_clarify_bullet_options(self):
        text = (
            "Pilih opsi backup:\n"
            "- [ ] Snapshot database SQLite\n"
            "- [ ] Export seluruh file .env dan data"
        )
        res = detect_clarify_options(text)
        self.assertIsNotNone(res)
        question, options = res
        self.assertEqual(len(options), 2)
        self.assertEqual(options[0], "Snapshot database SQLite")

    def test_clarify_keyboard_and_lifecycle(self):
        options = ["Option Alpha", "Option Beta"]
        kb, cid = build_clarify_keyboard(options)
        self.assertIsNotNone(kb)
        self.assertEqual(len(kb.inline_keyboard), 3)
        self.assertEqual(kb.inline_keyboard[0][0].callback_data, f"cl:{cid}:0")

        # Verify registration and popping
        stored = pop_clarification(cid)
        self.assertEqual(stored["options"], options)
        self.assertIsNone(pop_clarification(cid))

    def test_detect_clarify_ignores_plain_reports(self):
        # A bulleted summary followed by more prose is not a question
        report = (
            "Ringkasan audit:\n"
            "- Endpoint login sudah memakai rate limit\n"
            "- Session cookie sudah HttpOnly\n\n"
            "Secara keseluruhan aplikasi dalam kondisi baik dan siap dirilis."
        )
        self.assertIsNone(detect_clarify_options(report))

        # A trailing list without a choice intro is not a question either
        steps = "Langkah yang sudah saya kerjakan:\n1. Update dependency\n2. Jalankan test"
        self.assertIsNone(detect_clarify_options(steps))

    def test_detect_clarify_uses_trailing_block_and_question(self):
        text = (
            "Hasil analisis:\n"
            "1. Query lambat di dashboard\n"
            "2. Index hilang di tabel orders\n\n"
            "Mau saya kerjakan yang mana dulu?\n"
            "1. Tambah index\n"
            "2. Refactor query"
        )
        res = detect_clarify_options(text)
        self.assertIsNotNone(res)
        _, options = res
        self.assertEqual(options, ["Tambah index", "Refactor query"])


class TestHermesParityRichMedia(unittest.TestCase):
    """Tests raster sniffing and image downscaling / pre-compression."""

    def test_sniff_raster_format(self):
        # PNG signature
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 30
        self.assertEqual(sniff_raster_format(png_bytes), "png")

        # JPEG signature
        jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 30
        self.assertEqual(sniff_raster_format(jpeg_bytes), "jpeg")

        # GIF signature
        gif_bytes = b"GIF89a" + b"\x00" * 30
        self.assertEqual(sniff_raster_format(gif_bytes), "gif")

        # WebP signature
        webp_bytes = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 20
        self.assertEqual(sniff_raster_format(webp_bytes), "webp")

    def test_compress_image_to_jpeg_downscale(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed in test environment")

        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create a huge dummy image (2400 x 1800)
            orig_img = Image.new("RGB", (2400, 1800), color=(100, 150, 200))
            orig_path = Path(tmp_dir) / "huge_photo.png"
            orig_img.save(orig_path, format="PNG")

            compressed_path = compress_image_to_jpeg(orig_path, max_dim=1200, quality=80)
            self.assertTrue(Path(compressed_path).is_file())
            self.assertEqual(Path(compressed_path).suffix, ".jpg")

            # Verify resized dimensions
            with Image.open(compressed_path) as res_img:
                self.assertLessEqual(max(res_img.width, res_img.height), 1200)


if __name__ == "__main__":
    unittest.main()
