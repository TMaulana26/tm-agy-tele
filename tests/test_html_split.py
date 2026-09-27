#!/usr/bin/env python3
"""Unit tests for HTML-aware message splitting and tag balancing."""

import re
import unittest
from tele.streaming import split_message
from tele.formatters import strip_html_for_plain_text, markdown_to_telegram_html


class TestHtmlSplit(unittest.TestCase):

    def _validate_html_tags(self, chunk: str):
        tag_regex = re.compile(r"</?([a-zA-Z0-9_\-]+)(?:\s+[^>]*)?>")
        stack = []
        for match in tag_regex.finditer(chunk):
            tag_full = match.group(0)
            tag_name = match.group(1).lower()
            if not tag_full.startswith("</"):
                stack.append(tag_name)
            else:
                self.assertTrue(len(stack) > 0, f"Unexpected closing tag </{tag_name}> in chunk: {chunk[:50]}...")
                expected = stack.pop()
                self.assertEqual(expected, tag_name, f"Mismatched tag: expected </{expected}>, got </{tag_name}>")
        self.assertEqual(len(stack), 0, f"Unclosed tags remaining: {stack}")

    def test_split_huge_code_block(self):
        huge_code = '<pre><code class="language-python">\n' + ('print("hello world")\n' * 300) + '</code></pre>'
        chunks = split_message(huge_code, max_length=1000)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 1000)
            self._validate_html_tags(chunk)

    def test_split_mixed_html_formatting(self):
        content = (
            "<b>🛡️ Laporan Audit Keamanan</b>\n"
            "<pre><code class=\"language-php\">\n" + ("$item = request('color', '#005fa9');\n" * 200) + "</code></pre>\n"
            "───────────────\n"
            "<blockquote>Catatan penting untuk mitigasi risiko:</blockquote>\n"
            "<pre><code>" + ("line of text in second code block\n" * 150) + "</code></pre>\n"
            "<b>🎯 Kesimpulan & Langkah Selanjutnya</b>\n"
        )
        chunks = split_message(content, max_length=1500)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 1500)
            self._validate_html_tags(chunk)

    def test_strip_html_for_plain_text(self):
        html_input = '<b>Judul</b> &amp; <code>kode = &#x27;val&#x27;;</code> <a href="https://example.com">Link</a>'
        plain = strip_html_for_plain_text(html_input)
        self.assertEqual(plain, "Judul & kode = 'val'; Link")

    def test_severity_list_formatting(self):
        raw = "4. [MEDIUM] Bypass Batasan Tenant Admin di `/tenant/switch` (SEC-04)"
        formatted = markdown_to_telegram_html(raw)
        self.assertIn("<b>4. [MEDIUM] Bypass Batasan Tenant Admin di", formatted)
        self.assertIn("<code>/tenant/switch</code>", formatted)


if __name__ == "__main__":
    unittest.main()
