"""Tests for policy configuration persistence.

The important properties: the format stays byte-identical to the legacy blob,
writes are atomic and validated, and the "shipped defaults" cannot silently
drift from the bytes on the CD.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam import config, sysdat  # noqa: E402

SYSDIR = ROOT.parent / "LH-installed-tree" / "Windows" / "System32"


def _shipped() -> sysdat.PolicyArray:
    path = SYSDIR / "sys.dat"
    if not path.exists():
        raise unittest.SkipTest("legacy tree not present")
    return sysdat.load(path)


class ShippedDefaultsTest(unittest.TestCase):
    def test_defaults_match_the_shipped_blob_exactly(self):
        """``default_for_fresh_install`` must equal sys.dat, not a paraphrase."""
        shipped = _shipped()
        self.assertEqual(config.default_for_fresh_install().values, shipped.values)

    def test_shipped_values_table_covers_nonzero_slots(self):
        self.assertEqual(
            tuple(sorted(config._SHIPPED_VALUES)), sysdat.SHIPPED_NONZERO)

    def test_shipped_defaults_pass_strict_validation(self):
        config.validate(config.default_for_fresh_install(), strict=True)


class ValidateTest(unittest.TestCase):
    def _arr(self, **changes):
        values = [0] * sysdat.SLOT_COUNT
        for slot_no, value in changes.items():
            values[sysdat.slot_index(int(slot_no.lstrip("s")))] = value
        return sysdat.PolicyArray(values=tuple(values))

    def test_reserved_slot_nonzero_rejected(self):
        values = [0] * sysdat.SLOT_COUNT
        values[30] = 1                      # slot 31, reserved
        with self.assertRaises(config.ConfigError):
            config.validate(sysdat.PolicyArray(values=tuple(values)))

    def test_out_of_domain_value_rejected_in_strict_mode(self):
        with self.assertRaises(config.ConfigError):
            config.validate(self._arr(s5=99), strict=True)

    def test_out_of_domain_allowed_when_not_strict(self):
        config.validate(self._arr(s5=99), strict=False)

    def test_wrong_slot_count_rejected(self):
        with self.assertRaises(ValueError):
            sysdat.PolicyArray(values=(0,) * 10)


class SetValuesTest(unittest.TestCase):
    def test_changes_are_addressed_by_policy_number(self):
        arr = config.default_for_fresh_install()
        out = config.set_values(arr, {5: 1})
        # policy 5 -> index 4
        self.assertEqual(out.values[4], 1)

    def test_original_untouched(self):
        arr = config.default_for_fresh_install()
        before = arr.values
        config.set_values(arr, {5: 0})
        self.assertEqual(arr.values, before)

    def test_invalid_policy_number_rejected(self):
        arr = config.default_for_fresh_install()
        for bad in (0, 26, 99):
            with self.assertRaises(ValueError):
                config.set_values(arr, {bad: 1})

    def test_reserved_slot_cannot_be_set(self):
        arr = config.default_for_fresh_install()
        with self.assertRaises(ValueError):
            config.set_values(arr, {40: 1})

    def test_result_is_validated(self):
        arr = config.default_for_fresh_install()
        with self.assertRaises(config.ConfigError):
            config.set_values(arr, {5: 77}, strict=True)


class SaveLoadTest(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "xconfigs.dat"
            arr = config.default_for_fresh_install()
            config.save(arr, path, source="test")
            self.assertEqual(config.load(path).values, arr.values)

    def test_format_is_byte_identical_to_legacy(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "xconfigs.dat"
            arr = _shipped()
            config.save(arr, path)
            self.assertEqual(path.read_bytes(), (SYSDIR / "sys.dat").read_bytes())

    def test_size_is_268(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "p.dat"
            config.save(config.default_for_fresh_install(), path)
            self.assertEqual(path.stat().st_size, 268)

    def test_sidecar_written(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "p.dat"
            config.save(config.default_for_fresh_install(), path, source="unit")
            meta = config.read_meta(path)
            self.assertIsNotNone(meta)
            self.assertEqual(meta["schema"], config.META_SCHEMA)
            self.assertEqual(meta["source"], "unit")
            self.assertTrue(meta["attested"]["slot_names"])
            self.assertFalse(meta["attested"]["score_to_action"])

    def test_sidecar_can_be_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "p.dat"
            config.save(config.default_for_fresh_install(), path, write_meta=False)
            self.assertIsNone(config.read_meta(path))

    def test_sidecar_lists_nonzero_policies_not_entries(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "p.dat"
            config.save(config.default_for_fresh_install(), path)
            meta = config.read_meta(path)
            names = {row["name"] for row in meta["nonzero"]}
            self.assertIn("image_filter_level", names)
            # Reserved slots never appear.
            self.assertTrue(all(row["policy"] <= 25 for row in meta["nonzero"]))

    def test_no_temp_files_left_behind(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "p.dat"
            config.save(config.default_for_fresh_install(), path)
            leftovers = [p.name for p in Path(td).iterdir()
                         if p.name.endswith(".tmp")]
            self.assertEqual(leftovers, [])

    def test_invalid_array_not_written(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "p.dat"
            values = [0] * sysdat.SLOT_COUNT
            values[5] = 99
            bad = sysdat.PolicyArray(values=tuple(values))
            with self.assertRaises(config.ConfigError):
                config.save(bad, path)
            self.assertFalse(path.exists())


class LoadTest(unittest.TestCase):
    def test_missing_file(self):
        with self.assertRaises(config.ConfigError):
            config.load(Path("does-not-exist.dat"))

    def test_truncated_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "p.dat"
            path.write_bytes(b"\x00" * 100)
            with self.assertRaises(config.ConfigError):
                config.load(path)

    def test_loads_the_real_xconfigs(self):
        path = SYSDIR / "xconfigs.dat"
        if not path.exists():
            self.skipTest("legacy tree not present")
        arr = config.load(path)
        self.assertEqual(arr.values, _shipped().values)

    def test_lenient_by_default_but_structural_errors_still_raise(self):
        # A real file may hold a value outside our domain; reading it must work.
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "p.dat"
            values = [0] * sysdat.SLOT_COUNT
            values[4] = 99
            path.write_bytes(sysdat.encode(sysdat.PolicyArray(values=tuple(values))))
            self.assertEqual(config.load(path).values[4], 99)

    def test_reserved_slot_nonzero_still_rejected_on_load(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "p.dat"
            values = [0] * sysdat.SLOT_COUNT
            values[40] = 1
            path.write_bytes(sysdat.encode(sysdat.PolicyArray(values=tuple(values))))
            with self.assertRaises(config.ConfigError):
                config.load(path)


class PathsTest(unittest.TestCase):
    def test_meta_sits_beside_blob(self):
        paths = config.ConfigPaths.for_blob(Path("/tmp/x/xconfigs.dat"))
        self.assertEqual(paths.meta.name, "xconfigs.dat" + config.META_SUFFIX)
        self.assertEqual(paths.meta.parent, paths.blob.parent)


if __name__ == "__main__":
    unittest.main()
