"""Tests for the install journal and uninstall planning.

Two properties carry the weight:

* rollback order is the reverse of install order
* a change with no inverse is *reported*, never silently dropped

The second is the same rule the rest of the project follows: "could not undo it"
must not look like "undid everything".
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam.journal import (  # noqa: E402
    PRODUCT,
    SCHEMA,
    Change,
    ChangeKind,
    JournalError,
    Manifest,
    UnreversibleChange,
    mark_rolled_back,
    plan_uninstall,
    verify_inverse,
)


def _broken_change(target: str = "bad") -> Change:
    """A Change with an empty inverse, bypassing the constructor's guard.

    The guard exists so a normal caller cannot record an unreversible change.
    Testing the planner's handling of one requires getting past it, which is what
    a corrupted journal on disk would look like.
    """
    broken = Change.__new__(Change)
    object.__setattr__(broken, "kind", ChangeKind.FILE)
    object.__setattr__(broken, "target", target)
    object.__setattr__(broken, "inverse", "")
    object.__setattr__(broken, "detail", {})
    return broken


def _manifest(*specs) -> Manifest:
    """Build a manifest from (kind, target) pairs, with a generic inverse."""
    manifest = Manifest()
    for kind, target in specs:
        manifest.record(kind, target, f"undo-{kind.value}")
    return manifest


class ChangeTest(unittest.TestCase):
    def test_requires_target(self):
        with self.assertRaises(JournalError):
            Change(kind=ChangeKind.FILE, target="", inverse="delete")

    def test_requires_inverse(self):
        # A change nobody thought about undoing cannot be recorded.
        with self.assertRaises(JournalError):
            Change(kind=ChangeKind.FILE, target="/x", inverse="")

    def test_round_trip(self):
        change = Change(kind=ChangeKind.REGISTRY, target="HKLM\\X",
                        inverse="restore", detail={"previous": "1"})
        back = Change.from_dict(change.to_dict())
        self.assertEqual(back.kind, change.kind)
        self.assertEqual(back.target, change.target)
        self.assertEqual(back.detail, change.detail)

    def test_repr_hides_detail(self):
        change = Change(kind=ChangeKind.SETTING, target="s",
                        inverse="restore", detail={"previous": "secret-value"})
        self.assertNotIn("secret-value", repr(change))

    def test_from_dict_missing_fields(self):
        with self.assertRaises(JournalError):
            Change.from_dict({"kind": "file"})

    def test_from_dict_unknown_kind(self):
        with self.assertRaises(JournalError):
            Change.from_dict({"kind": "teleport", "target": "x",
                              "inverse": "undo"})

    def test_from_dict_bad_detail(self):
        with self.assertRaises(JournalError):
            Change.from_dict({"kind": "file", "target": "x",
                              "inverse": "undo", "detail": "nope"})


class ManifestTest(unittest.TestCase):
    def test_record_preserves_order(self):
        manifest = _manifest((ChangeKind.FILE, "a"), (ChangeKind.FILE, "b"),
                             (ChangeKind.FILE, "c"))
        self.assertEqual([c.target for c in manifest.changes], ["a", "b", "c"])

    def test_kinds_are_counts_not_targets(self):
        manifest = _manifest((ChangeKind.FILE, "a"), (ChangeKind.FILE, "b"),
                             (ChangeKind.SERVICE, "s"))
        got = manifest.kinds()
        self.assertEqual(got, {"file": 2, "service": 1})
        self.assertNotIn("a", repr(got))

    def test_round_trip(self):
        manifest = _manifest((ChangeKind.FIREWALL_RULE, "rule-1"),
                             (ChangeKind.CERTIFICATE, "thumb"))
        manifest.version = "0.1.0"
        back = Manifest.from_json(manifest.to_json())
        self.assertEqual(len(back), 2)
        self.assertEqual(back.version, "0.1.0")
        self.assertEqual([c.target for c in back.changes],
                         ["rule-1", "thumb"])

    def test_rejects_foreign_product(self):
        payload = json.loads(_manifest().to_json())
        payload["product"] = "something-else"
        with self.assertRaises(JournalError):
            Manifest.from_dict(payload)

    def test_rejects_wrong_schema(self):
        payload = json.loads(_manifest().to_json())
        payload["schema"] = SCHEMA + 1
        with self.assertRaises(JournalError):
            Manifest.from_dict(payload)

    def test_rejects_invalid_json(self):
        with self.assertRaises(JournalError):
            Manifest.from_json("{not json")

    def test_rejects_non_object(self):
        with self.assertRaises(JournalError):
            Manifest.from_dict([1, 2, 3])

    def test_rejects_non_list_changes(self):
        payload = json.loads(_manifest().to_json())
        payload["changes"] = "nope"
        with self.assertRaises(JournalError):
            Manifest.from_dict(payload)


class StorageTest(unittest.TestCase):
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sub" / "journal.json"
            manifest = _manifest((ChangeKind.FILE, "a"))
            manifest.save(path)
            self.assertEqual(len(Manifest.load(path)), 1)

    def test_no_temp_left(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "j.json"
            _manifest((ChangeKind.FILE, "a")).save(path)
            leftovers = [p.name for p in Path(td).iterdir()
                         if p.name.endswith(".tmp")]
            self.assertEqual(leftovers, [])

    def test_missing_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(JournalError):
                Manifest.load(Path(td) / "absent")

    def test_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "j.json"
            _manifest((ChangeKind.FILE, "a")).save(path)
            _manifest((ChangeKind.FILE, "b"), (ChangeKind.FILE, "c")).save(path)
            self.assertEqual(len(Manifest.load(path)), 2)


class PlanOrderTest(unittest.TestCase):
    """The whole point of a journal: undo in reverse."""

    def test_steps_run_in_reverse_install_order(self):
        manifest = _manifest((ChangeKind.FIREWALL_RULE, "r"),
                             (ChangeKind.CERTIFICATE, "c"),
                             (ChangeKind.SERVICE, "s"),
                             (ChangeKind.REGISTRY, "reg"),
                             (ChangeKind.FILE, "f"),
                             (ChangeKind.SETTING, "set"))
        plan = plan_uninstall(manifest)
        targets = [s.change.target for s in plan.steps]
        self.assertEqual(targets, ["set", "f", "reg", "s", "c", "r"])

    def test_ordering_numbers_are_sequential(self):
        plan = plan_uninstall(_manifest((ChangeKind.FILE, "a"),
                                        (ChangeKind.FILE, "b")))
        self.assertEqual([s.order for s in plan.steps], [1, 2])

    def test_architecture_order_is_representable(self):
        # The order architecture.md lists: firewall, CA, service, registry,
        # files, settings. Undo runs it backwards.
        manifest = _manifest((ChangeKind.FIREWALL_RULE, "rule"),
                             (ChangeKind.CERTIFICATE, "ca"),
                             (ChangeKind.SERVICE, "svc"),
                             (ChangeKind.REGISTRY, "reg"),
                             (ChangeKind.FILE, "files"),
                             (ChangeKind.SETTING, "settings"))
        plan = plan_uninstall(manifest)
        self.assertEqual([s.change.kind for s in plan.steps],
                         [ChangeKind.SETTING, ChangeKind.FILE,
                          ChangeKind.REGISTRY, ChangeKind.SERVICE,
                          ChangeKind.CERTIFICATE, ChangeKind.FIREWALL_RULE])


class PlanSafetyTest(unittest.TestCase):
    def test_empty_journal_warns(self):
        plan = plan_uninstall(Manifest())
        self.assertEqual(plan.count, 0)
        self.assertTrue(any("empty" in w for w in plan.warnings))

    def test_already_rolled_back_is_a_noop(self):
        manifest = _manifest((ChangeKind.FILE, "a"))
        manifest.rolled_back = True
        plan = plan_uninstall(manifest)
        self.assertTrue(plan.already_rolled_back)
        self.assertEqual(plan.count, 0)
        self.assertIn("already rolled back", plan.render())

    def test_unreversible_is_reported_not_dropped(self):
        manifest = Manifest()
        manifest.record(ChangeKind.FILE, "good", "delete")
        manifest.changes.append(_broken_change("bad"))

        plan = plan_uninstall(manifest)
        self.assertFalse(plan.complete)
        self.assertEqual(len(plan.unreversible), 1)
        self.assertIn("cannot be undone", plan.render())

    def test_strict_raises_instead(self):
        manifest = Manifest()
        manifest.changes.append(_broken_change("bad"))
        with self.assertRaises(UnreversibleChange):
            plan_uninstall(manifest, strict=True)

    def test_plan_never_reboots(self):
        plan = plan_uninstall(_manifest((ChangeKind.FILE, "a")))
        self.assertIn("no reboot", plan.render().lower())


class RenderTest(unittest.TestCase):
    def test_lists_steps_in_order(self):
        plan = plan_uninstall(_manifest((ChangeKind.FILE, "a"),
                                        (ChangeKind.FILE, "b")))
        text = plan.render()
        self.assertIn("would perform 2 step", text)
        self.assertLess(text.index(" b"), text.index(" a"))

    def test_summary_has_counts_only(self):
        manifest = _manifest((ChangeKind.FILE, "secret-target"),
                             (ChangeKind.SERVICE, "secret-service"))
        plan = plan_uninstall(manifest)
        summary = plan.summary()
        blob = json.dumps(summary)
        self.assertNotIn("secret-target", blob)
        self.assertNotIn("secret-service", blob)
        self.assertEqual(summary["steps"], 2)
        self.assertTrue(summary["complete"])

    def test_summary_is_json_friendly(self):
        json.dumps(plan_uninstall(_manifest((ChangeKind.FILE, "a"))).summary())


class RollbackMarkTest(unittest.TestCase):
    def test_mark_persists(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "j.json"
            manifest = _manifest((ChangeKind.FILE, "a"))
            manifest.save(path)
            mark_rolled_back(manifest, path)
            self.assertTrue(Manifest.load(path).rolled_back)

    def test_second_plan_is_noop(self):
        manifest = mark_rolled_back(_manifest((ChangeKind.FILE, "a")))
        self.assertTrue(plan_uninstall(manifest).already_rolled_back)


class VerifyInverseTest(unittest.TestCase):
    def test_clean_journal(self):
        self.assertEqual(verify_inverse(_manifest((ChangeKind.FILE, "a"))), [])

    def test_wrong_product(self):
        manifest = Manifest()
        manifest.product = "other"
        self.assertTrue(verify_inverse(manifest))

    def test_wrong_schema(self):
        manifest = Manifest()
        manifest.schema = SCHEMA + 1
        self.assertTrue(verify_inverse(manifest))

    def test_product_constant(self):
        self.assertEqual(PRODUCT, "green-dam-2026")


class NoLeakTest(unittest.TestCase):
    def test_render_shows_targets_but_summary_does_not(self):
        # A dry-run must name what it will remove - that is the point of it.
        # The machine-readable summary stays counts-only.
        manifest = _manifest((ChangeKind.FILE, "C:\\Program Files\\GD\\x.exe"))
        plan = plan_uninstall(manifest)
        self.assertIn("x.exe", plan.render())
        self.assertNotIn("x.exe", json.dumps(plan.summary()))


if __name__ == "__main__":
    unittest.main()
