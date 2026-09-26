#!/usr/bin/env python3
"""Unit tests for Private Chat Topics (Forum Topics in DM - Bot API 9.4)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from database.state import StateDatabase
from tele.topics import (
    get_conversation_for_message,
    bind_conversation_to_topic,
    auto_rename_forum_topic,
)


class TestTopics(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test_topics.db"
        self.db = StateDatabase(self.db_path)
        import database.state
        database.state._db_singleton = self.db

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_root_dm_has_no_topic_binding(self):
        conv_id, name = get_conversation_for_message(chat_id=123, thread_id=None)
        self.assertIsNone(conv_id)
        self.assertIsNone(name)

    def test_bind_and_retrieve_topic_conversation(self):
        chat_id = 12345
        thread_id = 99
        bind_conversation_to_topic(chat_id, thread_id, "conv-thread-99", "Refactor Auth")

        ret_conv, ret_name = get_conversation_for_message(chat_id, thread_id)
        self.assertEqual(ret_conv, "conv-thread-99")
        self.assertEqual(ret_name, "Refactor Auth")

    def test_separate_topics_have_isolated_conversations(self):
        chat_id = 12345
        bind_conversation_to_topic(chat_id, thread_id=101, conv_id="conv-101", topic_name="Topik 1")
        bind_conversation_to_topic(chat_id, thread_id=102, conv_id="conv-102", topic_name="Topik 2")

        c1, _ = get_conversation_for_message(chat_id, 101)
        c2, _ = get_conversation_for_message(chat_id, 102)
        self.assertEqual(c1, "conv-101")
        self.assertEqual(c2, "conv-102")
        self.assertNotEqual(c1, c2)

    async def test_auto_rename_forum_topic(self):
        chat_id = 12345
        thread_id = 200
        # Register topic with default placeholder name
        bind_conversation_to_topic(chat_id, thread_id, "conv-200", "Topik Baru")

        mock_bot = AsyncMock()
        mock_bot.edit_forum_topic = AsyncMock()

        user_prompt = "Tolong migrasikan database schema PostgreSQL ke SQLite"
        ai_response = "Siap, skema telah dimigrasikan."

        await auto_rename_forum_topic(mock_bot, chat_id, thread_id, user_prompt, ai_response)

        mock_bot.edit_forum_topic.assert_called_once()
        call_kwargs = mock_bot.edit_forum_topic.call_args.kwargs
        self.assertEqual(call_kwargs["chat_id"], chat_id)
        self.assertEqual(call_kwargs["message_thread_id"], thread_id)
        self.assertIn("tolong migrasikan database schema postgresql", call_kwargs["name"].lower())

        # Verify updated in DB
        _, new_name = get_conversation_for_message(chat_id, thread_id)
        self.assertEqual(new_name, call_kwargs["name"])


if __name__ == "__main__":
    unittest.main()
