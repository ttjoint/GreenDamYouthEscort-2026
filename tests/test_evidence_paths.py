"""Cross-path consistency tests.

The refactor this file guards against had a real bug: ``check()`` and
``check_text()`` disagreed about what the same text contained, because only one
of them consulted the HNC matcher. These tests compare the combined entry point
against the single-path entry points, so a future edit that lets them drift
fails immediately.

Fixtures are invented (甲乙丙丁, example hosts, proc-a).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam import sysdat  # noqa: E402
from green_dam.eventlog import Event, EventLog  # noqa: E402
from green_dam.filter import Filter  # noqa: E402
from green_dam.imaging import Check, ScreenResult  # noqa: E402
from green_dam.intercept import Request  # noqa: E402
from green_dam.namelists import NameLists  # noqa: E402
from green_dam.wordlists import WordlistIndex  # noqa: E402


def _policy(**slots) -> sysdat.PolicyArray:
    values = [0] * sysdat.SLOT_COUNT
    for key, value in slots.items():
        values[sysdat.slot_index(int(key.lstrip("s")))] = value
    return sysdat.PolicyArray(values=tuple(values))


def _words(*entries) -> WordlistIndex:
    index = WordlistIndex()
    for word, level in entries:
        index.add(word, "test", level=level)
    return index


def _suspect_image() -> ScreenResult:
    return ScreenResult(suspect=True, fired=(Check.HIGH_TONE,),
                        checks_run=(Check.ASPECT, Check.HIGH_TONE),
                        detail="high_tone")


class PathEnumerationTest(unittest.TestCase):
    """All four paths exist as single-path methods."""

    def test_four_methods_present(self):
        for name in ("check_request", "check_text", "check_image",
                     "check_process"):
            self.assertTrue(callable(getattr(Filter, name)), name)

    def test_process_path_uses_list_b(self):
        lists = NameLists()
        lists["B"].add("proc-a")
        f = Filter(lists=lists)
        self.assertTrue(f.check_process("proc-a").blocked)
        self.assertFalse(f.check_process("proc-b").blocked)

    def test_process_match_is_case_insensitive(self):
        lists = NameLists()
        lists["B"].add("proc-a")
        self.assertTrue(Filter(lists=lists).check_process("PROC-A").blocked)


class ConsistencyTest(unittest.TestCase):
    """check() must agree with the single-path methods."""

    def test_text_verdict_identical_to_check_text(self):
        f = Filter(words=_words(("甲词", 2)))
        self.assertEqual(
            f.check(text="甲词").text.findings,
            f.check_text("甲词").findings)

    def test_hnc_evidence_identical(self):
        from green_dam import semantic
        matcher = semantic.load_default()
        term = next(t for t, v in matcher._levels.items() if max(v) >= 1)
        f = Filter(hnc=matcher)
        # The bug this guards: check() used to skip the HNC matcher entirely.
        self.assertEqual(
            len(f.check(text=term).text.findings),
            len(f.check_text(term).findings))
        self.assertTrue(f.check(text=term).text.findings)

    def test_network_block_identical(self):
        lists = NameLists()
        lists["A"].add("bad.example")
        f = Filter(lists=lists)
        req = Request("outbound", host="bad.example", port=80)
        self.assertEqual(f.check(request=req).action,
                         f.check_request(req).action)

    def test_clean_text_reason_identical(self):
        f = Filter(words=_words(("甲词", 2)))
        self.assertEqual(f.check(text="nothing").text.reason,
                         f.check_text("nothing").reason)

    def test_no_wordlists_reason_identical(self):
        f = Filter()
        self.assertEqual(f.check(text="x").text.reason, "no_wordlists")
        self.assertEqual(f.check_text("x").reason, "no_wordlists")


class GateConsistencyTest(unittest.TestCase):
    """A gate must silence every path, in both entry points."""

    def test_slot2_silences_all_four_paths(self):
        lists = NameLists()
        lists["A"].add("bad.example")
        lists["B"].add("proc-a")
        f = Filter(legacy=_policy(s2=1), lists=lists,
                   words=_words(("甲词", 2)))
        req = Request("outbound", host="bad.example", port=80)
        self.assertIsNone(f.check_request(req).action)
        self.assertIsNone(f.check_text("甲词").action)
        self.assertFalse(f.check_process("proc-a").blocked)
        self.assertEqual(f.check_image(width=64, height=64).detail,
                         "legacy_filtering_disabled")

    def test_slot10_silences_text_only(self):
        lists = NameLists()
        lists["A"].add("bad.example")
        f = Filter(legacy=_policy(s10=1), lists=lists,
                   words=_words(("甲词", 2)))
        self.assertIsNone(f.check_text("甲词").action)
        # Network still works.
        self.assertTrue(
            f.check_request(Request("outbound", host="bad.example",
                                    port=80)).blocked)

    def test_image_gate_applies_inside_check(self):
        f = Filter(words=_words(("甲词", 2)))
        got = f.check(image=_suspect_image(), image_action="block")
        self.assertIsNone(got.action)

    def test_gate_is_logged_once_per_call(self):
        f = Filter(legacy=_policy(s2=1), log=EventLog())
        f.check(request=Request("outbound"), text="x")
        gated = [r for r in f.log.records if r.event is Event.GATED]
        self.assertEqual(len(gated), 1)


class PrecedenceTest(unittest.TestCase):
    def test_network_beats_process(self):
        lists = NameLists()
        lists["A"].add("bad.example")
        lists["B"].add("proc-a")
        f = Filter(lists=lists)
        got = f.check(request=Request("outbound", host="bad.example", port=80),
                      process="proc-a")
        self.assertEqual(got.action, "block")
        self.assertEqual(got.source, "network")
        self.assertEqual(got.network.reason, "host_listed")

    def test_process_consulted_when_request_clean(self):
        lists = NameLists()
        lists["B"].add("proc-a")
        f = Filter(lists=lists)
        got = f.check(request=Request("outbound", host="ok.example", port=80),
                      process="proc-a")
        self.assertTrue(got.blocked)
        self.assertEqual(got.network.reason, "watched_process")

    def test_process_alone_blocks(self):
        lists = NameLists()
        lists["B"].add("proc-a")
        got = Filter(lists=lists).check(process="proc-a")
        self.assertTrue(got.blocked)

    def test_network_block_beats_image(self):
        lists = NameLists()
        lists["A"].add("bad.example")
        f = Filter(lists=lists, legacy=_policy(s13=1, s5=2))
        got = f.check(request=Request("outbound", host="bad.example", port=80),
                      image=_suspect_image(), image_action="block")
        self.assertEqual(got.source, "network")


class SingleRecordTest(unittest.TestCase):
    def test_one_record_per_check_regardless_of_paths(self):
        lists = NameLists()
        lists["A"].add("bad.example")
        lists["B"].add("proc-a")
        cases = (
            {"request": Request("outbound", host="bad.example", port=80)},
            {"text": "x"},
            {"process": "proc-a"},
            {"request": Request("outbound", host="bad.example", port=80),
             "text": "x", "process": "proc-a"},
        )
        for kwargs in cases:
            log = EventLog()
            f = Filter(lists=lists, words=_words(("甲词", 2)), log=log)
            f.check(**kwargs)
            self.assertEqual(len(log), 1, msg=str(kwargs))

    def test_all_four_paths_together_one_record(self):
        lists = NameLists()
        lists["A"].add("bad.example")
        lists["B"].add("proc-a")
        log = EventLog()
        f = Filter(lists=lists, words=_words(("甲词", 2)), log=log,
                   legacy=_policy(s13=1, s5=2))
        f.check(request=Request("outbound", host="bad.example", port=80),
                text="甲词", process="proc-a",
                image=_suspect_image(), image_action="block")
        self.assertEqual(len(log), 1)


class NoLeakTest(unittest.TestCase):
    def test_all_paths_keep_content_out_of_the_log(self):
        lists = NameLists()
        lists["A"].add("secret.example")
        lists["B"].add("proc-secret")
        log = EventLog()
        f = Filter(lists=lists, words=_words(("甲词", 2)), log=log,
                   legacy=_policy(s13=1, s5=2))
        f.check(request=Request("outbound", host="secret.example", port=80),
                text="甲词 private-marker", process="proc-secret",
                image=_suspect_image(), image_action="block")
        blob = "".join(r.to_json() for r in log.records)
        for leaked in ("secret.example", "proc-secret", "甲词", "private-marker"):
            self.assertNotIn(leaked, blob)


if __name__ == "__main__":
    unittest.main()


class BackendSwapTest(unittest.TestCase):
    """E1: the text path accepts either backend without changing callers."""

    def _matcher(self):
        from green_dam.semantic import LegacySemanticMatcher
        return LegacySemanticMatcher({"甲乙": (2,), "丙丁": (0,)})

    def test_hnc_backend_yields_findings(self):
        from green_dam.backends import HncBackend
        f = Filter(backend=HncBackend(matcher=self._matcher()))
        got = f.check_text("甲乙")
        self.assertTrue(got.findings)
        self.assertEqual(got.findings[0].sources, ("hnc",))

    def test_segmentation_backend_yields_no_severity(self):
        # The reference backend has no severity information, so its level-0 hits
        # are dropped - it can only agree about spans, not about harm.
        from green_dam.backends import SegmentationBackend
        f = Filter(backend=SegmentationBackend.from_terms(["甲乙"]))
        got = f.check_text("甲乙")
        self.assertEqual(got.findings, ())

    def test_bare_matcher_still_works(self):
        # The pre-existing call style must not break.
        f = Filter(hnc=self._matcher())
        self.assertTrue(f.check_text("甲乙").findings)

    def test_backend_takes_precedence_over_bare_matcher(self):
        from green_dam.backends import HncBackend
        f = Filter(hnc=self._matcher(),
                   backend=HncBackend(matcher=self._matcher()))
        self.assertTrue(f.check_text("甲乙").findings)

    def test_status_reports_backend(self):
        from green_dam.backends import HncBackend
        self.assertTrue(
            Filter(backend=HncBackend(matcher=self._matcher()))
            .status()["hnc_loaded"])

    def test_no_backend_still_reports_no_wordlists(self):
        self.assertEqual(Filter().check_text("x").reason, "no_wordlists")
