#!/usr/bin/env python3
"""Unit tests for Anti-Replay Update Admission Control."""

import tempfile
import unittest
from pathlib import Path

from database.state import StateDatabase, get_db
from tele.admission import is_update_admitted, _seen_in_memory


class TestAdmission(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test_admission.db"
        self.db = StateDatabase(self.db_path)
        # Point global db singleton to test db
        import database.state
        database.state._db_singleton = self.db
        _seen_in_memory.clear()

    def tearDown(self):
        _seen_in_memory.clear()
        self.tmpdir.cleanup()

    def test_first_time_update_admitted(self):
        bot_id = "bot123"
        update_id = 99901
        self.assertTrue(is_update_admitted(bot_id, update_id))

    def test_duplicate_update_dropped_in_memory(self):
        bot_id = "bot123"
        update_id = 99902
        self.assertTrue(is_update_admitted(bot_id, update_id))
        # Second attempt immediately should be dropped by in-memory cache
        self.assertFalse(is_update_admitted(bot_id, update_id))

    def test_duplicate_update_dropped_from_disk_receipts(self):
        bot_id = "bot123"
        update_id = 99903
        self.assertTrue(is_update_admitted(bot_id, update_id))

        # Clear in-memory cache to simulate bot restart
        _seen_in_memory.clear()

        # Database receipt should drop the update
        self.assertFalse(is_update_admitted(bot_id, update_id))

    def test_different_bots_independent(self):
        update_id = 99904
        self.assertTrue(is_update_admitted("bot_A", update_id))
        self.assertTrue(is_update_admitted("bot_B", update_id))


if __name__ == "__main__":
    unittest.main()
