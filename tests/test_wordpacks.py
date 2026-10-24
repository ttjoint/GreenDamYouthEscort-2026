"""Word packs: the model (pure) and the CLI (file access).

Split because the engine performs no I/O. ``green_dam.wordpacks`` decides what a
pack is and whether one is valid; ``green_dam.cli_words`` reads and writes the
files. The model tests need no filesystem, and the CLI tests need no knowledge of
the format.

What these tests are guarding is not the JSON parsing - that is easy. It is the
three properties that make the pack system answer the complaint the original
product earned:

1. **A pack can be turned off.** Disabling removes its terms from the effective
   set without hiding the pack.
2. **A pack can be inspected.** The count is reported; the terms are not, because
   a status output that lists the terms is a copy of the list.
3. **A bad pack cannot escape the directory.** The id becomes a filename, so an
   id containing ``..`` or a separator must be refused before anything is
   written.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from green_dam import wordpacks as wp                      # noqa: E402
from green_dam.cli_words import (                          # noqa: E402
    export_pack,
    import_pack,
    load_index,
    set_enabled,
)


def _pack_text(pack_id: str = "a", terms=("甲", "乙"), **extra) -> str:
    payload = {"id": pack_id, "name": f"Pack {pack_id}", "category": "adult",
               "licence": "CC0-1.0", "source": "test", "terms": list(terms)}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


class ModelTest(unittest.TestCase):
    """``green_dam.wordpacks``: no filesystem involved."""

    def test_valid_pack_parses(self):
        pack = wp.parse_pack(_pack_text())
        self.assertEqual(pack.id, "a")
        self.assertEqual(pack.terms, ("甲", "乙"))
        self.assertFalse(pack.error)

    def test_duplicate_terms_collapse(self):
        pack = wp.parse_pack(_pack_text(terms=("甲", "甲", "乙")))
        self.assertEqual(pack.terms, ("甲", "乙"))

    def test_blank_terms_are_dropped(self):
        pack = wp.parse_pack(_pack_text(terms=("甲", "  ", "", "乙")))
        self.assertEqual(pack.terms, ("甲", "乙"))

    def test_invalid_json_is_an_error_not_an_exception(self):
        """A directory with one corrupt pack must still list the others."""
        pack = wp.parse_pack("{not json", fallback_id="bad")
        self.assertTrue(pack.error)
        self.assertIn("not valid JSON", pack.error)

    def test_non_object_document_is_an_error(self):
        pack = wp.parse_pack("[1,2,3]", fallback_id="x")
        self.assertTrue(pack.error)

    def test_terms_must_be_a_list(self):
        pack = wp.parse_pack(json.dumps({"id": "a", "terms": "甲"}))
        self.assertTrue(pack.error)
        self.assertIn("list", pack.error)

    def test_unknown_category_becomes_other(self):
        pack = wp.parse_pack(_pack_text(category="nonsense"))
        self.assertEqual(pack.category, "other")

    def test_enabled_defaults_to_true(self):
        self.assertTrue(wp.parse_pack(_pack_text()).enabled)

    def test_enabled_false_is_honoured(self):
        pack = wp.parse_pack(_pack_text(enabled=False))
        self.assertFalse(pack.enabled)
        self.assertFalse(pack.usable)

    def test_oversized_pack_is_refused(self):
        big = [f"t{i}" for i in range(wp.TERM_LIMIT + 1)]
        pack = wp.parse_pack(_pack_text(terms=big))
        self.assertTrue(pack.error)
        self.assertIn("limit", pack.error)


class IdSafetyTest(unittest.TestCase):
    """The id becomes a filename, so it must not be able to leave the directory."""

    def test_normal_ids_are_accepted(self):
        for pack_id in ("adult", "my-list", "list_2", "a", "0day"):
            self.assertEqual(wp.validate_id(pack_id), pack_id)

    def test_traversal_is_rejected(self):
        for bad in ("../evil", "a/b", "..", "a\\b"):
            with self.assertRaises(wp.PackError, msg=bad):
                wp.validate_id(bad)

    def test_uppercase_is_rejected(self):
        """Case-insensitive filesystems would make ``A`` and ``a`` collide."""
        with self.assertRaises(wp.PackError):
            wp.validate_id("Adult")

    def test_empty_is_rejected(self):
        with self.assertRaises(wp.PackError):
            wp.validate_id("")

    def test_leading_punctuation_is_rejected(self):
        for bad in ("-lead", "_lead", ".dot"):
            with self.assertRaises(wp.PackError, msg=bad):
                wp.validate_id(bad)

    def test_pack_with_a_traversing_id_does_not_load(self):
        pack = wp.parse_pack(json.dumps({"id": "../evil", "terms": ["x"]}))
        self.assertTrue(pack.error)


class IndexTest(unittest.TestCase):
    def _index(self, *packs):
        return wp.index_of([wp.parse_pack(_pack_text(**p)) for p in packs])

    def test_terms_merge_across_packs(self):
        index = self._index({"id": "a", "terms": ("甲", "乙")},
                            {"id": "b", "terms": ("乙", "丙")})
        self.assertEqual(index.terms, ("甲", "乙", "丙"))

    def test_disabled_pack_contributes_nothing(self):
        index = self._index({"id": "a", "terms": ("甲",)},
                            {"id": "b", "terms": ("乙",), "enabled": False})
        self.assertEqual(index.terms, ("甲",))

    def test_disabled_pack_is_still_listed(self):
        """Disabling is not hiding: 'why is this not blocked' needs an answer."""
        index = self._index({"id": "a", "terms": ("甲",), "enabled": False})
        self.assertEqual(len(index.packs), 1)
        self.assertFalse(index.packs[0].usable)

    def test_errored_pack_contributes_nothing(self):
        good = wp.parse_pack(_pack_text("good"))
        bad = wp.parse_pack("{broken", fallback_id="bad")
        index = wp.index_of([good, bad])
        self.assertEqual(index.terms, ("甲", "乙"))
        self.assertEqual(len(index.warnings), 1)

    def test_summary_reports_counts_not_terms(self):
        """A status output that lists the terms is a copy of the list."""
        index = self._index({"id": "a", "terms": ("秘密词",)})
        blob = json.dumps(index.summary(), ensure_ascii=False)
        self.assertNotIn("秘密词", blob)
        self.assertIn("total_terms", blob)

    def test_packs_are_sorted_by_id(self):
        index = self._index({"id": "z"}, {"id": "a"}, {"id": "m"})
        self.assertEqual([p.id for p in index.packs], ["a", "m", "z"])


class FileAccessTest(unittest.TestCase):
    """``cli_words``: the file half."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "packs"

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, pack_id: str, text: str | None = None) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{pack_id}{wp.PACK_SUFFIX}"
        path.write_text(text or _pack_text(pack_id), encoding="utf-8")
        return path

    def test_missing_directory_is_empty_not_an_error(self):
        index = load_index(self.dir)
        self.assertEqual(index.packs, ())

    def test_loads_every_pack(self):
        self._write("a")
        self._write("b")
        self.assertEqual(len(load_index(self.dir).packs), 2)

    def test_disable_persists(self):
        self._write("a")
        set_enabled(self.dir, "a", False)
        self.assertFalse(load_index(self.dir).get("a").enabled)

    def test_disable_then_enable_round_trips(self):
        self._write("a")
        set_enabled(self.dir, "a", False)
        set_enabled(self.dir, "a", True)
        self.assertTrue(load_index(self.dir).get("a").enabled)

    def test_disable_preserves_terms(self):
        self._write("a")
        set_enabled(self.dir, "a", False)
        self.assertEqual(load_index(self.dir).get("a").terms, ("甲", "乙"))

    def test_disable_missing_pack_raises(self):
        with self.assertRaises(wp.PackError):
            set_enabled(self.dir, "nope", False)

    def test_disable_broken_pack_raises(self):
        self._write("a", "{broken")
        with self.assertRaises(wp.PackError):
            set_enabled(self.dir, "a", False)

    def test_import_copies_the_pack(self):
        source = Path(self._tmp.name) / "incoming.json"
        source.write_text(_pack_text("incoming"), encoding="utf-8")
        pack = import_pack(source, self.dir)
        self.assertEqual(pack.id, "incoming")
        self.assertTrue(load_index(self.dir).get("incoming"))

    def test_import_can_rename(self):
        source = Path(self._tmp.name) / "incoming.json"
        source.write_text(_pack_text("incoming"), encoding="utf-8")
        pack = import_pack(source, self.dir, override_id="renamed")
        self.assertEqual(pack.id, "renamed")

    def test_import_refuses_a_traversing_id(self):
        source = Path(self._tmp.name) / "evil.json"
        source.write_text(json.dumps({"id": "../evil", "terms": ["x"]}),
                          encoding="utf-8")
        with self.assertRaises(wp.PackError):
            import_pack(source, self.dir)

    def test_import_refuses_a_missing_file(self):
        with self.assertRaises(wp.PackError):
            import_pack(Path(self._tmp.name) / "absent.json", self.dir)

    def test_export_round_trips(self):
        self._write("a")
        target = Path(self._tmp.name) / "out.json"
        export_pack(self.dir, "a", target)
        self.assertTrue(target.is_file())
        reloaded = wp.parse_pack(target.read_text(encoding="utf-8"))
        self.assertEqual(reloaded.terms, ("甲", "乙"))

    def test_export_missing_pack_raises(self):
        with self.assertRaises(wp.PackError):
            export_pack(self.dir, "nope", Path(self._tmp.name) / "x.json")

    def test_corrupt_pack_does_not_break_the_directory(self):
        self._write("good")
        self._write("bad", "{broken")
        index = load_index(self.dir)
        self.assertEqual(len(index.packs), 2)
        self.assertEqual(len(index.usable), 1)


if __name__ == "__main__":
    unittest.main()
