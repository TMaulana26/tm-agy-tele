#!/usr/bin/env python3
"""Unit tests for Telegram entity link expansion & mention cleanup."""

import unittest
from unittest.mock import MagicMock

from tele.entities import expand_link_entities, clean_bot_mentions


class TestEntities(unittest.TestCase):

    def test_expand_link_entities_simple(self):
        msg = MagicMock()
        msg.text = "Kunjungi Google untuk info."
        entity = MagicMock()
        entity.type = "text_link"
        entity.offset = 9  # Index of 'Google'
        entity.length = 6
        entity.url = "https://google.com"
        msg.entities = [entity]

        expanded = expand_link_entities(msg)
        self.assertEqual(expanded, "Kunjungi Google (https://google.com) untuk info.")

    def test_expand_link_entities_with_emoji(self):
        # In UTF-16, emoji like 🤖 takes 2 code units
        msg = MagicMock()
        msg.text = "🤖 Cek GitHub kami."
        entity = MagicMock()
        entity.type = "text_link"
        entity.offset = 7  # 🤖 (2) + ' ' (1) + 'Cek ' (4) -> offset in code units
        entity.length = 6
        entity.url = "https://github.com"
        msg.entities = [entity]

        expanded = expand_link_entities(msg)
        self.assertIn("GitHub (https://github.com)", expanded)

    def test_expand_link_entities_no_entities(self):
        msg = MagicMock()
        msg.text = "Pesan teks biasa tanpa tautan."
        msg.entities = []
        expanded = expand_link_entities(msg)
        self.assertEqual(expanded, "Pesan teks biasa tanpa tautan.")

    def test_clean_bot_mentions(self):
        self.assertEqual(clean_bot_mentions("@antigravity_bot buatkan script", "antigravity_bot"), "buatkan script")
        self.assertEqual(clean_bot_mentions("Halo @my_bot periksa log"), "Halo  periksa log".strip())
        self.assertEqual(clean_bot_mentions("tidak ada mention"), "tidak ada mention")


if __name__ == "__main__":
    unittest.main()
