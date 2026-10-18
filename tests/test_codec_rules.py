"""Codec and parser tests that need no legacy binary present."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import legacy_rules
import legacy_wordlist_codec as codec

CRLF = codec._CRLF


class WordlistCodecTest(unittest.TestCase):
    def test_alphabet_is_printable_ascii(self):
        # 64-symbol alphabet mapped onto 0x30..0x6F; CRLF is kept verbatim.
        self.assertEqual(codec.SYM_BASE, 0x30)
        alphabet = {codec.SYM_BASE + i for i in range(64)}
        self.assertEqual(len(alphabet), 64)
        self.assertIn(0x30, alphabet)
        self.assertIn(0x6F, alphabet)

    def test_roundtrip_ascii(self):
        payload = b"hello world" + CRLF + b"second line" + CRLF
        enc = codec.encode_bytes(payload)
        self.assertNotEqual(enc, payload)
        body = enc.replace(CRLF, b"")
        self.assertTrue(all(0x30 <= b <= 0x6F for b in body))
        # every plaintext byte becomes exactly two symbols
        self.assertEqual(len(body), 2 * len(payload.replace(CRLF, b"")))
        self.assertEqual(codec.decode_bytes(enc), payload)

    def test_roundtrip_gbk_chinese(self):
        payload = "色情网站".encode("gbk") + CRLF + "暴力游戏".encode("gbk") + CRLF
        enc = codec.encode_bytes(payload)
        self.assertEqual(codec.decode_bytes(enc), payload)

    def test_roundtrip_edges(self):
        for payload in (b"", b"x", b"ab", b"abc"):
            self.assertEqual(codec.decode_bytes(codec.encode_bytes(payload)), payload)

    def test_plaintext_detected_as_plain(self):
        self.assertFalse(codec.is_obfuscated(b"this is plainly readable text\n"))

    def test_ciphertext_detected_as_obfuscated(self):
        enc = codec.encode_bytes(b"abc")
        self.assertTrue(codec.is_obfuscated(enc))


class RuleGrammarTest(unittest.TestCase):
    def test_plain_literal(self):
        r = legacy_rules.parse_line("[alt.homosexual]")
        self.assertEqual(r["terms"], [{"text": "alt.homosexual", "match": "literal"}])

    def test_prefix_modifier(self):
        r = legacy_rules.parse_line("[+girl]")
        self.assertEqual(r["terms"], [{"text": "girl", "match": "prefix"}])

    def test_suffix_modifier(self):
        r = legacy_rules.parse_line("[=sx600]")
        self.assertEqual(r["terms"], [{"text": "sx600", "match": "suffix"}])

    def test_composed_fragments(self):
        r = legacy_rules.parse_line("[,homo,bi][sex][y,ual,uality]")
        got = {t["text"] for t in r["terms"]}
        for word in ("homosexual", "homosexuality", "bisexual"):
            self.assertIn(word, got)
        self.assertEqual(len(got), 6)

    def test_both_directions_fan_out(self):
        r = legacy_rules.parse_line("[+,=][coed,sex]")
        pairs = {(t["text"], t["match"]) for t in r["terms"]}
        for want in (
            ("coed", "prefix"),
            ("coed", "suffix"),
            ("sex", "prefix"),
            ("sex", "suffix"),
        ):
            self.assertIn(want, pairs)
        self.assertEqual(len(pairs), 4)

    def test_braces_equivalent_to_brackets(self):
        a = legacy_rules.parse_line("[adult,xxx][sex,porn]")
        b = legacy_rules.parse_line("{adult,xxx}{sex,porn}")
        self.assertEqual(
            {t["text"] for t in a["terms"]}, {t["text"] for t in b["terms"]}
        )

    def test_empty_line_dropped(self):
        self.assertEqual(legacy_rules.parse_line("   "), {})


class CategoryIndexTest(unittest.TestCase):
    def test_shape(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "wfile.dat"
            p.write_text(
                "1 成人/色情网站 adwfil.dat\r\n0 其他 other.dat\r\n", encoding="utf-8"
            )
            idx = legacy_rules.read_category_index(p)
        self.assertEqual(
            idx[0], {"enabled": True, "name": "成人/色情网站", "file": "adwfil.dat"}
        )
        self.assertFalse(idx[1]["enabled"])


if __name__ == "__main__":
    unittest.main()
