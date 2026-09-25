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
        self.assertIn("Execution Timeout", kwargs.get("text", ""))


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

    # --------------------------------------------------------------------------
    # 11. MARKDOWN TO TELEGRAM HTML CONVERTER
    # --------------------------------------------------------------------------
    def test_markdown_to_telegram_html_conversion(self):
        sample = """### 1. **Uptime & Beban CPU**
* **Uptime:** 26 hari
* **Load:** `0.10, 0.20` *(Sangat stabil)*
* **`stretch_reminder`**: Aktif (Up 7 hari)
> Catatan server
```bash
docker ps -a
```"""
        html_out = bot.markdown_to_telegram_html(sample)
        # Verify headers converted without ###
        self.assertNotIn("###", html_out)
        self.assertIn("<b>1. Uptime &amp; Beban CPU</b>", html_out)
        # Verify bullets
        self.assertIn("• <b>Uptime:</b>", html_out)
        # Verify inline code with underscore safely preserved
        self.assertIn("<code>stretch_reminder</code>", html_out)
        self.assertIn("<code>0.10, 0.20</code>", html_out)
        # Verify italic
        self.assertIn("<i>(Sangat stabil)</i>", html_out)
        # Verify blockquote
        self.assertIn("<blockquote>Catatan server</blockquote>", html_out)
        # Verify code block
        self.assertIn('<pre><code class="language-bash">docker ps -a</code></pre>', html_out)

    # --------------------------------------------------------------------------
    # 12. WORKSPACE DIRECTORY AUTO-FALLBACK
    # --------------------------------------------------------------------------
    def test_resolve_workspace_dir(self):
        # When valid directory
        with patch.dict(os.environ, {"WORKSPACE_DIR": "."}):
            ws = bot.resolve_workspace_dir()
            self.assertEqual(ws, ".")

        # When invalid directory like /workspace (Docker leftover)
        with patch.dict(os.environ, {"WORKSPACE_DIR": "/non_existent_workspace_path_123"}):
            ws = bot.resolve_workspace_dir()
            self.assertTrue(os.path.isdir(ws))

    # --------------------------------------------------------------------------
    # 13. MODEL QUOTA & /usage UTILITIES
    # --------------------------------------------------------------------------
    def test_format_progress_bar(self):
        self.assertEqual(bot.format_progress_bar(0.0), "[░░░░░░░░░░] 0.0%")
        self.assertEqual(bot.format_progress_bar(0.5), "[█████░░░░░] 50.0%")
        self.assertEqual(bot.format_progress_bar(1.0), "[██████████] 100.0%")
        self.assertEqual(bot.format_progress_bar(0.944), "[█████████░] 94.4%")
        self.assertEqual(bot.format_progress_bar(0.785), "[████████░░] 78.5%")

    def test_format_relative_time(self):
        # Empty string
        self.assertEqual(bot.format_relative_time(""), "")
        # Past timestamp
        self.assertEqual(bot.format_relative_time("2020-01-01T00:00:00Z"), "Quota available")
        # Future timestamp
        future_iso = "2099-01-01T00:00:00Z"
        rel = bot.format_relative_time(future_iso)
        self.assertTrue(rel.startswith("Refreshes in "))

    def test_format_usage_data_json(self):
        sample_json = json.dumps({
            "command": {
                "name": "usage",
                "data": {
                    "groups": [
                        {
                            "name": "Gemini Models",
                            "description": "Models within this group: Gemini Flash, Gemini Pro",
                            "buckets": [
                                {
                                    "name": "Weekly Limit Remaining",
                                    "remaining_fraction": 0.944,
                                    "reset_time": "2099-01-01T00:00:00Z"
                                },
                                {
                                    "name": "Five Hour Limit Remaining",
                                    "remaining_fraction": 0.785,
                                    "reset_time": "2099-01-01T00:00:00Z"
                                }
                            ]
                        }
                    ]
                }
            }
        })
        formatted = bot.format_usage_data(sample_json)
        self.assertIn("Models &amp; Quota", formatted)
        self.assertIn("GEMINI MODELS", formatted)
        self.assertIn("Weekly Limit Remaining", formatted)
        self.assertIn("Five Hour Limit Remaining", formatted)
        self.assertIn("94.4%", formatted)
        self.assertIn("78.5%", formatted)

    def test_format_usage_data_tsv_fallback(self):
        tsv_sample = "Gemini Models\tWeekly Limit Remaining\t94%\t2026-09-30T02:23:22Z"
        formatted = bot.format_usage_data(tsv_sample)
        self.assertIn("Gemini Models", formatted)
        self.assertIn("Weekly Limit Remaining", formatted)
        self.assertIn("94%", formatted)

    def test_is_quota_inquiry(self):
        # Pertanyaan kuota dalam bahasa alami
        self.assertTrue(bot.is_quota_inquiry("Usage limit akang sisa berapa ya ?"))
        self.assertTrue(bot.is_quota_inquiry("sisa limit"))
        self.assertTrue(bot.is_quota_inquiry("sisa kuota saya berapa ya"))
        self.assertTrue(bot.is_quota_inquiry("cek kuota"))
        self.assertTrue(bot.is_quota_inquiry("cek limit"))
        self.assertTrue(bot.is_quota_inquiry("/usage"))
        self.assertTrue(bot.is_quota_inquiry("/limit"))
        self.assertTrue(bot.is_quota_inquiry("berapa kuota tersisa?"))

        # Bukan pertanyaan kuota (instruksi koding atau percakapan biasa)
        self.assertFalse(bot.is_quota_inquiry("SELECT * FROM users LIMIT 10"))
        self.assertFalse(bot.is_quota_inquiry("buat middleware rate limit di nodejs"))
        self.assertFalse(bot.is_quota_inquiry("halo apa kabar"))
        self.assertFalse(bot.is_quota_inquiry("tolong buatkan form input dengan css"))

    async def test_usage_command_unauthorized(self):
        update = MagicMock()
        update.effective_user.id = 999999  # Unauthorized
        context = MagicMock()
        await bot.usage_command(update, context)
        # Should return without sending message
        context.bot.send_message.assert_not_called()

    async def test_usage_command_authorized(self):
        update = MagicMock()
        update.effective_user.id = 111111  # Authorized
        update.effective_chat.id = 111111
        context = MagicMock()

        mock_status_msg = AsyncMock()
        context.bot.send_message = AsyncMock(return_value=mock_status_msg)

        with patch("bot.fetch_agy_usage_report", new=AsyncMock(return_value="📊 <b>Laporan Kuota</b>")):
            await bot.usage_command(update, context)
            mock_status_msg.edit_text.assert_called_once()
            call_kwargs = mock_status_msg.edit_text.call_args.kwargs
            self.assertIn("Laporan Kuota", call_kwargs.get("text", ""))

    async def test_handle_message_quota_inquiry_intercept(self):
        update = MagicMock()
        update.effective_user.id = 111111
        update.effective_chat.id = 111111
        update.message.text = "Usage limit akang sisa berapa ya ?"
        context = MagicMock()

        mock_status_msg = AsyncMock()
        context.bot.send_message = AsyncMock(return_value=mock_status_msg)

        with patch("bot.fetch_agy_usage_report", new=AsyncMock(return_value="📊 <b>Laporan Kuota Intercepted</b>")):
            with patch("bot.execute_agent_turn", new=AsyncMock()) as mock_agent_turn:
                await bot.handle_message(update, context)
                # execute_agent_turn must NOT be called because it was intercepted
                mock_agent_turn.assert_not_called()
                mock_status_msg.edit_text.assert_called_once()
                call_kwargs = mock_status_msg.edit_text.call_args.kwargs
                self.assertIn("Laporan Kuota Intercepted", call_kwargs.get("text", ""))

    # --------------------------------------------------------------------------
    # 11. HEADLESS RESILIENCE & SUBPROCESS TIMEOUT RECOVERY
    # --------------------------------------------------------------------------
    def test_build_cli_prompt(self):
        prompt = "buatkan cron job jam 8 pagi"
        built = bot.build_cli_prompt(prompt)
        self.assertIn("JANGAN PERNAH menggunakan tool internal `schedule`", built)
        self.assertIn("crontab", built)
        self.assertIn("[PERMINTAAN PENGGUNA]\nbuatkan cron job jam 8 pagi", built)

    def test_recover_last_response_success(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            conv_id = "test-conv-12345"
            log_dir = tmppath / ".gemini" / "brain" / conv_id / ".system_generated" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            transcript_file = log_dir / "transcript.jsonl"

            lines = [
                json.dumps({"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "buat script"}),
                json.dumps({"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "tool_calls": [{"name": "schedule"}]}),
                json.dumps({"step_index": 2, "source": "MODEL", "type": "GENERIC", "status": "RUNNING", "content": "Tool is running"}),
                json.dumps({"step_index": 3, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE", "content": "Siap! Cron job sudah aktif."})
            ]
            transcript_file.write_text("\n".join(lines), encoding="utf-8")

            with patch("bot.WORKSPACE_DIR", tmpdir):
                recovered, res_id = bot.recover_last_response_from_transcript(conv_id)
                self.assertEqual(recovered, "Siap! Cron job sudah aktif.")
                self.assertEqual(res_id, conv_id)

    def test_recover_last_response_anti_stale_protection(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            conv_id = "test-conv-anti-stale"
            log_dir = tmppath / ".gemini" / "brain" / conv_id / ".system_generated" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            transcript_file = log_dir / "transcript.jsonl"

            lines = [
                json.dumps({"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "halo"}),
                json.dumps({"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE", "content": "Jawaban lama turn 1"}),
                json.dumps({"step_index": 2, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "jalankan perintah hang"}),
                json.dumps({"step_index": 3, "source": "MODEL", "type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command"}]}),
                json.dumps({"step_index": 4, "source": "MODEL", "type": "GENERIC", "status": "RUNNING", "content": "running..."})
            ]
            transcript_file.write_text("\n".join(lines), encoding="utf-8")

            with patch("bot.WORKSPACE_DIR", tmpdir):
                recovered, res_id = bot.recover_last_response_from_transcript(conv_id)
                # Harus None karena terhenti di baris USER_INPUT sebelum ada PLANNER_RESPONSE baru
                self.assertIsNone(recovered)
                self.assertEqual(res_id, conv_id)

    def test_recover_last_response_new_conversation_autodiscovery(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            conv_id = "new-conv-autodiscovered"
            log_dir = tmppath / ".gemini" / "brain" / conv_id / ".system_generated" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            transcript_file = log_dir / "transcript.jsonl"

            lines = [
                json.dumps({"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "halo pertama"}),
                json.dumps({"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE", "content": "Halo! Ini jawaban sesi baru."})
            ]
            transcript_file.write_text("\n".join(lines), encoding="utf-8")

            with patch("bot.WORKSPACE_DIR", tmpdir):
                # conv_id None (sesi baru)
                recovered, res_id = bot.recover_last_response_from_transcript(None)
                self.assertEqual(recovered, "Halo! Ini jawaban sesi baru.")
                self.assertEqual(res_id, conv_id)

    async def test_run_agy_cli_timeout_with_recovery(self):
        mock_proc = AsyncMock()
        mock_proc.pid = 8888
        mock_proc.communicate.side_effect = asyncio.TimeoutError()
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            with patch("os.path.exists", return_value=True):
                with patch("bot.recover_last_response_from_transcript", return_value=("Jawaban pulih!", "conv-timeout-1")):
                    resp, conv_id = await bot.run_agy_cli(
                        user_id=111111,
                        prompt="tugas berat",
                        conv_id="conv-timeout-1",
                        cwd="."
                    )
                    self.assertIn("Jawaban pulih!", resp)
                    self.assertIn("melebihi batas waktu", resp)
                    self.assertEqual(conv_id, "conv-timeout-1")
                    mock_proc.terminate.assert_called_once()

                    call_args = mock_exec.call_args[0]
                    self.assertIn("--print-timeout", call_args)

    async def test_run_agy_cli_timeout_without_recovery(self):
        mock_proc = AsyncMock()
        mock_proc.pid = 8888
        mock_proc.communicate.side_effect = asyncio.TimeoutError()
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("os.path.exists", return_value=True):
                with patch("bot.recover_last_response_from_transcript", return_value=(None, "conv-timeout-2")):
                    resp, conv_id = await bot.run_agy_cli(
                        user_id=111111,
                        prompt="tugas berat macet",
                        conv_id="conv-timeout-2",
                        cwd="."
                    )
                    self.assertIn("Waktu eksekusi habis (Timeout", resp)
                    self.assertEqual(conv_id, "conv-timeout-2")
                    mock_proc.terminate.assert_called_once()

    # --------------------------------------------------------------------------
    # 12. TELEGRAM UX ENHANCEMENTS (HERMES-INSPIRED)
    # --------------------------------------------------------------------------
    async def test_post_init_set_my_commands_success(self):
        mock_app = MagicMock()
        mock_app.bot.set_my_commands = AsyncMock()

        await bot.post_init(mock_app)
        mock_app.bot.set_my_commands.assert_called_once()
        commands = mock_app.bot.set_my_commands.call_args[0][0]
        cmd_names = [c.command for c in commands]
        self.assertEqual(cmd_names, ["usage", "status", "cancel", "reset", "help"])

    async def test_post_init_set_my_commands_error_handled(self):
        mock_app = MagicMock()
        mock_app.bot.set_my_commands = AsyncMock(side_effect=Exception("Network error"))

        try:
            await bot.post_init(mock_app)
        except Exception as e:
            self.fail(f"post_init raised unexpected exception: {e}")

    async def test_safe_send_message_with_reply_and_silent(self):
        mock_bot = AsyncMock()
        mock_msg = MagicMock(message_id=101)
        mock_bot.send_message.return_value = mock_msg

        res = await bot.safe_send_message(
            bot=mock_bot,
            chat_id=123,
            text="Pesan status",
            disable_notification=True,
            reply_to_message_id=987
        )
        self.assertEqual(res, mock_msg)
        mock_bot.send_message.assert_called_once_with(
            chat_id=123,
            text="Pesan status",
            reply_markup=None,
            parse_mode=bot.ParseMode.HTML,
            disable_notification=True,
            reply_to_message_id=987
        )

    async def test_safe_send_message_reply_deleted_fallback(self):
        mock_bot = AsyncMock()
        mock_msg = MagicMock(message_id=102)
        mock_bot.send_message.side_effect = [
            bot.BadRequest("Can't find end of entities"),
            bot.BadRequest("Message to be replied not found"),
            mock_msg
        ]

        res = await bot.safe_send_message(
            bot=mock_bot,
            chat_id=123,
            text="Pesan fallback",
            disable_notification=True,
            reply_to_message_id=987
        )
        self.assertEqual(res, mock_msg)
        self.assertEqual(mock_bot.send_message.call_count, 3)
        last_kwargs = mock_bot.send_message.call_args.kwargs
        self.assertNotIn("reply_to_message_id", last_kwargs)

    def test_markdown_to_telegram_html_table_wrapping(self):
        table_md = (
            "Berikut tabel spesifikasi:\n\n"
            "| Fitur | Status | Keterangan |\n"
            "| :--- | :---: | ---: |\n"
            "| RAM | 35MB | Hemat |\n"
            "| Storage | SSD | Cepat |\n\n"
            "Selesai."
        )
        out = bot.markdown_to_telegram_html(table_md)
        self.assertIn("Berikut tabel spesifikasi:", out)
        self.assertIn("<pre><code>| Fitur | Status | Keterangan |", out)
        self.assertIn("| Storage | SSD | Cepat |</code></pre>", out)
        self.assertIn("Selesai.", out)

    def test_markdown_to_telegram_html_multiple_tables(self):
        md = (
            "Tabel 1:\n"
            "| A | B |\n"
            "|---|---|\n"
            "| 1 | 2 |\n\n"
            "Tabel 2:\n"
            "| X | Y |\n"
            "|---|---|\n"
            "| 8 | 9 |\n"
        )
        out = bot.markdown_to_telegram_html(md)
        self.assertEqual(out.count("<pre><code>"), 2)
        self.assertIn("<pre><code>| A | B |\n|---|---|\n| 1 | 2 |</code></pre>", out)
        self.assertIn("<pre><code>| X | Y |\n|---|---|\n| 8 | 9 |</code></pre>", out)

    def test_markdown_to_telegram_html_table_inside_codeblock_not_double_wrapped(self):
        md = (
            "```markdown\n"
            "| Col1 | Col2 |\n"
            "| --- | --- |\n"
            "| Val1 | Val2 |\n"
            "```"
        )
        out = bot.markdown_to_telegram_html(md)
        self.assertEqual(out.count("<pre>"), 1)
        self.assertIn('<pre><code class="language-markdown">', out)

    def test_get_upload_dir(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("bot.WORKSPACE_DIR", tmpdir):
                up_dir = bot.get_upload_dir()
                self.assertTrue(up_dir.exists())
                self.assertTrue(up_dir.is_dir())
                self.assertEqual(up_dir.name, ".telegram_uploads")

    async def test_handle_photo_message_unauthorized(self):
        update = MagicMock()
        update.effective_user.id = 999999  # Unauthorized
        context = MagicMock()

        with patch("bot._dispatch_agent_turn", new=AsyncMock()) as mock_dispatch:
            await bot.handle_photo_message(update, context)
            mock_dispatch.assert_not_called()

    async def test_handle_photo_message_flow(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("bot.WORKSPACE_DIR", tmpdir):
                update = MagicMock()
                update.effective_user.id = 111111  # Authorized
                update.message.caption = "Periksa screenshot error ini"

                mock_photo_small = MagicMock()
                mock_photo_large = MagicMock()
                mock_file = AsyncMock()
                mock_photo_large.get_file = AsyncMock(return_value=mock_file)
                update.message.photo = [mock_photo_small, mock_photo_large]

                context = MagicMock()

                with patch("bot._dispatch_agent_turn", new=AsyncMock()) as mock_dispatch:
                    await bot.handle_photo_message(update, context)
                    mock_file.download_to_drive.assert_called_once()
                    mock_dispatch.assert_called_once()
                    prompt_arg = mock_dispatch.call_args[0][2]
                    self.assertIn("[PENGGUNA MENGIRIMKAN GAMBAR / SCREENSHOT]", prompt_arg)
                    self.assertIn("Periksa screenshot error ini", prompt_arg)
                    self.assertIn(".telegram_uploads", prompt_arg)

    async def test_handle_photo_message_download_error(self):
        update = MagicMock()
        update.effective_user.id = 111111  # Authorized
        update.message.photo = [MagicMock()]
        update.message.photo[-1].get_file = AsyncMock(side_effect=Exception("Download failed"))
        update.message.reply_text = AsyncMock()
        context = MagicMock()

        with patch("bot._dispatch_agent_turn", new=AsyncMock()) as mock_dispatch:
            await bot.handle_photo_message(update, context)
            mock_dispatch.assert_not_called()
            update.message.reply_text.assert_called_once()
            self.assertIn("Gagal mengunduh foto", update.message.reply_text.call_args[0][0])

    async def test_handle_document_message_unauthorized(self):
        update = MagicMock()
        update.effective_user.id = 999999  # Unauthorized
        context = MagicMock()

        with patch("bot._dispatch_agent_turn", new=AsyncMock()) as mock_dispatch:
            await bot.handle_document_message(update, context)
            mock_dispatch.assert_not_called()

    async def test_handle_document_message_flow(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("bot.WORKSPACE_DIR", tmpdir):
                update = MagicMock()
                update.effective_user.id = 111111  # Authorized
                update.message.caption = "Baca file log ini"
                mock_doc = MagicMock()
                mock_doc.file_name = "server_error.log"
                mock_file = AsyncMock()
                mock_doc.get_file = AsyncMock(return_value=mock_file)
                update.message.document = mock_doc

                context = MagicMock()

                with patch("bot._dispatch_agent_turn", new=AsyncMock()) as mock_dispatch:
                    await bot.handle_document_message(update, context)
                    mock_file.download_to_drive.assert_called_once()
                    mock_dispatch.assert_called_once()
                    prompt_arg = mock_dispatch.call_args[0][2]
                    self.assertIn("[PENGGUNA MENGIRIMKAN DOKUMEN / BERKAS]", prompt_arg)
                    self.assertIn("server_error.log", prompt_arg)
                    self.assertIn("Baca file log ini", prompt_arg)
                    self.assertIn(".telegram_uploads", prompt_arg)

    async def test_handle_document_message_download_error(self):
        update = MagicMock()
        update.effective_user.id = 111111  # Authorized
        update.message.document = MagicMock()
        update.message.document.get_file = AsyncMock(side_effect=Exception("Disk full"))
        update.message.reply_text = AsyncMock()
        context = MagicMock()

        with patch("bot._dispatch_agent_turn", new=AsyncMock()) as mock_dispatch:
            await bot.handle_document_message(update, context)
            mock_dispatch.assert_not_called()
            update.message.reply_text.assert_called_once()
            self.assertIn("Gagal mengunduh dokumen", update.message.reply_text.call_args[0][0])

    async def test_execute_agent_turn_silent_status_and_reply_anchor(self):
        update = MagicMock()
        update.effective_user.id = 111111
        update.effective_chat.id = 123
        update.message.message_id = 777

        context = MagicMock()
        mock_status_msg = AsyncMock()
        mock_status_msg.message_id = 888

        with patch("bot.safe_send_message", new=AsyncMock(return_value=mock_status_msg)) as mock_send:
            with patch("bot.run_agy_cli", new=AsyncMock(return_value=("Hasil respons", "conv-turn-1"))):
                with patch("bot.safe_edit_message", new=AsyncMock(return_value=True)):
                    await bot.execute_agent_turn(update, context, "halo bot")
                    self.assertTrue(mock_send.called)
                    first_call = mock_send.call_args_list[0]
                    self.assertEqual(first_call.kwargs.get("disable_notification"), True)
                    self.assertEqual(first_call.kwargs.get("reply_to_message_id"), 777)

    async def test_handle_photo_message_empty_caption_default_prompt(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("bot.WORKSPACE_DIR", tmpdir):
                update = MagicMock()
                update.effective_user.id = 111111
                update.message.caption = None  # No caption
                mock_photo = MagicMock()
                mock_file = AsyncMock()
                mock_photo.get_file = AsyncMock(return_value=mock_file)
                update.message.photo = [mock_photo]

                context = MagicMock()
                with patch("bot._dispatch_agent_turn", new=AsyncMock()) as mock_dispatch:
                    await bot.handle_photo_message(update, context)
                    mock_dispatch.assert_called_once()
                    prompt_arg = mock_dispatch.call_args[0][2]
                    self.assertIn("Tolong periksa dan analisis gambar terlampir ini.", prompt_arg)

    async def test_handle_document_message_path_traversal_sanitized(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("bot.WORKSPACE_DIR", tmpdir):
                update = MagicMock()
                update.effective_user.id = 111111
                update.message.caption = None
                mock_doc = MagicMock()
                mock_doc.file_name = "../../../etc/passwd"  # Directory traversal attempt
                mock_file = AsyncMock()
                mock_doc.get_file = AsyncMock(return_value=mock_file)
                update.message.document = mock_doc

                context = MagicMock()
                with patch("bot._dispatch_agent_turn", new=AsyncMock()) as mock_dispatch:
                    await bot.handle_document_message(update, context)
                    mock_dispatch.assert_called_once()
                    dest_call_arg = mock_file.download_to_drive.call_args.kwargs.get("custom_path")
                    # The saved file must reside safely inside .telegram_uploads
                    self.assertTrue(str(dest_call_arg).startswith(tmpdir))
                    self.assertIn(".telegram_uploads", str(dest_call_arg))
                    self.assertTrue(str(dest_call_arg).endswith("passwd"))

    async def test_handle_document_message_empty_filename_fallback(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("bot.WORKSPACE_DIR", tmpdir):
                update = MagicMock()
                update.effective_user.id = 111111
                update.message.caption = ""
                mock_doc = MagicMock()
                mock_doc.file_name = ""  # Empty filename
                mock_file = AsyncMock()
                mock_doc.get_file = AsyncMock(return_value=mock_file)
                update.message.document = mock_doc

                context = MagicMock()
                with patch("bot._dispatch_agent_turn", new=AsyncMock()) as mock_dispatch:
                    await bot.handle_document_message(update, context)
                    mock_dispatch.assert_called_once()
                    prompt_arg = mock_dispatch.call_args[0][2]
                    self.assertIn("doc_", prompt_arg)
                    self.assertIn("Tolong periksa, baca, dan analisis dokumen terlampir ini.", prompt_arg)

    def test_markdown_to_telegram_html_table_special_chars(self):
        table_md = (
            "| Tag | Symbol | Logic |\n"
            "| --- | --- | --- |\n"
            "| <script> | & | _test_var_ |\n"
        )
        out = bot.markdown_to_telegram_html(table_md)
        self.assertIn("&lt;script&gt;", out)
        self.assertIn("&amp;", out)
        self.assertIn("_test_var_", out)
        # Should not be converted to <i>test</i> inside table pre block
        self.assertNotIn("<i>", out)

if __name__ == "__main__":
    unittest.main()


