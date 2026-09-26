#!/usr/bin/env python3
"""Unit tests for Media Security Path Traversal Guard & Media Classification."""

import os
import tempfile
import unittest
from pathlib import Path

from tele.media import (
    validate_media_delivery_path,
    classify_media_type,
    extract_media_paths,
)


class TestMediaGuard(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmpdir.name)
        # Create dummy workspace files
        self.safe_file = self.ws / "result.png"
        self.safe_file.write_text("dummy image content")

        self.voice_file = self.ws / "speech.ogg"
        self.voice_file.write_text("dummy audio content")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_safe_workspace_file_allowed(self):
        is_valid, reason = validate_media_delivery_path(str(self.safe_file), workspace_dir=str(self.ws))
        self.assertTrue(is_valid, f"Expected safe file to be allowed, got error: {reason}")
        self.assertEqual(reason, "")

    def test_nonexistent_file_rejected(self):
        bogus = self.ws / "non_existent.png"
        is_valid, reason = validate_media_delivery_path(str(bogus), workspace_dir=str(self.ws))
        self.assertFalse(is_valid)
        self.assertIn("does not exist", reason)

    def test_directory_rejected(self):
        sub = self.ws / "subdir"
        sub.mkdir()
        is_valid, reason = validate_media_delivery_path(str(sub), workspace_dir=str(self.ws))
        self.assertFalse(is_valid)
        self.assertIn("not a regular file", reason)

    def test_system_directory_blocked(self):
        # Linux system files
        blocked_targets = [
            "/etc/passwd",
            "/proc/cpuinfo",
            "/sys/class/net",
            "/root/.bashrc",
        ]
        for target in blocked_targets:
            is_valid, reason = validate_media_delivery_path(target, workspace_dir=str(self.ws))
            self.assertFalse(is_valid)

    def test_secret_patterns_blocked(self):
        (self.ws / ".config").mkdir(exist_ok=True)
        (self.ws / ".git").mkdir(exist_ok=True)
        secret_candidates = [
            self.ws / ".env",
            self.ws / ".env.production",
            self.ws / "state.db",
            self.ws / "id_rsa",
            self.ws / "credentials.json",
            self.ws / ".bash_history",
            self.ws / ".bashrc",
            self.ws / ".config" / "token.json",
            self.ws / ".git" / "config",
        ]
        for s in secret_candidates:
            s.write_text("secret")
            is_valid, reason = validate_media_delivery_path(str(s), workspace_dir=str(self.ws))
            self.assertFalse(is_valid, f"Expected {s.name} to be blocked as sensitive file!")

    def test_classify_media_type(self):
        self.assertEqual(classify_media_type("audio.ogg"), "voice")
        self.assertEqual(classify_media_type("audio.oga"), "voice")
        self.assertEqual(classify_media_type("track.mp3"), "audio")
        self.assertEqual(classify_media_type("track.wav"), "audio")
        self.assertEqual(classify_media_type("photo.jpg"), "photo")
        self.assertEqual(classify_media_type("photo.png"), "photo")
        self.assertEqual(classify_media_type("anim.gif"), "animation")
        self.assertEqual(classify_media_type("video.mp4"), "video")
        self.assertEqual(classify_media_type("doc.pdf"), "document")
        self.assertEqual(classify_media_type("archive.zip"), "document")

    def test_extract_media_paths(self):
        text = f"Berikut hasilnya:\nMEDIA:{self.safe_file}\nMEDIA: {self.voice_file}\ndan teks lain."
        extracted = extract_media_paths(text, workspace_dir=str(self.ws))
        self.assertEqual(len(extracted), 2)
        self.assertIn(str(self.safe_file.resolve()), extracted)
        self.assertIn(str(self.voice_file.resolve()), extracted)


if __name__ == "__main__":
    unittest.main()
