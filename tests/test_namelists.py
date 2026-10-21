"""Tests for the four legacy name-list containers.

Discipline: fixtures are invented (single CJK characters 甲乙丙丁, or ASCII
placeholders). No legacy word-list content appears here, and no assertion
prints legacy entries - only counts and structure.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam.namelist import RECORD_SIZE  # noqa: E402
from green_dam.namelists import (  # noqa: E402
    CAPACITY,
    SPEC,
    ListFull,
    NameList,
    NameLists,
    load_lists,
)


class CapacityTest(unittest.TestCase):
    """Capacities are pinned to the disassembly evidence, not guessed."""

    def test_capacities_match_the_evidence(self):
        self.assertEqual(CAPACITY["A"], 100)    # 0x64  @ 0x2d34
        self.assertEqual(CAPACITY["C"], 1000)   # 0x3e8 @ 0x1acd
        self.assertEqual(CAPACITY["D"], 1500)   # 0x5dc @ 0x27b5
        self.assertIsNone(CAPACITY["B"])        # no immediate compare found

    def test_spec_covers_every_list(self):
        self.assertEqual(set(SPEC), set(CAPACITY))

    def test_defaults_come_from_the_tables(self):
        for name in CAPACITY:
            lst = NameList(name=name)  # type: ignore[arg-type]
            self.assertEqual(lst.capacity, CAPACITY[name])
            self.assertEqual(lst.kind, SPEC[name][1])
            self.assertEqual(lst.role, SPEC[name][0])

    def test_unknown_name_rejected(self):
        with self.assertRaises(ValueError):
            NameList(name="E")  # type: ignore[arg-type]


class AddRemoveTest(unittest.TestCase):
    def test_add_and_contains(self):
        lst = NameList(name="A")
        self.assertTrue(lst.add("abc"))
        self.assertTrue(lst.contains("abc"))
        self.assertEqual(len(lst), 1)

    def test_add_is_case_insensitive_for_dedupe(self):
        # Every legacy add path lowercased before comparing (0x14076).
        lst = NameList(name="A")
        lst.add("abc")
        self.assertFalse(lst.add("ABC"))
        self.assertEqual(len(lst), 1)

    def test_empty_text_rejected(self):
        lst = NameList(name="A")
        self.assertFalse(lst.add(""))
        self.assertEqual(len(lst), 0)

    def test_dedupe_can_be_disabled(self):
        lst = NameList(name="A")
        lst.add("abc")
        self.assertTrue(lst.add("abc", dedupe=False))
        self.assertEqual(len(lst), 2)

    def test_remove(self):
        lst = NameList(name="A")
        lst.add("abc")
        self.assertTrue(lst.remove("ABC"))
        self.assertFalse(lst.remove("ABC"))
        self.assertEqual(len(lst), 0)

    def test_full_raises(self):
        lst = NameList(name="A")
        for i in range(CAPACITY["A"]):
            lst.add(f"x{i}")
        self.assertTrue(lst.full)
        with self.assertRaises(ListFull):
            lst.add("overflow")

    def test_unbounded_list_never_full(self):
        lst = NameList(name="B")
        for i in range(CAPACITY["A"] + 5):
            lst.add(f"x{i}")
        self.assertFalse(lst.full)


class MatchTest(unittest.TestCase):
    def test_exact_kind(self):
        lst = NameList(name="B")   # exact
        lst.add("proc")
        self.assertIsNotNone(lst.match("proc"))
        self.assertIsNotNone(lst.match("PROC"))
        self.assertIsNone(lst.match("process"))   # exact, not substring

    def test_substring_kind(self):
        lst = NameList(name="A")   # substring
        lst.add("bad.example")
        self.assertIsNotNone(lst.match("http://bad.example/x"))
        self.assertIsNone(lst.match("good.example"))

    def test_rule_kind(self):
        lst = NameList(name="C")   # rule
        lst.add("{甲,乙}[丙,丁]")
        self.assertIsNotNone(lst.match("甲丙"))
        self.assertIsNone(lst.match("甲戊"))

    def test_match_rule_returns_both(self):
        lst = NameList(name="C")
        lst.add("{甲}[乙]")
        hit = lst.match_rule("甲乙")
        self.assertIsNotNone(hit)
        record, rule = hit  # type: ignore[misc]
        self.assertEqual(record.text, "{甲}[乙]")
        self.assertTrue(rule.matches("甲乙"))

    def test_empty_list_matches_nothing(self):
        for name in CAPACITY:
            self.assertIsNone(NameList(name=name).match("anything"))  # type: ignore[arg-type]


class BlobTest(unittest.TestCase):
    def test_round_trip(self):
        lst = NameList(name="D")
        lst.add("甲")
        lst.add("乙")
        blob = lst.to_blob()
        self.assertEqual(len(blob), 2 * RECORD_SIZE)
        back = NameList.from_blob("D", blob)
        self.assertEqual([r.text for r in back], ["甲", "乙"])

    def test_from_blob_rejects_partial_record(self):
        with self.assertRaises(ValueError):
            NameList.from_blob("D", b"\x00" * (RECORD_SIZE + 1))

    def test_from_blob_skips_unused_slots(self):
        used = NameList(name="D")
        used.add("甲")
        blob = used.to_blob()
        unused = b"\x00" * RECORD_SIZE          # flag not set
        back = NameList.from_blob("D", blob + unused)
        self.assertEqual(len(back), 1)

    def test_from_blob_enforces_capacity(self):
        # Build a blob with more records than list A allows, all flagged in use.
        too_many = NameList(name="A")
        too_many.capacity = None                # bypass while building
        for i in range(CAPACITY["A"] + 1):
            too_many.add(f"x{i}")
        with self.assertRaises(ListFull):
            NameList.from_blob("A", too_many.to_blob())


class NameListsTest(unittest.TestCase):
    def test_all_four_present(self):
        lists = NameLists()
        self.assertEqual(set(lists.lists), {"A", "B", "C", "D"})

    def test_indexing(self):
        lists = NameLists()
        self.assertEqual(lists["A"].name, "A")
        self.assertEqual(lists["C"].kind, "rule")

    def test_summary_is_counts_only(self):
        lists = NameLists()
        lists["A"].add("abc")
        summary = lists.summary()
        self.assertEqual(summary["A"]["count"], 1)
        self.assertEqual(summary["A"]["capacity"], 100)
        # The summary must not leak entries.
        self.assertNotIn("abc", repr(summary))

    def test_load_lists(self):
        src = NameList(name="C")
        src.add("{甲}[乙]")
        lists = load_lists({"C": src.to_blob()})
        self.assertEqual(len(lists["C"]), 1)
        self.assertEqual(len(lists["A"]), 0)


if __name__ == "__main__":
    unittest.main()
