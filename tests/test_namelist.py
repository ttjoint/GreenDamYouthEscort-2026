"""Tests for the 144-byte name-list record and the legacy rule grammar.

Discipline: every rule and text in this file is invented (single CJK
characters 甲乙丙丁, or ASCII placeholders). Nothing here is copied from a
legacy word list, and no assertion prints legacy content.
"""
from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam import namelist  # noqa: E402
from green_dam.namelist import (  # noqa: E402
    RECORD_SIZE,
    NameRecord,
    Rule,
    RuleSyntaxError,
    decode_record,
    encode_record,
    iter_records,
    match_any,
    parse_rule,
)


class GrammarShapeTest(unittest.TestCase):
    def test_single_bare_term(self):
        rule = parse_rule("甲")
        self.assertEqual(rule.groups, (frozenset({"甲"}),))

    def test_bare_comma_list_is_one_or_group(self):
        rule = parse_rule("甲,乙")
        self.assertEqual(rule.groups, (frozenset({"甲", "乙"}),))

    def test_braced_group(self):
        rule = parse_rule("{甲,乙}")
        self.assertEqual(rule.groups, (frozenset({"甲", "乙"}),))

    def test_two_groups_are_anded(self):
        rule = parse_rule("{甲,乙}[丙,丁]")
        self.assertEqual(len(rule.groups), 2)
        self.assertEqual(rule.groups[0], frozenset({"甲", "乙"}))
        self.assertEqual(rule.groups[1], frozenset({"丙", "丁"}))

    def test_empty_rule_has_no_groups(self):
        self.assertEqual(parse_rule("").groups, ())
        self.assertEqual(parse_rule("   ").groups, ())

    def test_whitespace_is_ignored(self):
        a = parse_rule("{甲 , 乙} [ 丙 ]")
        b = parse_rule("{甲,乙}[丙]")
        self.assertEqual(a.groups, b.groups)

    def test_terms_deduped(self):
        self.assertEqual(set(parse_rule("{甲,甲}[乙]").terms), {"甲", "乙"})


class GrammarMatchTest(unittest.TestCase):
    def test_or_group_matches_either(self):
        rule = parse_rule("甲,乙")
        self.assertTrue(rule.matches("...甲..."))
        self.assertTrue(rule.matches("...乙..."))
        self.assertFalse(rule.matches("...丙..."))

    def test_and_across_groups_requires_both(self):
        rule = parse_rule("{甲,乙}[丙,丁]")
        self.assertTrue(rule.matches("甲丙"))
        self.assertTrue(rule.matches("乙丁"))
        self.assertFalse(rule.matches("甲戊"))   # second group absent
        self.assertFalse(rule.matches("戊丙"))   # first group absent

    def test_empty_rule_matches_nothing(self):
        self.assertFalse(parse_rule("").matches("甲"))

    def test_matching_is_case_sensitive(self):
        # The original lowercased upstream; the matcher itself did not.
        rule = parse_rule("abc")
        self.assertTrue(rule.matches("abc"))
        self.assertFalse(rule.matches("ABC"))

    def test_match_any_returns_first(self):
        rules = [parse_rule("甲"), parse_rule("乙")]
        self.assertEqual(match_any(rules, "乙").source, "乙")
        self.assertIsNone(match_any(rules, "丙"))


class GrammarErrorTest(unittest.TestCase):
    def test_unclosed_brace(self):
        with self.assertRaises(RuleSyntaxError):
            parse_rule("{甲,乙")

    def test_unclosed_bracket(self):
        with self.assertRaises(RuleSyntaxError):
            parse_rule("[甲,乙")

    def test_stray_closer(self):
        with self.assertRaises(RuleSyntaxError):
            parse_rule("甲}")

    def test_group_cap_is_five(self):
        ok = "".join("{甲}" for _ in range(namelist.MAX_GROUPS))
        self.assertEqual(len(parse_rule(ok).groups), namelist.MAX_GROUPS)
        too_many = "".join("{甲}" for _ in range(namelist.MAX_GROUPS + 1))
        with self.assertRaises(RuleSyntaxError):
            parse_rule(too_many)

    def test_entry_cap_per_group(self):
        many = ",".join(f"x{i}" for i in range(namelist.MAX_ENTRIES_PER_GROUP + 1))
        with self.assertRaises(RuleSyntaxError):
            parse_rule("{" + many + "}")


