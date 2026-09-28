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

    def test_triple_asterisk_and_crossing_tags(self):
        # Regression test for VPS Step 384 bug with nested italic inside bold ending in ***
        raw = "1. **[Kritis] Urutan *Permission* di [deploy.sh](file:///deploy.sh) Memicu *Permission Denied (500 Error)***"
        formatted = markdown_to_telegram_html(raw)
        self._validate_html_tags(formatted)
        self.assertIn("Permission Denied (500 Error)", formatted)

    def test_sanitize_and_balance_html_direct(self):
        from tele.formatters import sanitize_and_balance_html
        # Crossing tags: <b><i>...</b></i>
        crossing = "<b>Title <i>Sub</b></i>"
        balanced = sanitize_and_balance_html(crossing)
        self._validate_html_tags(balanced)
        self.assertEqual(balanced, "<b>Title <i>Sub</i></b>")

        # Unclosed tags: <b><i>text
        unclosed = "<b><i>Unclosed text"
        balanced2 = sanitize_and_balance_html(unclosed)
        self._validate_html_tags(balanced2)
        self.assertEqual(balanced2, "<b><i>Unclosed text</i></b>")

        # Unsupported tags escaped: <script>alert(1)</script>
        unsupported = "<script>alert(1)</script>"
        balanced3 = sanitize_and_balance_html(unsupported)
        self.assertEqual(balanced3, "&lt;script&gt;alert(1)&lt;/script&gt;")


if __name__ == "__main__":
    unittest.main()
