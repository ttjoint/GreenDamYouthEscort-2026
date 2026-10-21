"""Tests for the legacy 67-slot policy array.

Every assertion here is pinned to a reverse-engineered fact in
``analysis/03-DATA-FORMATS.md`` §F-002. If a refactor breaks one of these, the
doc and the code have diverged - which is the failure mode this file exists to
catch.
"""
from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam import sysdat  # noqa: E402

SYSDIR = ROOT.parent / "LH-installed-tree" / "Windows" / "System32"


class SlotNumberingTest(unittest.TestCase):
    def test_index_is_slot_minus_one(self):
        # CLOSED by disassembly at 0x40bc16: [esp+0x50] is policy 5.
        self.assertEqual(sysdat.slot_index(1), 0)
        self.assertEqual(sysdat.slot_index(5), 4)
        self.assertEqual(sysdat.slot_index(25), 24)

    def test_out_of_range_rejected(self):
        for bad in (0, -1, 26, 67, 68):
            with self.assertRaises(ValueError):
                sysdat.slot_index(bad)

    def test_only_25_policies_are_named(self):
        # The blob has 67 slots, but XNet2's name table stops at 25. Slots
        # 26..67 are reserved and always zero; naming them would be invention.
        self.assertEqual(sysdat.NAMED_SLOTS, 25)
        self.assertEqual(len(sysdat.SLOT_NAMES), 25)
        self.assertLess(sysdat.NAMED_SLOTS, sysdat.SLOT_COUNT)

    def test_reserved_slots_readable_by_index_only(self):
        arr = sysdat.PolicyArray(values=(0,) * 66 + (7,))
        self.assertEqual(arr.raw(66), 7)
        self.assertIn((67, 7), arr.nonzero())
        with self.assertRaises(ValueError):
            arr.get(67)

    def test_names_cover_every_named_slot(self):
        self.assertEqual(len(sysdat.SLOT_NAMES), sysdat.NAMED_SLOTS)
        self.assertEqual(sysdat.slot_name(1), "block_network")
        self.assertEqual(sysdat.slot_name(25), "show_mascot")

    def test_slot_17_is_screenshot_not_shutdown(self):
        # REFUTED claim: "17 = allow auto shutdown" came from mixing in the
        # log-event numbering. The name table at 0x68dbc says screenshot.
        self.assertEqual(sysdat.slot_name(17), "allow_auto_screenshot")
        self.assertNotIn("shutdown", sysdat.slot_name(17))


class DecodeTest(unittest.TestCase):
    def test_size_is_67_dwords(self):
        self.assertEqual(sysdat.BLOB_SIZE, 268)
        self.assertEqual(sysdat.SLOT_COUNT, 67)

    def test_decode_rejects_wrong_size(self):
        for n in (0, 264, 272):
            with self.assertRaises(ValueError):
                sysdat.decode(b"\x00" * n)

    def test_round_trip(self):
        values = tuple(range(sysdat.SLOT_COUNT))
        blob = sysdat.encode(sysdat.PolicyArray(values=values))
        self.assertEqual(len(blob), sysdat.BLOB_SIZE)
        self.assertEqual(sysdat.decode(blob).values, values)

    def test_hand_built_array_is_not_attested(self):
        arr = sysdat.PolicyArray(values=(0,) * sysdat.SLOT_COUNT)
        self.assertFalse(arr.attested)
        self.assertTrue(sysdat.decode(b"\x00" * sysdat.BLOB_SIZE).attested)


class ShippedBlobTest(unittest.TestCase):
    """Pinned to the actual bytes on the CD, when they are available."""

    def setUp(self):
        self.path = SYSDIR / "sys.dat"
        if not self.path.exists():
            self.skipTest("legacy tree not present")

    def test_shipped_nonzero_slots(self):
        arr = sysdat.load(self.path)
        self.assertEqual(
            tuple(i - 1 for i, _ in arr.nonzero()), sysdat.SHIPPED_NONZERO)

    def test_value_domain(self):
        arr = sysdat.load(self.path)
        self.assertLessEqual(set(arr.values), {0, 1, 2, 3, 5, 14})

    def test_xconfigs_is_byte_identical(self):
        other = SYSDIR / "xconfigs.dat"
        if not other.exists():
            self.skipTest("xconfigs.dat not present")
        self.assertEqual(self.path.read_bytes(), other.read_bytes())

    def test_preset_differs_from_shipped_in_two_slots(self):
        """The preset branch and the installed file legitimately disagree.

        ``PRESET_LEVEL_6`` is what XNet2 writes when preset 6 is *selected*;
        ``sys.dat`` is the state left after installation. Two slots differ, and
        this test exists so nobody "fixes" one to match the other.
        """
        arr = sysdat.load(self.path)
        differing = {
            idx: (preset, arr.values[idx])
            for idx, preset in sysdat.PRESET_LEVEL_6.items()
            if arr.values[idx] != preset
        }
        self.assertEqual(differing, {11: (2, 0), 18: (14, 3)})


class SemanticFlagsTest(unittest.TestCase):
    def _arr(self, **overrides):
        values = [0] * sysdat.SLOT_COUNT
        for slot_no, value in overrides.items():
            values[sysdat.slot_index(int(slot_no.lstrip("s")))] = value
        return sysdat.PolicyArray(values=tuple(values))

    def test_filtering_disabled_reads_slot_2(self):
        self.assertFalse(self._arr().filtering_disabled)
        self.assertTrue(self._arr(s2=1).filtering_disabled)

    def test_image_filter_needs_both_switches(self):
        # slot 13 enables, slot 5 carries the level; level 0 means off.
        self.assertFalse(self._arr(s13=1).image_filter_enabled)
        self.assertFalse(self._arr(s5=2).image_filter_enabled)
        self.assertTrue(self._arr(s13=1, s5=2).image_filter_enabled)

    def test_image_filter_level_is_three_way(self):
        # The UI's low/mid/high maps onto the three detection modes.
        for level in (0, 1, 2):
            self.assertEqual(self._arr(s5=level).image_filter_level, level)

    def test_keyword_filter_disabled_reads_slot_10(self):
        self.assertFalse(self._arr().keyword_filter_disabled)
        self.assertTrue(self._arr(s10=1).keyword_filter_disabled)

    def test_screenshot_slots(self):
        arr = self._arr(s17=1, s19=14)
        self.assertTrue(arr.auto_screenshot)
        self.assertEqual(arr.screenshot_retention_days, 14)


if __name__ == "__main__":
    unittest.main()
