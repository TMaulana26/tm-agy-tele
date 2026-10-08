#!/usr/bin/env python3
"""
Regression tests for the ingress gate, callback-driven turns, the follow-up queue,
root conversation persistence, and transcript recovery isolation.
"""

import os
import json
import time
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import bot
import database.state
from database.state import StateDatabase
from telegram.ext import ApplicationHandlerStop
from tele import admission
from tele.clarify import register_clarification, get_clarification
from core import agy_engine
from core.approval import is_hardline_blocked, is_destructive_prompt

AUTHORIZED = 111111
OTHER_AUTHORIZED = 222222
STRANGER = 999999


def _message_update(user_id: int, text: str = "halo", update_id: int = 1) -> MagicMock:
    update = MagicMock()
    update.update_id = update_id
    update.effective_user.id = user_id
    update.effective_chat.id = 500
    update.callback_query = None
    update.message.text = text
    update.message.message_thread_id = None
    return update


class _IsolatedDbCase(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # config may already be loaded by another test module, so pin the whitelist here
        allowed = patch.object(bot, "ALLOWED_USER_IDS", {AUTHORIZED, OTHER_AUTHORIZED})
        allowed.start()
        self.addCleanup(allowed.stop)
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        database.state._db_singleton = StateDatabase(Path(self._tmp.name) / "state.db")
        admission._seen_in_memory.clear()
        for registry in (bot.user_conversations, bot.user_processes, bot.user_tasks,
                         bot.user_locks, bot.user_pending_prompts):
            registry.clear()

    def tearDown(self):
        database.state._db_singleton = None
        self._tmp.cleanup()


class TestIngressGate(_IsolatedDbCase):

    async def test_stranger_message_is_stopped(self):
        with self.assertRaises(ApplicationHandlerStop):
            await bot.ingress_gate(_message_update(STRANGER, "/topic X --path=/etc"), MagicMock())

    async def test_stranger_callback_is_stopped_with_alert(self):
        update = MagicMock()
        update.update_id = 7
        update.effective_user.id = STRANGER
        update.callback_query.answer = AsyncMock()
        with self.assertRaises(ApplicationHandlerStop):
            await bot.ingress_gate(update, MagicMock())
        update.callback_query.answer.assert_called_once()
        self.assertTrue(update.callback_query.answer.call_args.kwargs.get("show_alert"))

    async def test_stranger_start_passes_so_they_learn_their_id(self):
        await bot.ingress_gate(_message_update(STRANGER, "/start", update_id=11), MagicMock())

    async def test_authorized_update_passes_once_then_replay_is_dropped(self):
        context = MagicMock()
        context.bot.id = 42
        update = _message_update(AUTHORIZED, "halo", update_id=21)
        await bot.ingress_gate(update, context)
        with self.assertRaises(ApplicationHandlerStop):
            await bot.ingress_gate(update, context)

    async def test_gate_records_watchdog_progress(self):
        before = bot.stall_watchdog.last_progress_monotonic
        await asyncio.sleep(0.01)
        with self.assertRaises(ApplicationHandlerStop):
            await bot.ingress_gate(_message_update(STRANGER, "spam", update_id=31), MagicMock())
        self.assertGreater(bot.stall_watchdog.last_progress_monotonic, before)


class TestPollingRecovery(_IsolatedDbCase):

    async def test_restart_polling_restarts_updater(self):
        app = MagicMock()
        app.updater.running = True
        app.updater.stop = AsyncMock()
        app.updater.start_polling = AsyncMock()
        await bot._restart_polling(app)
        app.updater.stop.assert_awaited_once()
        app.updater.start_polling.assert_awaited_once()
        app.stop_running.assert_not_called()

    async def test_restart_failure_escalates_to_process_restart(self):
        app = MagicMock()
        app.updater.running = False
        app.updater.start_polling = AsyncMock(side_effect=RuntimeError("network down"))
        await bot._restart_polling(app)
        app.stop_running.assert_called_once()

    async def test_post_init_wires_watchdog_recovery(self):
        app = MagicMock()
        app.bot.set_my_commands = AsyncMock()
        with patch.object(bot.stall_watchdog, "start") as mock_start, \
             patch("bot._restart_polling", new=AsyncMock()) as mock_restart:
            await bot.post_init(app)
            mock_start.assert_called_once_with(app)
            await bot.stall_watchdog.on_stall_callback()
            mock_restart.assert_awaited_once_with(app)


class TestCallbackTurns(_IsolatedDbCase):

    def _callback_update(self, user_id: int, data: str, thread_id: int = 77) -> MagicMock:
        update = MagicMock()
        update.message = None
        update.effective_user.id = user_id
        update.effective_chat.id = 500
        update.callback_query.data = data
        update.callback_query.answer = AsyncMock()
        update.callback_query.message.message_thread_id = thread_id
        update.callback_query.message.edit_text = AsyncMock()
        return update

    async def test_clarify_choice_runs_in_the_topic_of_the_button(self):
        register_clarification("cid1", {"user_id": AUTHORIZED, "choices": ["Tambah index", "Refactor"]})
        update = self._callback_update(AUTHORIZED, "cl:cid1:0", thread_id=77)
        context = MagicMock()

        with patch("bot.safe_send_message", new=AsyncMock(return_value=AsyncMock())) as mock_send, \
             patch("bot.run_agy_cli", new=AsyncMock(return_value=("Selesai", "conv-topic"))) as mock_run, \
             patch("bot.auto_rename_forum_topic", new=AsyncMock()):
            await bot.handle_callback_query(update, context)
            await bot.user_tasks[AUTHORIZED]

        self.assertEqual(mock_run.call_args.kwargs["prompt"], "Tambah index")
        thread_ids = {c.kwargs.get("message_thread_id") for c in mock_send.call_args_list}
        self.assertEqual(thread_ids, {77})
        binding = database.state._db_singleton.get_topic_binding(500, 77)
        self.assertEqual(binding["conv_id"], "conv-topic")
        self.assertNotIn(AUTHORIZED, bot.user_conversations)

    async def test_clarify_choice_rejects_other_user(self):
        register_clarification("cid2", {"user_id": AUTHORIZED, "choices": ["A", "B"]})
        update = self._callback_update(OTHER_AUTHORIZED, "cl:cid2:1")

        with patch("bot._dispatch_agent_turn", new=AsyncMock()) as mock_dispatch:
            await bot.handle_callback_query(update, MagicMock())

        mock_dispatch.assert_not_called()
        self.assertIsNotNone(get_clarification("cid2"))
        self.assertTrue(update.callback_query.answer.call_args.kwargs.get("show_alert"))


class TestFollowUpQueue(_IsolatedDbCase):

    async def test_prompts_sent_while_busy_are_queued_and_merged(self):
        release = asyncio.Event()
        prompts = []

        async def fake_turn(update, context, prompt):
            prompts.append(prompt)
            if len(prompts) == 1:
                await release.wait()

        with patch("bot.execute_agent_turn", new=fake_turn), \
             patch("bot.safe_send_message", new=AsyncMock()) as mock_send:
            await bot._dispatch_agent_turn(_message_update(AUTHORIZED), MagicMock(), "tugas pertama")
            await asyncio.sleep(0)
            await bot._dispatch_agent_turn(_message_update(AUTHORIZED), MagicMock(), "tambahan satu")
            await bot._dispatch_agent_turn(_message_update(AUTHORIZED), MagicMock(), "tambahan dua")
            self.assertEqual(len(bot.user_pending_prompts[AUTHORIZED]), 2)
            self.assertIn("diantrikan", mock_send.call_args.args[2])

            release.set()
            for _ in range(10):
                await asyncio.sleep(0.01)

        self.assertEqual(prompts, ["tugas pertama", "tambahan satu\n\ntambahan dua"])
        self.assertNotIn(AUTHORIZED, bot.user_pending_prompts)

    async def test_cancel_discards_queue(self):
        bot.user_pending_prompts[AUTHORIZED] = [(MagicMock(), MagicMock(), "antre")]
        update = _message_update(AUTHORIZED)
        context = MagicMock()
        context.bot = AsyncMock()
        await bot.cancel_command(update, context)
        self.assertNotIn(AUTHORIZED, bot.user_pending_prompts)
        self.assertIn("berhasil dibatalkan", context.bot.send_message.call_args.kwargs["text"])


class TestRootConversationPersistence(_IsolatedDbCase):

    async def test_root_conversation_survives_restart(self):
        update = _message_update(AUTHORIZED)
        update.message.message_id = 10
        with patch("bot.safe_send_message", new=AsyncMock(return_value=AsyncMock())), \
             patch("bot.run_agy_cli", new=AsyncMock(return_value=("ok", "conv-root-1"))):
            await bot.execute_agent_turn(update, MagicMock(), "halo")

        bot.user_conversations.clear()  # simulate process restart
        self.assertEqual(bot.get_root_conversation(AUTHORIZED), "conv-root-1")

        with patch("bot.safe_send_message", new=AsyncMock(return_value=AsyncMock())), \
             patch("bot.run_agy_cli", new=AsyncMock(return_value=("ok", "conv-root-1"))) as mock_run:
            await bot.execute_agent_turn(update, MagicMock(), "lanjut")
        self.assertEqual(mock_run.call_args.kwargs["conv_id"], "conv-root-1")

        context = MagicMock()
        context.bot = AsyncMock()
        await bot.reset_command(update, context)
        bot.user_conversations.clear()
        self.assertIsNone(bot.get_root_conversation(AUTHORIZED))

    async def test_topic_without_binding_does_not_reuse_root_session(self):
        bot.set_root_conversation(AUTHORIZED, "conv-root-x")
        update = _message_update(AUTHORIZED)
        update.message.message_thread_id = 55
        with patch("bot.safe_send_message", new=AsyncMock(return_value=AsyncMock())), \
             patch("bot.auto_rename_forum_topic", new=AsyncMock()), \
             patch("bot.run_agy_cli", new=AsyncMock(return_value=("ok", "conv-topic-55"))) as mock_run:
            await bot.execute_agent_turn(update, MagicMock(), "halo topik")
        self.assertIsNone(mock_run.call_args.kwargs["conv_id"])
        self.assertEqual(bot.get_root_conversation(AUTHORIZED), "conv-root-x")


class TestTranscriptRecoveryIsolation(unittest.TestCase):

    def _write_transcript(self, brain: Path, conv_id: str, answer: str, mtime: float) -> None:
        log_dir = brain / conv_id / ".system_generated" / "logs"
        log_dir.mkdir(parents=True)
        path = log_dir / "transcript.jsonl"
        path.write_text("\n".join([
            json.dumps({"type": "USER_INPUT", "content": "q"}),
            json.dumps({"type": "PLANNER_RESPONSE", "content": answer}),
        ]), encoding="utf-8")
        os.utime(path, (mtime, mtime))

    def test_new_conversation_recovery_ignores_sessions_older_than_the_turn(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            brain = Path(tmpdir) / ".gemini" / "brain"
            turn_started = time.time()
            self._write_transcript(brain, "old-session", "jawaban sesi lain", turn_started - 3600)

            with patch("core.agy_engine.WORKSPACE_DIR", tmpdir), \
                 patch("core.agy_engine.list_brain_bases", return_value=[brain]):
                recovered, conv = agy_engine.recover_last_response_from_transcript(None, since=turn_started)
                self.assertIsNone(recovered)
                self.assertIsNone(conv)

                self._write_transcript(brain, "this-turn", "jawaban turn ini", turn_started + 1)
                recovered, conv = agy_engine.recover_last_response_from_transcript(None, since=turn_started)
                self.assertEqual(recovered, "jawaban turn ini")
                self.assertEqual(conv, "this-turn")


class TestGuardPrecision(unittest.TestCase):

    def test_power_words_in_prose_need_approval_instead_of_hard_block(self):
        prose = "kenapa server saya reboot sendiri tadi malam?"
        self.assertFalse(is_hardline_blocked(prose))
        self.assertTrue(is_destructive_prompt(prose)[0])

    def test_power_commands_in_command_position_stay_blocked(self):
        for cmd in ("sudo reboot", "cd /app && shutdown -h now", "systemctl poweroff", "echo ok\nreboot"):
            self.assertTrue(is_hardline_blocked(cmd), cmd)


if __name__ == "__main__":
    unittest.main()
