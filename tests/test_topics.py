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

    async def test_handle_topics_command(self):
        from tele.topics import handle_topics_command
        chat_id = 999
        bind_conversation_to_topic(chat_id, 10, "conv-10", "Fix Login")
        bind_conversation_to_topic(chat_id, 20, "conv-20", "Update Docker")

        update = MagicMock()
        update.effective_chat.id = chat_id
        update.message = MagicMock()
        update.message.message_thread_id = None
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        await handle_topics_command(update, context)

        update.message.reply_text.assert_called_once()
        reply_content = update.message.reply_text.call_args[0][0]
        self.assertIn("Fix Login", reply_content)
        self.assertIn("Update Docker", reply_content)

    async def test_handle_title_command(self):
        from tele.topics import handle_title_command
        chat_id = 999
        thread_id = 55
        bind_conversation_to_topic(chat_id, thread_id, "conv-55", "Old Title")

        update = MagicMock()
        update.effective_chat.id = chat_id
        update.message = MagicMock()
        update.message.message_thread_id = thread_id
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.args = ["Brand", "New", "Title"]
        context.bot = AsyncMock()
        context.bot.edit_forum_topic = AsyncMock()

        await handle_title_command(update, context)

        context.bot.edit_forum_topic.assert_called_once_with(
            chat_id=chat_id, message_thread_id=thread_id, name="Brand New Title"
        )
        _, updated_name = get_conversation_for_message(chat_id, thread_id)
        self.assertEqual(updated_name, "Brand New Title")

    async def test_handle_delete_topic_command(self):
        from tele.topics import handle_delete_topic_command
        chat_id = 999
        thread_id = 88
        bind_conversation_to_topic(chat_id, thread_id, "conv-88", "Temporary Topic")

        update = MagicMock()
        update.effective_chat.id = chat_id
        update.message = MagicMock()
        update.message.message_thread_id = thread_id
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.bot = AsyncMock()
        context.bot.delete_forum_topic = AsyncMock()

        await handle_delete_topic_command(update, context)

        context.bot.delete_forum_topic.assert_called_once_with(
            chat_id=chat_id, message_thread_id=thread_id
        )
        conv, name = get_conversation_for_message(chat_id, thread_id)
        self.assertIsNone(conv)
        self.assertIsNone(name)


if __name__ == "__main__":
    unittest.main()
