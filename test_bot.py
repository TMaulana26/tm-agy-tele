"""
Comprehensive Unit Tests for Antigravity Telegram Bot (bot.py)
Tests all security mechanisms, command categorization, approval workflows,
message chunking, media extraction, and command handlers.
"""

import os
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

import bot

class TestAntigravityBot(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        bot.pending_approvals.clear()
        bot.user_agents.clear()
        bot.user_approval_hooks.clear()
        bot.user_tasks.clear()
        bot.user_locks.clear()

    # --------------------------------------------------------------------------
    # 1. MESSAGE CHUNKING & SPLITTING
    # --------------------------------------------------------------------------
    def test_split_message_empty(self):
        chunks = bot.split_message("")
        self.assertEqual(chunks, ["(Tidak ada output)"])

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
            "rm -r -f /",
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
    # 3. COMMAND SAFETY CLASSIFICATION
    # --------------------------------------------------------------------------
    def test_is_command_safe(self):
        # Safe read-only commands
        self.assertTrue(bot.is_command_safe("ls -la"))
        self.assertTrue(bot.is_command_safe("pwd"))
        self.assertTrue(bot.is_command_safe("git status"))
        self.assertTrue(bot.is_command_safe("git log -n 5"))
        self.assertTrue(bot.is_command_safe("git diff HEAD~1"))
        self.assertTrue(bot.is_command_safe("cat README.md"))
        self.assertTrue(bot.is_command_safe("cat file.txt | grep error"))
        self.assertTrue(bot.is_command_safe("ls && pwd"))

        # Mutative / unsafe commands
        self.assertFalse(bot.is_command_safe("rm test.txt"))
        self.assertFalse(bot.is_command_safe("echo 'data' > output.txt"))
        self.assertFalse(bot.is_command_safe("ls && rm -rf temp"))
        self.assertFalse(bot.is_command_safe("python run_migration.py"))
        self.assertFalse(bot.is_command_safe("git push origin main"))

    # --------------------------------------------------------------------------
    # 4. DESTRUCTIVE ACTION DETECTOR
    # --------------------------------------------------------------------------
    def test_is_destructive_action(self):
        # File mutative tools
        destruct, _ = bot.is_destructive_action("create_file", {"TargetFile": "a.py"})
        self.assertTrue(destruct)

        destruct, _ = bot.is_destructive_action("edit_file", {"TargetFile": "a.py"})
        self.assertTrue(destruct)

        destruct, _ = bot.is_destructive_action("write_to_file", {"TargetFile": "a.py"})
        self.assertTrue(destruct)

        # Terminal safe vs destructive
        destruct, _ = bot.is_destructive_action("run_command", {"command": "ls -l"})
        self.assertFalse(destruct)

        destruct, _ = bot.is_destructive_action("run_command", {"command": "pip install foo"})
        self.assertTrue(destruct)

        # Read only tools
        destruct, _ = bot.is_destructive_action("view_file", {"file_path": "a.py"})
        self.assertFalse(destruct)

    # --------------------------------------------------------------------------
    # 5. MEDIA PATH EXTRACTION
    # --------------------------------------------------------------------------
    def test_extract_media_paths(self):
        # Create a temp file to test detection
        temp_file = Path("test_artifact.png")
        temp_file.write_text("dummy")

        try:
            sample_text = f"""
            Saya telah membuat diagram arsitektur:
            MEDIA:{temp_file.name}
            Juga file yang tidak ada:
            MEDIA:/path/to/non_existent.txt
            """
            extracted = bot.extract_media_paths(sample_text, workspace_dir=".")
            self.assertEqual(len(extracted), 1)
            self.assertTrue(extracted[0].endswith("test_artifact.png"))
        finally:
            if temp_file.exists():
                temp_file.unlink()

    # --------------------------------------------------------------------------
    # 6. AUTHORIZATION
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
    # 7. INTERACTIVE APPROVAL WORKFLOW
    # --------------------------------------------------------------------------
    async def test_approval_workflow_approved(self):
        mock_bot = AsyncMock()
        mock_msg = AsyncMock()
        mock_bot.send_message.return_value = mock_msg

        # Task to approve
        async def _approve_later():
            await asyncio.sleep(0.05)
            # Find the pending request
            self.assertEqual(len(bot.pending_approvals), 1)
            req_id = list(bot.pending_approvals.keys())[0]
            bot.pending_approvals[req_id]["future"].set_result(True)

        asyncio.create_task(_approve_later())
        result = await bot.request_user_approval(
            bot=mock_bot,
            chat_id=123,
            user_id=111111,
            action_name="run_command",
            action_details="npm install"
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
            action_name="run_command",
            action_details="rm test.txt"
        )
        self.assertFalse(result)
        self.assertEqual(len(bot.pending_approvals), 0)

    async def test_approval_workflow_timeout(self):
        mock_bot = AsyncMock()
        mock_msg = AsyncMock()
        mock_bot.send_message.return_value = mock_msg

        # Patch timeout to very short (0.1s)
        with patch.object(bot, "APPROVAL_TIMEOUT_SECONDS", 0.1):
            result = await bot.request_user_approval(
                bot=mock_bot,
                chat_id=123,
                user_id=111111,
                action_name="run_command",
                action_details="git push"
            )
            self.assertFalse(result)
            self.assertEqual(len(bot.pending_approvals), 0)

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

    async def test_cancel_command(self):
        update = MagicMock()
        update.effective_user.id = 111111
        update.effective_chat.id = 123
        context = MagicMock()
        context.bot = AsyncMock()

        # When no task is running
        await bot.cancel_command(update, context)
        context.bot.send_message.assert_called_once()
        args, kwargs = context.bot.send_message.call_args
        self.assertIn("Tidak ada tugas", kwargs.get("text", ""))

        # When a task is running
        context.bot.reset_mock()
        dummy_task = asyncio.create_task(asyncio.sleep(10))
        bot.user_tasks[111111] = dummy_task

        await bot.cancel_command(update, context)
        # Yield to allow task cancellation to finalize
        await asyncio.sleep(0.01)
        self.assertTrue(dummy_task.cancelled())
        context.bot.send_message.assert_called_once()
        args, kwargs = context.bot.send_message.call_args
        self.assertIn("berhasil dibatalkan", kwargs.get("text", ""))

    async def test_reset_command(self):
        update = MagicMock()
        update.effective_user.id = 111111
        update.effective_chat.id = 123
        context = MagicMock()
        context.bot = AsyncMock()

        mock_agent = AsyncMock()
        bot.user_agents[111111] = mock_agent

        await bot.reset_command(update, context)
        self.assertNotIn(111111, bot.user_agents)
        mock_agent.__aexit__.assert_called_once()
        context.bot.send_message.assert_called_once()
        args, kwargs = context.bot.send_message.call_args
        self.assertIn("Sesi Percakapan Direset", kwargs.get("text", ""))

    # --------------------------------------------------------------------------
    # 9. SAFE MARKDOWN FALLBACK
    # --------------------------------------------------------------------------
    async def test_safe_send_message_fallback_on_bad_request(self):
        mock_bot = AsyncMock()
        # First call fails with BadRequest (bad markdown), second call succeeds (plain text)
        mock_bot.send_message.side_effect = [
            bot.BadRequest("Can't find end of entities"),
            MagicMock(message_id=99)
        ]
        res = await bot.safe_send_message(mock_bot, 123, "Unclosed *markdown")
        self.assertIsNotNone(res)
        self.assertEqual(mock_bot.send_message.call_count, 2)
        # Second call should have parse_mode=None
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
        # Setup pending approval
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

    # --------------------------------------------------------------------------
    # 11. TELEGRAM APPROVAL HOOK
    # --------------------------------------------------------------------------
    async def test_approval_hook_hardline_blocked(self):
        mock_bot = AsyncMock()
        hook = bot.TelegramApprovalHook(bot=mock_bot, chat_id=123, user_id=111111)

        tool_call = bot.ToolCall(
            name="run_command",
            args={"command": "rm -rf /"},
            id="call_1"
        )
        context = MagicMock()
        result = await hook.run(context, tool_call)
        self.assertFalse(result.allow)
        self.assertIn("DIBLOKIR", result.message)

    async def test_approval_hook_safe_command_allowed(self):
        mock_bot = AsyncMock()
        hook = bot.TelegramApprovalHook(bot=mock_bot, chat_id=123, user_id=111111)

        tool_call = bot.ToolCall(
            name="run_command",
            args={"command": "git status"},
            id="call_2"
        )
        context = MagicMock()
        result = await hook.run(context, tool_call)
        self.assertTrue(result.allow)

if __name__ == "__main__":
    unittest.main()
