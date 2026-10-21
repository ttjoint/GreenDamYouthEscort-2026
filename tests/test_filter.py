"""End-to-end tests: configuration -> lists -> decision -> policy -> log.

These are the only tests that exercise the whole stack at once. Fixtures are
invented (甲乙丙丁, example hosts, proc-a); no legacy list content is used.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam import config, sysdat  # noqa: E402
from green_dam.eventlog import Event, EventLog  # noqa: E402
from green_dam.filter import Filter  # noqa: E402
from green_dam.intercept import Request  # noqa: E402
from green_dam.wordlists import WordlistIndex  # noqa: E402

SYSDIR = ROOT.parent / "LH-installed-tree" / "Windows" / "System32"


def _filter(*, log=None, words=None, legacy=None, entries=None):
    f = Filter(log=log, words=words, legacy=legacy)
    for key, texts in (entries or {}).items():
        for text in texts:
            f.lists[key].add(text)
    return f


class ConstructionTest(unittest.TestCase):
    def test_fresh_install_uses_shipped_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            f = Filter.from_config_dir(Path(td))
            self.assertEqual(f.legacy.values,
                             config.default_for_fresh_install().values)

    def test_reads_existing_xconfigs(self):
        if not (SYSDIR / "xconfigs.dat").exists():
            self.skipTest("legacy tree not present")
        f = Filter.from_config_dir(SYSDIR)
        self.assertEqual(f.legacy.values, sysdat.load(SYSDIR / "sys.dat").values)

    def test_status_has_no_entries(self):
        f = _filter(entries={"A": ["secret.example"]})
        blob = repr(f.status())
        self.assertNotIn("secret.example", blob)
        self.assertIn("capacity", blob)


class NetworkPathTest(unittest.TestCase):
    def test_block_records_one_event(self):
        log = EventLog()
        f = _filter(log=log, entries={"A": ["bad.example"]})
        got = f.check_request(Request("outbound", host="bad.example", port=80))
        self.assertTrue(got.blocked)
        self.assertEqual(got.source, "network")
        self.assertEqual(len(log), 1)
        self.assertEqual(log.records[0].event, Event.BLOCK)
        self.assertEqual(log.records[0].stage, "host")

    def test_clean_request_records_allow(self):
        log = EventLog()
        f = _filter(log=log)
        got = f.check_request(Request("outbound", host="ok.example", port=80))
        self.assertIsNone(got.action)
        self.assertEqual(log.records[0].event, Event.ALLOW)

    def test_block_carries_network_stage(self):
        f = _filter(entries={"B": ["proc-a"]})
        got = f.check_request(Request("outbound", process="proc-a"))
        self.assertEqual(got.action, "block")
        self.assertEqual(got.network.stage.value, "process")


class GateTest(unittest.TestCase):
    def _gated_filter(self, **slots):
        values = [0] * sysdat.SLOT_COUNT
        for slot_no, value in slots.items():
            values[sysdat.slot_index(int(slot_no.lstrip("s")))] = value
        legacy = sysdat.PolicyArray(values=tuple(values))
        return _filter(log=EventLog(), legacy=legacy,
                       entries={"A": ["bad.example"], "B": ["proc-a"]})

    def test_slot2_gates_network(self):
        f = self._gated_filter(s2=1)
        got = f.check_request(Request("outbound", host="bad.example", port=80))
        self.assertIsNone(got.action)
        self.assertEqual(got.reason, "legacy_filtering_disabled")
        self.assertEqual(f.log.records[0].event, Event.GATED)

    def test_slot2_gates_text(self):
        f = self._gated_filter(s2=1)
        got = f.check_text("anything")
        self.assertIsNone(got.action)
        self.assertEqual(got.reason, "legacy_filtering_disabled")

    def test_slot10_gates_keyword_only(self):
        f = self._gated_filter(s10=1)
        # Keyword path gated...
        text = f.check_text("anything")
        self.assertEqual(text.reason, "legacy_keyword_filter_disabled")
        # ...but the network host list still works.
        net = f.check_request(Request("outbound", host="bad.example", port=80))
        self.assertTrue(net.blocked)

    def test_gate_never_reports_clean(self):
        f = self._gated_filter(s2=1)
        got = f.check_request(Request("outbound", host="bad.example", port=80))
        self.assertNotEqual(got.reason, "no_stage_matched")
        self.assertNotEqual(got.action, "allow")


class TextPathTest(unittest.TestCase):
    def _words(self):
        index = WordlistIndex()
        index.add("甲词", "test-source", level=2)
        return index

    def test_no_wordlists_is_not_an_error(self):
        log = EventLog()
        f = _filter(log=log)
        got = f.check_text("甲词")
        self.assertIsNone(got.action)
        self.assertEqual(got.reason, "no_wordlists")
        self.assertEqual(len(log), 1)

    def test_wordlist_hit_with_unmapped_level_has_no_action(self):
        # Nothing is mapped by default: the vendor's score->action mapping is
        # unknown, so a hit yields evidence but no action.
        f = _filter(words=self._words())
        got = f.check_text("甲词")
        self.assertEqual(len(got.findings), 1)
        self.assertIsNone(got.action)

    def test_mapped_level_produces_action(self):
        f = _filter(words=self._words())
        f.policies.level_actions[2] = "block"
        got = f.check_text("甲词")
        self.assertEqual(got.action, "block")


class CombinedPathTest(unittest.TestCase):
    def test_network_block_wins_over_text(self):
        index = WordlistIndex()
        index.add("甲词", "s", level=2)
        f = _filter(words=index, entries={"A": ["bad.example"]})
        f.policies.level_actions[2] = "warn"
        got = f.check(request=Request("outbound", host="bad.example", port=80),
                      text="甲词")
        self.assertEqual(got.action, "block")
        self.assertEqual(got.source, "network")

    def test_text_action_when_network_clean(self):
        index = WordlistIndex()
        index.add("甲词", "s", level=2)
        f = _filter(words=index)
        f.policies.level_actions[2] = "log"
        got = f.check(request=Request("outbound", host="ok.example", port=80),
                      text="甲词")
        self.assertEqual(got.action, "log")
        self.assertEqual(got.source, "text")

    def test_one_log_record_per_check(self):
        log = EventLog()
        f = _filter(log=log, entries={"A": ["bad.example"]})
        f.check(request=Request("outbound", host="bad.example", port=80),
                text="anything")
        self.assertEqual(len(log), 1)

    def test_check_text_only(self):
        f = _filter(log=EventLog())
        got = f.check(text="anything")
        self.assertIsNone(got.action)
        self.assertEqual(len(f.log), 1)

    def test_check_request_only(self):
        f = _filter(log=EventLog(), entries={"A": ["bad.example"]})
        got = f.check(request=Request("outbound", host="bad.example", port=80))
        self.assertTrue(got.blocked)
        self.assertEqual(len(f.log), 1)


class NoLeakTest(unittest.TestCase):
    def test_end_to_end_log_contains_no_content(self):
        log = EventLog()
        f = _filter(log=log, entries={"A": ["secret.example"],
                                      "C": ["{甲}[乙]"]})
        f.check_request(Request("outbound", host="secret.example", port=80))
        f.check(request=Request("inbound", port=80, body="甲乙 private-marker"),
                text="甲乙 private-marker")
        blob = "".join(r.to_json() for r in log.records)
        for leaked in ("secret.example", "甲", "乙", "private-marker"):
            self.assertNotIn(leaked, blob)


class PersistenceIntegrationTest(unittest.TestCase):
    def test_configure_save_reload_decide(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "xconfigs.dat"
            # Start from defaults, turn keyword filtering off, persist.
            arr = config.default_for_fresh_install()
            arr = config.set_values(arr, {10: 1})
            config.save(arr, path, source="integration")

            f = Filter.from_config_dir(Path(td))
            self.assertTrue(f.keyword_filter_disabled)
            got = f.check_text("anything")
            self.assertEqual(got.reason, "legacy_keyword_filter_disabled")

    def test_saved_blob_still_matches_legacy_size(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "xconfigs.dat"
            config.save(config.default_for_fresh_install(), path)
            self.assertEqual(path.stat().st_size, 268)


if __name__ == "__main__":
    unittest.main()