class RecordLayoutTest(unittest.TestCase):
    def test_sizes_are_the_documented_ones(self):
        self.assertEqual(RECORD_SIZE, 144)
        self.assertEqual(namelist.RECORD_FLAG_OFFSET, 0x84)

    def test_round_trip_minimal(self):
        rec = NameRecord(text="甲", in_use=True)
        blob = encode_record(rec)
        self.assertEqual(len(blob), RECORD_SIZE)
        back = decode_record(blob)
        self.assertEqual(back.text, "甲")
        self.assertTrue(back.in_use)

    def test_flag_lives_at_0x84(self):
        blob = bytearray(RECORD_SIZE)
        blob[0:2] = "甲".encode("gbk")
        struct.pack_into("<I", blob, 0x84, 1)
        self.assertTrue(decode_record(bytes(blob)).in_use)
        struct.pack_into("<I", blob, 0x84, 0)
        self.assertFalse(decode_record(bytes(blob)).in_use)

    def test_decode_requires_exact_size(self):
        for n in (0, 143, 145):
            with self.assertRaises(ValueError):
                decode_record(b"\x00" * n)

    def test_text_must_fit_field(self):
        with self.assertRaises(ValueError):
            NameRecord(text="x" * namelist.TEXT_FIELD_SIZE, in_use=True)

    def test_opaque_bytes_round_trip_verbatim(self):
        # Bytes *after* the flag are never interpreted by the original, so a
        # decode/encode cycle must preserve them exactly. (Bytes inside the text
        # field belong to the text field and are legitimately rewritten.)
        raw = bytearray(RECORD_SIZE)
        raw[0:2] = "甲".encode("gbk")
        struct.pack_into("<I", raw, 0x84, 1)
        raw[0x88:0x8C] = b"\x01\x02\x03\x04"
        raw[0x8C:0x90] = b"\xde\xad\xbe\xef"
        rec = decode_record(bytes(raw))
        self.assertEqual(rec.raw, bytes(raw))
        self.assertEqual(encode_record(rec), bytes(raw))

    def test_raw_length_validated(self):
        with self.assertRaises(ValueError):
            NameRecord(text="甲", in_use=True, raw=b"\x00")

    def test_bare_record_encodes_zero_filled(self):
        blob = encode_record(NameRecord(text="甲", in_use=True))
        self.assertEqual(len(blob), RECORD_SIZE)
        self.assertEqual(blob[2:0x84].count(0), 0x84 - 2)
        self.assertEqual(blob[0x88:].count(0), RECORD_SIZE - 0x88)

    def test_record_rule_property(self):
        rec = NameRecord(text="{甲}[乙]", in_use=True)
        self.assertTrue(rec.rule.matches("甲乙"))


class ArrayTest(unittest.TestCase):
    def _blob(self, texts):
        out = bytearray()
        for text, in_use in texts:
            out += encode_record(NameRecord(text=text, in_use=in_use))
        return bytes(out)

    def test_iter_skips_unused(self):
        blob = self._blob([("甲", True), ("乙", False), ("丙", True)])
        self.assertEqual([r.text for r in iter_records(blob)], ["甲", "丙"])

    def test_iter_rejects_partial_record(self):
        with self.assertRaises(ValueError):
            list(iter_records(b"\x00" * (RECORD_SIZE + 1)))

    def test_empty_blob_yields_nothing(self):
        self.assertEqual(list(iter_records(b"")), [])


if __name__ == "__main__":
    unittest.main()
