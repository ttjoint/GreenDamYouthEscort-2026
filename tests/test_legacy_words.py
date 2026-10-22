"""Tests for loading the shipped obfuscated word lists.

Two kinds of test here. The codec tests use synthetic bytes so they assert the
format rather than whatever happens to be in this install. The integration tests
read the real tree if it is present and skip otherwise, so the suite still runs
on a machine without the vendor files.

No test prints a decoded term: the lists are the vendor's content, and the
project's rule is to report counts and structure, never entries.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam.legacy_words import (  # noqa: E402
    CATALOGUE,
    SYM_BASE,
    CatalogueEntry,
    LoadReport,
    WordlistFormatError,
    decode_bytes,
    decode_lines,
    decode_text,
    is_obfuscated,
    load_directory,
    load_file,
    parse_catalogue,
)
from green_dam.wordlists import WordlistIndex  # noqa: E402

#: Where the vendor tree lives, if this checkout has it.
VENDOR = ROOT.parent / "LH-installed-tree" / "Windows" / "System32"


def encode_pair(byte: int) -> bytes:
    """Encode one plaintext byte to two symbols, inverting the documented bits.

    Written out plainly: the padding slots are ignored by the decoder, and a
    readable encoder is worth more here than a dense one.
    """
    bits = [(byte >> i) & 1 for i in range(8)]
    value = 0
    value |= bits[0] << 0
    value |= bits[1] << 1
    value |= (1 - bits[2]) << 2
    value |= (1 - bits[3]) << 3
    value |= bits[4] << 6
    value |= bits[5] << 7
    value |= (1 - bits[6]) << 8
    value |= (1 - bits[7]) << 9
    return bytes([SYM_BASE + (value >> 6), SYM_BASE + (value & 0x3F)])


def encode_bytes(plain: bytes) -> bytes:
    """Encode a whole payload, keeping CRLF verbatim."""
    out = bytearray()
    i = 0
    while i < len(plain):
        if plain[i:i + 2] == b"\r\n":
            out += b"\r\n"
            i += 2
        else:
            out += encode_pair(plain[i])
            i += 1
    return bytes(out)


class CodecTest(unittest.TestCase):
    def test_round_trip_ascii(self):
        plain = "abcXYZ012".encode()
        self.assertEqual(decode_bytes(encode_bytes(plain)), plain)

    def test_round_trip_all_bytes(self):
        plain = bytes(range(256))
        self.assertEqual(decode_bytes(encode_bytes(plain)), plain)

    def test_crlf_preserved(self):
        plain = b"a\r\nb"
        self.assertEqual(decode_bytes(encode_bytes(plain)), plain)

    def test_empty(self):
        self.assertEqual(decode_bytes(b""), b"")

    def test_is_obfuscated_true(self):
        self.assertTrue(is_obfuscated(encode_bytes(b"hello")))

    def test_is_obfuscated_false_for_plain(self):
        self.assertFalse(is_obfuscated(b"hello world"))

    def test_is_obfuscated_false_for_empty(self):
        # Nothing to decode is not "obfuscated".
        self.assertFalse(is_obfuscated(b""))

    def test_trailing_single_symbol_raises(self):
        with self.assertRaises(WordlistFormatError):
            decode_bytes(encode_bytes(b"a") + bytes([SYM_BASE]))

    def test_out_of_range_symbol_raises(self):
        with self.assertRaises(WordlistFormatError):
            decode_bytes(bytes([0x20, 0x20]))

    def test_decode_text_gbk(self):
        plain = "甲乙丙".encode("gbk")
        self.assertEqual(decode_text(encode_bytes(plain)), "甲乙丙")

    def test_decode_lines_strips_and_drops_blanks(self):
        plain = "甲乙\r\n\r\n  丙丁  \r\n".encode("gbk")
        self.assertEqual(decode_lines(encode_bytes(plain)), ["甲乙", "丙丁"])


class CatalogueTest(unittest.TestCase):
    def test_parses_three_fields(self):
        text = "1 甲乙网站 example.dat\r\n"
        got = parse_catalogue(text)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].category, 1)
        self.assertEqual(got[0].label, "甲乙网站")
        self.assertEqual(got[0].filename, "example.dat")

    def test_skips_short_lines(self):
        self.assertEqual(parse_catalogue("only two\r\n"), [])

    def test_skips_non_numeric_category(self):
        self.assertEqual(parse_catalogue("x label file.dat\r\n"), [])

    def test_skips_blank_lines(self):
        self.assertEqual(parse_catalogue("\r\n\r\n"), [])

    def test_entry_to_dict(self):
        entry = CatalogueEntry(1, "label", "f.dat")
        self.assertEqual(set(entry.to_dict()), {"category", "label",
                                                "filename"})


class LoadFileTest(unittest.TestCase):
    def test_loads_obfuscated(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "example.dat"
            path.write_bytes(encode_bytes("甲乙\r\n丙丁\r\n".encode("gbk")))
            loaded, index = load_file(path)
            self.assertEqual(loaded.terms, 2)
            self.assertTrue(loaded.obfuscated)
            self.assertEqual(index.size, 2)
            self.assertIsNotNone(index.get("甲乙"))

    def test_terms_are_attested(self):
        """Vendor data is attested; parsed prose is not."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "example.dat"
            path.write_bytes(encode_bytes("甲乙".encode("gbk")))
            _, index = load_file(path)
            self.assertTrue(index.get("甲乙").attested)

    def test_no_level_is_invented(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "example.dat"
            path.write_bytes(encode_bytes("甲乙".encode("gbk")))
            _, index = load_file(path)
            self.assertIsNone(index.get("甲乙").max_level)

    def test_plain_text_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "example.dat"
            path.write_bytes("甲乙\r\n".encode("gbk"))
            loaded, index = load_file(path)
            self.assertFalse(loaded.obfuscated)
            self.assertEqual(index.size, 1)

    def test_empty_file_raises(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "empty.dat"
            path.write_bytes(b"")
            with self.assertRaises(WordlistFormatError):
                load_file(path)

    def test_uses_supplied_index(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "example.dat"
            path.write_bytes(encode_bytes("甲乙".encode("gbk")))
            index = WordlistIndex()
            load_file(path, index=index)
            self.assertEqual(index.size, 1)

    def test_label_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "example.dat"
            path.write_bytes(encode_bytes("甲乙".encode("gbk")))
            loaded, _ = load_file(path, label="测试类")
            self.assertEqual(loaded.label, "测试类")


class LoadDirectoryTest(unittest.TestCase):
    def _write_tree(self, td: Path) -> None:
        (td / CATALOGUE).write_bytes(
            encode_bytes("1 甲乙类 aaa.dat\r\n1 丙丁类 bbb.dat\r\n"
                         .encode("gbk")))
        (td / "aaa.dat").write_bytes(encode_bytes("甲乙\r\n".encode("gbk")))
        (td / "bbb.dat").write_bytes(encode_bytes("丙丁\r\n戊己\r\n"
                                                  .encode("gbk")))

    def test_loads_catalogued_files(self):
        with tempfile.TemporaryDirectory() as td:
            self._write_tree(Path(td))
            report, index = load_directory(Path(td))
            self.assertEqual(len(report.lists), 2)
            self.assertEqual(report.total_terms, 3)
            self.assertEqual(index.size, 3)

    def test_labels_from_catalogue(self):
        with tempfile.TemporaryDirectory() as td:
            self._write_tree(Path(td))
            report, _ = load_directory(Path(td))
            labels = {entry.filename: entry.label for entry in report.lists}
            self.assertEqual(labels["aaa.dat"], "甲乙类")

    def test_missing_catalogued_file_reported(self):
        with tempfile.TemporaryDirectory() as td:
            self._write_tree(Path(td))
            (Path(td) / "bbb.dat").unlink()
            report, _ = load_directory(Path(td))
            self.assertEqual(report.missing, ["bbb.dat"])

    def test_uncatalogued_list_still_loaded(self):
        """A shipped list the catalogue omits must still apply: ignoring it
        would under-filter."""
        with tempfile.TemporaryDirectory() as td:
            self._write_tree(Path(td))
            (Path(td) / "zzzfil.dat").write_bytes(
                encode_bytes("庚辛\r\n".encode("gbk")))
            report, index = load_directory(Path(td))
            self.assertIn("zzzfil.dat",
                          [entry.filename for entry in report.lists])
            self.assertIsNotNone(index.get("庚辛"))

    def test_no_catalogue_is_tolerated(self):
        with tempfile.TemporaryDirectory() as td:
            # A name matching the *fil.dat glob, so it is picked up without a
            # catalogue entry.
            (Path(td) / "zzzfil.dat").write_bytes(
                encode_bytes("甲乙\r\n".encode("gbk")))
            report, index = load_directory(Path(td))
            self.assertEqual(report.catalogue, [])
            self.assertEqual(index.size, 1)

    def test_summary_reports_counts_not_terms(self):
        """The summary carries file names and labels but never a term.

        Labels come from the catalogue (a category name), so they legitimately
        appear; terms from inside the lists must not.
        """
        with tempfile.TemporaryDirectory() as td:
            self._write_tree(Path(td))
            report, _ = load_directory(Path(td))
            summary = report.summary()
            self.assertEqual(summary["total_terms"], 3)
            for entry in summary["lists"]:
                self.assertEqual(set(entry),
                                 {"filename", "label", "terms",
                                  "decoded_bytes", "obfuscated"})
                self.assertNotIn("terms_list", entry)
            # Terms are absent from the structure itself.
            self.assertNotIn("戊己", repr(summary))


@unittest.skipUnless(VENDOR.exists(), "vendor tree not present")
class VendorTreeTest(unittest.TestCase):
    """Reads the real files. Asserts structure, never contents."""

    @classmethod
    def setUpClass(cls):
        cls.report, cls.index = load_directory(VENDOR)

    def test_five_lists_load(self):
        self.assertEqual(len(self.report.lists), 5)

    def test_nothing_missing(self):
        self.assertEqual(self.report.missing, [])

    def test_all_obfuscated(self):
        self.assertTrue(all(entry.obfuscated for entry in self.report.lists))

    def test_catalogue_has_five_entries(self):
        self.assertEqual(len(self.report.catalogue), 5)

    def test_catalogue_names_are_distinct(self):
        names = [entry.filename for entry in self.report.catalogue]
        self.assertEqual(len(names), len(set(names)))

    def test_term_count_is_plausible(self):
        # Thousands, not tens and not millions.
        self.assertGreater(self.report.total_terms, 1000)
        self.assertLess(self.report.total_terms, 100_000)

    def test_index_deduplicates_across_lists(self):
        # Two lists share terms, so the index is smaller than the sum.
        self.assertLess(self.index.size, self.report.total_terms)

    def test_terms_are_attested(self):
        for entry in self.report.lists[:1]:
            self.assertTrue(entry.obfuscated)

    def test_summary_does_not_leak_terms(self):
        blob = repr(self.report.summary())
        self.assertNotIn("adwapp", blob.replace("adwapp.dat", ""))
        self.assertIn("total_terms", blob)


if __name__ == "__main__":
    unittest.main()
