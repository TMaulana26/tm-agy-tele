#!/usr/bin/env python3
"""Unit tests for Interactive /model Picker."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tele.picker import (
    FALLBACK_MODELS,
    build_model_keyboard,
    fetch_available_models,
)


class TestModelPicker(unittest.IsolatedAsyncioTestCase):

    def test_build_model_keyboard_layout(self):
        models = [
            {"id": "gemini-3.8-flash-high", "name": "Gemini 3.8 Flash"},
            {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6"},
            {"id": "gpt-oss-120b-medium", "name": "GPT-OSS 120B"},
        ]
        active = "claude-sonnet-4-6"
        kb = build_model_keyboard(models, active, page=0)

        # 3 model rows + 1 navigation row + 1 close button row
        self.assertEqual(len(kb.inline_keyboard), 5)

        # Active model should have checkmark
        claude_btn = kb.inline_keyboard[1][0]
        self.assertTrue(claude_btn.text.startswith("✓"))
        self.assertEqual(claude_btn.callback_data, "model_set:claude-sonnet-4-6")

        # Inactive model should not have checkmark
        gemini_btn = kb.inline_keyboard[0][0]
        self.assertFalse(gemini_btn.text.startswith("✓"))

        # Close button row
        close_btn = kb.inline_keyboard[4][0]
        self.assertEqual(close_btn.callback_data, "model_close")
        self.assertIn("Tutup", close_btn.text)

    async def test_fetch_available_models_fallback_on_error(self):
        with patch("asyncio.create_subprocess_exec", side_effect=Exception("binary error")):
            models = await fetch_available_models()
            self.assertEqual(models, FALLBACK_MODELS)


if __name__ == "__main__":
    unittest.main()
