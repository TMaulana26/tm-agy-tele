#!/usr/bin/env python3
"""
Unit tests for core/agy_engine.py:
1. Transcript recovery and artifact fallback (.md files).
2. Subprocess execution, JSON output parsing, and empty response recovery.
3. Raw JSON leak prevention when response is empty.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from core import agy_engine


class TestAgyEngine(unittest.IsolatedAsyncioTestCase):

    def test_recover_last_response_from_artifact_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            conv_id = "test-conv-artifact-1"
            conv_dir = tmppath / ".gemini" / "brain" / conv_id
            log_dir = conv_dir / ".system_generated" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            transcript_file = log_dir / "transcript.jsonl"

            # Transcript has only USER_INPUT and tool calls, no PLANNER_RESPONSE content
            lines = [
                json.dumps({"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "audit code"}),
                json.dumps({"step_index": 1, "source": "MODEL", "type": "GENERIC", "status": "RUNNING", "content": "Tool running"}),
            ]
            transcript_file.write_text("\n".join(lines), encoding="utf-8")

            # Write an artifact file in conv_dir
            artifact_file = conv_dir / "audit_report.md"
            artifact_file.write_text("# Laporan Audit Keamanan\nSemua aman.", encoding="utf-8")

            with patch("core.agy_engine.WORKSPACE_DIR", tmpdir):
                recovered, res_id = agy_engine.recover_last_response_from_transcript(conv_id)
                self.assertIsNotNone(recovered)
                self.assertIn("Laporan Audit Keamanan", recovered)
                self.assertEqual(res_id, conv_id)

    async def test_run_agy_cli_empty_response_recovers_transcript(self):
        mock_proc = AsyncMock()
        mock_proc.pid = 1234
        json_output = json.dumps({
            "conversation_id": "conv-empty-1",
            "status": "SUCCESS",
            "response": "",
            "duration_seconds": 12.5,
            "num_turns": 2
        }).encode("utf-8")
        mock_proc.communicate.return_value = (json_output, b"")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("os.path.exists", return_value=True):
                with patch("core.agy_engine.recover_last_response_from_transcript", return_value=("# Recovered Report", "conv-empty-1")):
                    resp, conv_id = await agy_engine.run_agy_cli(
                        user_id=111111,
                        prompt="generate audit report",
                        conv_id="conv-empty-1",
                        cwd="."
                    )
                    self.assertEqual(resp, "# Recovered Report")
                    self.assertEqual(conv_id, "conv-empty-1")

    async def test_run_agy_cli_empty_response_friendly_fallback(self):
        mock_proc = AsyncMock()
        mock_proc.pid = 1234
        json_output = json.dumps({
            "conversation_id": "conv-empty-2",
            "status": "SUCCESS",
            "response": "",
            "duration_seconds": 45.2,
            "num_turns": 3
        }).encode("utf-8")
        mock_proc.communicate.return_value = (json_output, b"")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("os.path.exists", return_value=True):
                with patch("core.agy_engine.recover_last_response_from_transcript", return_value=(None, "conv-empty-2")):
                    resp, conv_id = await agy_engine.run_agy_cli(
                        user_id=111111,
                        prompt="long background task",
                        conv_id="conv-empty-2",
                        cwd="."
                    )
                    self.assertIn("Tugas Selesai!", resp)
                    self.assertIn("45.2s", resp)
                    self.assertIn("3 turns", resp)
                    self.assertNotIn('{"conversation_id"', resp)
                    self.assertEqual(conv_id, "conv-empty-2")

    async def test_run_agy_cli_raw_json_stdout_leak_prevention(self):
        mock_proc = AsyncMock()
        mock_proc.pid = 1234
        raw_json_str = '{"conversation_id": "raw-conv-1", "status": "SUCCESS"}'
        mock_proc.communicate.return_value = (raw_json_str.encode("utf-8"), b"")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("os.path.exists", return_value=True):
                resp, conv_id = await agy_engine.run_agy_cli(
                    user_id=111111,
                    prompt="test raw stdout",
                    conv_id="raw-conv-1",
                    cwd="."
                )
                self.assertIn("Tugas Selesai!", resp)
                self.assertNotIn('{"conversation_id"', resp)


if __name__ == "__main__":
    unittest.main()
