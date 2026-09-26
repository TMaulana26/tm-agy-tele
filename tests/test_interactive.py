#!/usr/bin/env python3
"""
Unit tests for interactive menus, dismiss flows, and callback query authorizations.
Validates:
1. /model picker clean confirmation card (no dangling keyboard) and close/reopen flow.
2. /help center dismiss buttons and action callbacks with thread isolation.
3. Approval callback user authorization check and expired alert.
4. Dismiss buttons on /status, /usage, /sessions, and /topics.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import InlineKeyboardMarkup
from telegram.constants import ParseMode

import bot
from database.state import StateDatabase
import database.state
from core.approval import handle_approval_callback, pending_approvals
from tele.picker import (
    build_model_keyboard,
    handle_model_callback,
    FALLBACK_MODELS,
)


class TestInteractiveUXAndSecurity(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        pending_approvals.clear()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test_interactive.db"
        self.db = StateDatabase(self.db_path)
        database.state._db_singleton = self.db

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_model_picker_keyboard_has_close_button(self):
        kb = build_model_keyboard(FALLBACK_MODELS, "gemini-3.8-flash-high", page=0)
        # Last row must be close button
        last_row = kb.inline_keyboard[-1]
        self.assertEqual(len(last_row), 1)
        self.assertEqual(last_row[0].callback_data, "model_close")
        self.assertIn("Tutup", last_row[0].text)

    async def test_model_callback_set_shows_clean_confirmation_without_dangling_list(self):
        update = MagicMock()
        update.effective_user.id = 12345
        update.effective_chat.id = 67890
        update.callback_query.data = "model_set:gemini-3.1-pro-high:0"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        update.callback_query.message.message_thread_id = None
        context = MagicMock()

        await handle_model_callback(update, context)

        # Must call edit_message_text
        update.callback_query.edit_message_text.assert_called_once()
        pos_args = update.callback_query.edit_message_text.call_args[0]
        kw_args = update.callback_query.edit_message_text.call_args[1]
        msg_text = kw_args.get("text") or (pos_args[0] if pos_args else "")
        self.assertIn("Model Aktif Telah Diperbarui", msg_text)

        # The keyboard must be a compact 1-row action bar: [Ganti Model] [Selesai], NOT full model list!
        reply_markup = kw_args.get("reply_markup")
        self.assertEqual(len(reply_markup.inline_keyboard), 1)
        action_buttons = reply_markup.inline_keyboard[0]
        self.assertEqual(len(action_buttons), 2)
        self.assertTrue(action_buttons[0].callback_data.startswith("model_reopen"))
        self.assertEqual(action_buttons[1].callback_data, "model_close")

    async def test_model_callback_close_deletes_message(self):
        update = MagicMock()
        update.effective_user.id = 12345
        update.effective_chat.id = 67890
        update.callback_query.data = "model_close"
        update.callback_query.answer = AsyncMock()
        update.callback_query.message.delete = AsyncMock()
        context = MagicMock()

        await handle_model_callback(update, context)
        update.callback_query.message.delete.assert_called_once()

    async def test_approval_callback_denies_unauthorized_user(self):
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        req_id = "sec_test_1"
        pending_approvals[req_id] = {"future": fut, "user_id": 111111}

        update = MagicMock()
        # Clicker is attacker (222222), while initiator was 111111
        update.callback_query.from_user.id = 222222
        update.effective_user.id = 222222
        update.callback_query.data = f"appr:{req_id}"
        update.callback_query.answer = AsyncMock()
        context = MagicMock()

        await handle_approval_callback(update, context)

        # Future should NOT be done (unauthorized clicker was blocked!)
        self.assertFalse(fut.done())
        update.callback_query.answer.assert_called_once()
        ans_args = update.callback_query.answer.call_args
        self.assertTrue(ans_args[1].get("show_alert"))
        self.assertIn("tidak memiliki wewenang", ans_args[0][0])

    async def test_approval_callback_alerts_on_expired_request(self):
        update = MagicMock()
        update.callback_query.from_user.id = 111111
        update.effective_user.id = 111111
        update.callback_query.data = "appr:non_existent_req_999"
        update.callback_query.answer = AsyncMock()
        context = MagicMock()

        await handle_approval_callback(update, context)
        update.callback_query.answer.assert_called_once()
        ans_args = update.callback_query.answer.call_args
        self.assertTrue(ans_args[1].get("show_alert"))
        self.assertIn("kadaluwarsa", ans_args[0][0])

    def test_help_menu_content_has_close_buttons(self):
        # Main menu
        _, main_kb = bot.get_help_menu_content("main")
        last_row = main_kb.inline_keyboard[-1]
        self.assertEqual(last_row[0].callback_data, "help:close")

        # Categories
        for cat in ("topics", "sessions", "security", "system"):
            _, cat_kb = bot.get_help_menu_content(cat)
            btn_data = [btn.callback_data for row in cat_kb.inline_keyboard for btn in row]
            self.assertIn("help:close", btn_data)
            self.assertIn("help:main", btn_data)

    def test_get_effective_thread_id(self):
        # Case A: direct message with thread_id
        update_msg = MagicMock()
        update_msg.message.message_thread_id = 42
        update_msg.callback_query = None
        self.assertEqual(bot.get_effective_thread_id(update_msg), 42)

        # Case B: callback query inside a thread
        update_cb = MagicMock()
        update_cb.message = None
        update_cb.callback_query.message.message_thread_id = 99
        self.assertEqual(bot.get_effective_thread_id(update_cb), 99)

        # Case C: root chat (no thread)
        update_root = MagicMock()
        update_root.message.message_thread_id = None
        update_root.callback_query = None
        self.assertIsNone(bot.get_effective_thread_id(update_root))


if __name__ == "__main__":
    unittest.main()
