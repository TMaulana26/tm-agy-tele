"""
Comprehensive Unit Tests for Antigravity Telegram Bot (bot.py)
Tests all security mechanisms, prompt categorization, approval workflows,
subprocess execution, cancellation, message chunking, and command handlers.
"""

import os
import sys
import json
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

# Set dummy env vars for test environment before importing bot
os.environ["TELEGRAM_BOT_TOKEN"] = "123456789:ABCDEF_TEST_TOKEN"
os.environ["ALLOWED_USER_ID"] = "111111,222222"
os.environ["WORKSPACE_DIR"] = "/tmp/test_workspace"
os.environ["APPROVAL_MODE"] = "ask_destructive"
os.environ["APPROVAL_TIMEOUT_SECONDS"] = "2"
os.environ["AGY_BIN_PATH"] = "agy"

import bot

class TestAntigravityBot(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        bot.pending_approvals.clear()
        bot.user_conversations.clear()
        bot.user_processes.clear()
        bot.user_tasks.clear()
        bot.user_locks.clear()

    # --------------------------------------------------------------------------
    # 1. MESSAGE CHUNKING & SPLITTING
    # --------------------------------------------------------------------------
    def test_split_message_empty(self):
        chunks = bot.split_message("")
        self.assertEqual(chunks, ["(Tidak ada output teks dari agy)"])

    def test_split_message_short(self):
        text = "Hello world! This is a test."
        chunks = bot.split_message(text)
        self.assertEqual(chunks, [text])

    def test_split_message_long_newlines(self):
        lines = [f"Line {i} of code output with data" for i in range(500)]
        long_text = "\n".join(lines)
        chunks = bot.split_message(long_text, max_length=1000)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 1000)
        # Verify content completeness
        reconstructed = "\n".join(chunks)
        self.assertEqual(reconstructed.replace("\r", ""), long_text.replace("\r", ""))

    def test_split_message_no_newlines(self):
        long_unbroken = "a" * 3500
        chunks = bot.split_message(long_unbroken, max_length=1000)
        self.assertEqual(len(chunks), 4)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 1000)
        self.assertEqual("".join(chunks), long_unbroken)

    # --------------------------------------------------------------------------
    # 2. HARDLINE SECURITY BLOCKLIST
    # --------------------------------------------------------------------------
    def test_hardline_blocklist(self):
        blocked_commands = [
            "rm -rf /",
            "rm -rf /*",
            "rm -fr /",
            ":(){ :|:& };:",
            "mkfs.ext4 /dev/sda1",
            "mkfs /dev/sdb",
            "dd if=/dev/zero of=/dev/sda bs=1M",
            "fdisk /dev/nvme0n1",
            "> /dev/sda",
            "chmod -R 777 /",
            "chown -R root /",
            "shutdown -h now",
            "reboot",
            "init 0",
        ]
        for cmd in blocked_commands:
            self.assertTrue(bot.is_hardline_blocked(cmd), f"Expected '{cmd}' to be hardline blocked!")

        allowed_commands = [
            "rm file.txt",
            "rm -rf ./build",
            "chmod +x run.sh",
            "git status",
            "echo rebooting the server",
            "cat /etc/hosts",
            "python test.py",
        ]
        for cmd in allowed_commands:
            self.assertFalse(bot.is_hardline_blocked(cmd), f"Expected '{cmd}' NOT to be hardline blocked!")

    # --------------------------------------------------------------------------
    # 3. DESTRUCTIVE INTENT GUARD
    # --------------------------------------------------------------------------
    def test_is_destructive_prompt(self):
        destructive_prompts = [
            "tolong hapus file database.sqlite",
            "delete the build directory",
            "remove temporary logs",
            "rm -rf temp_data",
            "drop database production",
            "drop table users",
            "git reset --hard HEAD~1",
            "git push origin main --force",
            "docker rm -f container_test",
            "docker system prune",
            "kill -9 1234",
            "format disk /dev/sdb",
        ]
        for prompt in destructive_prompts:
            is_destruct, reason = bot.is_destructive_prompt(prompt)
            self.assertTrue(is_destruct, f"Expected '{prompt}' to be detected as destructive!")
            self.assertTrue(len(reason) > 0)

        safe_prompts = [
            "halo kang",
            "cek folder saat ini",
            "buatkan fungsi kalkulator di python",
            "baca file README.md",
            "git status",
            "ls -la",
            "analisis kode di folder src",
        ]
        for prompt in safe_prompts:
            is_destruct, _ = bot.is_destructive_prompt(prompt)
            self.assertFalse(is_destruct, f"Expected '{prompt}' NOT to be detected as destructive!")

    # --------------------------------------------------------------------------
    # 4. MEDIA PATH EXTRACTION
    # --------------------------------------------------------------------------
    def test_extract_media_paths(self):
        temp_file = Path("test_artifact_media.png")
        temp_file.write_text("dummy")

        try:
            sample_text = f"""
            Berikut gambar diagram arsitektur:
            MEDIA:{temp_file.name}
            Dan file yang tidak ada:
            MEDIA:/path/to/non_existent.txt
            """
            extracted = bot.extract_media_paths(sample_text, workspace_dir=".")
            self.assertEqual(len(extracted), 1)
            self.assertTrue(extracted[0].endswith("test_artifact_media.png"))
        finally:
            if temp_file.exists():
                temp_file.unlink()

    # --------------------------------------------------------------------------
    # 5. AUTHORIZATION
    # --------------------------------------------------------------------------
    def test_is_authorized(self):
        update_auth = MagicMock()
        update_auth.effective_user.id = 111111
        self.assertTrue(bot.is_authorized(update_auth))

        update_auth2 = MagicMock()
        update_auth2.effective_user.id = 222222
        self.assertTrue(bot.is_authorized(update_auth2))

        update_unauth = MagicMock()
        update_unauth.effective_user.id = 999999
        self.assertFalse(bot.is_authorized(update_unauth))

        update_none = MagicMock()
        update_none.effective_user = None
        self.assertFalse(bot.is_authorized(update_none))

    # --------------------------------------------------------------------------
    # 6. INTERACTIVE APPROVAL WORKFLOW
    # --------------------------------------------------------------------------
    async def test_approval_workflow_approved(self):
        mock_bot = AsyncMock()
        mock_msg = AsyncMock()
        mock_bot.send_message.return_value = mock_msg

        async def _approve_later():
            await asyncio.sleep(0.05)
            self.assertEqual(len(bot.pending_approvals), 1)
            req_id = list(bot.pending_approvals.keys())[0]
            bot.pending_approvals[req_id]["future"].set_result(True)

        asyncio.create_task(_approve_later())
        result = await bot.request_user_approval(
            bot=mock_bot,
            chat_id=123,
            user_id=111111,
            prompt="rm -rf ./build",
            reason="Terdeteksi perintah rm"
        )
        self.assertTrue(result)
        self.assertEqual(len(bot.pending_approvals), 0)

    async def test_approval_workflow_denied(self):
        mock_bot = AsyncMock()
        mock_msg = AsyncMock()
        mock_bot.send_message.return_value = mock_msg

        async def _deny_later():
            await asyncio.sleep(0.05)
            req_id = list(bot.pending_approvals.keys())[0]
            bot.pending_approvals[req_id]["future"].set_result(False)

        asyncio.create_task(_deny_later())
        result = await bot.request_user_approval(
            bot=mock_bot,
            chat_id=123,
            user_id=111111,
            prompt="drop database test",
            reason="Terdeteksi drop database"
        )
        self.assertFalse(result)
        self.assertEqual(len(bot.pending_approvals), 0)

    async def test_approval_workflow_timeout(self):
        mock_bot = AsyncMock()
        mock_msg = AsyncMock()
        mock_bot.send_message.return_value = mock_msg

        with patch.object(bot, "APPROVAL_TIMEOUT_SECONDS", 0.1):
            result = await bot.request_user_approval(
                bot=mock_bot,
                chat_id=123,
                user_id=111111,
                prompt="git reset --hard",
                reason="Terdeteksi git reset"
            )
            self.assertFalse(result)
            self.assertEqual(len(bot.pending_approvals), 0)

    # --------------------------------------------------------------------------
    # 7. SUBPROCESS AGY CLI RUNNER
    # --------------------------------------------------------------------------
    async def test_run_agy_cli_json_success(self):
        mock_proc = AsyncMock()
        json_output = json.dumps({
            "conversation_id": "conv-uuid-12345",
            "status": "SUCCESS",
            "response": "Halo! Tugas telah selesai.",
            "usage": {"total_tokens": 150}
        }).encode("utf-8")

        mock_proc.communicate.return_value = (json_output, b"")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("os.path.exists", return_value=True):
                resp, conv_id = await bot.run_agy_cli(
                    user_id=111111,
                    prompt="test prompt",
                    conv_id=None,
                    cwd="."
                )
                self.assertEqual(resp, "Halo! Tugas telah selesai.")
                self.assertEqual(conv_id, "conv-uuid-12345")

    async def test_run_agy_cli_json_error(self):
        mock_proc = AsyncMock()
        json_output = json.dumps({
            "conversation_id": "conv-uuid-12345",
            "status": "ERROR",
            "response": "",
            "error": "Failed to compile project"
        }).encode("utf-8")

        mock_proc.communicate.return_value = (json_output, b"")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("os.path.exists", return_value=True):
                resp, conv_id = await bot.run_agy_cli(
                    user_id=111111,
                    prompt="compile",
                    conv_id="conv-uuid-12345",
                    cwd="."
                )
                self.assertIn("Error dari agy", resp)
                self.assertIn("Failed to compile project", resp)
                self.assertEqual(conv_id, "conv-uuid-12345")

    async def test_run_agy_cli_fallback_text(self):
        mock_proc = AsyncMock()
        raw_text = b"PONG\nProcess completed."
        mock_proc.communicate.return_value = (raw_text, b"")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("os.path.exists", return_value=True):
                resp, conv_id = await bot.run_agy_cli(
                    user_id=111111,
                    prompt="ping",
                    conv_id="conv-999",
                    cwd="."
                )
                self.assertIn("PONG", resp)
                self.assertEqual(conv_id, "conv-999")

    # --------------------------------------------------------------------------
    # 8. COMMAND HANDLERS
    # --------------------------------------------------------------------------
    async def test_start_command(self):
        update = MagicMock()
        update.effective_user.id = 111111
        update.effective_chat.id = 123
        context = MagicMock()
        context.bot = AsyncMock()

        await bot.start_command(update, context)
        context.bot.send_message.assert_called_once()
        args, kwargs = context.bot.send_message.call_args
        self.assertIn("Halo Kang", kwargs.get("text", ""))

    async def test_status_command(self):
        update = MagicMock()
        update.effective_user.id = 111111
        update.effective_chat.id = 123
        context = MagicMock()
        context.bot = AsyncMock()

        await bot.status_command(update, context)
        context.bot.send_message.assert_called_once()
        args, kwargs = context.bot.send_message.call_args
        self.assertIn("Status Sistem Antigravity Bot", kwargs.get("text", ""))

    async def test_reset_command(self):
        update = MagicMock()
        update.effective_user.id = 111111
        update.effective_chat.id = 123
        context = MagicMock()
        context.bot = AsyncMock()

        bot.user_conversations[111111] = "session-123"
        mock_proc = MagicMock()
        mock_proc.returncode = None
        bot.user_processes[111111] = mock_proc

        await bot.reset_command(update, context)
        self.assertNotIn(111111, bot.user_conversations)
        mock_proc.terminate.assert_called_once()
        context.bot.send_message.assert_called_once()
        args, kwargs = context.bot.send_message.call_args
        self.assertIn("Sesi Percakapan Direset", kwargs.get("text", ""))

    async def test_cancel_command(self):
        update = MagicMock()
        update.effective_user.id = 111111
        update.effective_chat.id = 123
        context = MagicMock()
        context.bot = AsyncMock()

        # 1. No task running
        await bot.cancel_command(update, context)
        context.bot.send_message.assert_called_once()
        args, kwargs = context.bot.send_message.call_args
        self.assertIn("Tidak ada tugas", kwargs.get("text", ""))

        # 2. Running subprocess
        context.bot.reset_mock()
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.pid = 9999
        bot.user_processes[111111] = mock_proc

        dummy_task = asyncio.create_task(asyncio.sleep(10))
        bot.user_tasks[111111] = dummy_task

        await bot.cancel_command(update, context)
        # Yield to allow task cancellation to finalize
        await asyncio.sleep(0.01)
        mock_proc.terminate.assert_called_once()
        self.assertTrue(dummy_task.cancelled())
        context.bot.send_message.assert_called_once()
        args, kwargs = context.bot.send_message.call_args
        self.assertIn("berhasil dibatalkan", kwargs.get("text", ""))

    # --------------------------------------------------------------------------
    # 9. SAFE MARKDOWN FALLBACK
    # --------------------------------------------------------------------------
    async def test_safe_send_message_fallback_on_bad_request(self):
        mock_bot = AsyncMock()
        mock_bot.send_message.side_effect = [
            bot.BadRequest("Can't find end of entities"),
            MagicMock(message_id=99)
        ]
        res = await bot.safe_send_message(mock_bot, 123, "Unclosed *markdown")
        self.assertIsNotNone(res)
        self.assertEqual(mock_bot.send_message.call_count, 2)
        self.assertIsNone(mock_bot.send_message.call_args_list[1].kwargs.get("parse_mode"))

    async def test_safe_edit_message_fallback_on_bad_request(self):
        mock_msg = AsyncMock()
        mock_msg.edit_text.side_effect = [
            bot.BadRequest("Can't find end of entities"),
            True
        ]
        res = await bot.safe_edit_message(mock_msg, "Broken _entity")
        self.assertTrue(res)
        self.assertEqual(mock_msg.edit_text.call_count, 2)
        self.assertIsNone(mock_msg.edit_text.call_args_list[1].kwargs.get("parse_mode"))

    # --------------------------------------------------------------------------
    # 10. CALLBACK QUERY HANDLER
    # --------------------------------------------------------------------------
    async def test_handle_callback_query(self):
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        req_id = "test_123"
        bot.pending_approvals[req_id] = {"future": fut, "user_id": 111111}

        update = MagicMock()
        update.effective_user.id = 111111
        update.callback_query.data = f"appr:{req_id}"
        update.callback_query.answer = AsyncMock()
        context = MagicMock()

        await bot.handle_callback_query(update, context)
        self.assertTrue(fut.done())
        self.assertTrue(fut.result())

if __name__ == "__main__":
    unittest.main()
