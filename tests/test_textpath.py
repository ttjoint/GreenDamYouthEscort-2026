"""Tests for the text evidence pipeline.

The pipeline's job is to turn sources into findings without inventing actions.
Two properties matter most:

* a level-0 hit is never evidence (the false-positive trap)
* "no source configured" and "configured, found nothing" stay distinguishable

Fixtures are invented (甲乙丙丁).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam.backends import HncBackend, SegmentationBackend  # noqa: E402
from green_dam.policy import PolicyEngine, PolicyTable, KIND_SCORED  # noqa: E402
from green_dam.semantic import LegacySemanticMatcher  # noqa: E402
from green_dam.textpath import (  # noqa: E402
    SAFE_LEVEL,
    TextSources,
    evaluate,
    findings_from_backend,
    gather,
    verdict_from,
)
from green_dam.wordlists import WordlistIndex  # noqa: E402


def _matcher() -> LegacySemanticMatcher:
    return LegacySemanticMatcher({"甲乙": (2,), "丙丁": (0,), "戊己": (3, 7)})


def _engine(*entries) -> PolicyEngine:
    index = WordlistIndex()
    for word, level in entries:
        index.add(word, "test", level=level)
    return PolicyEngine(words=index, policies=PolicyTable(level_actions={},
                                                         attested=False))


class SourcePresenceTest(unittest.TestCase):
    def test_empty_sources_not_configured(self):
        self.assertFalse(TextSources().configured)

    def test_any_source_counts(self):
        self.assertTrue(TextSources(wordlists=object()).configured)
        self.assertTrue(TextSources(backend=object()).configured)
        self.assertTrue(TextSources(matcher=object()).configured)

    def test_backend_preferred_over_matcher(self):
        backend, matcher = object(), object()
        self.assertIs(TextSources(backend=backend, matcher=matcher)
                      .active_backend(), backend)

    def test_matcher_used_when_no_backend(self):
        matcher = object()
        self.assertIs(TextSources(matcher=matcher).active_backend(), matcher)


class BackendFindingsTest(unittest.TestCase):
    def test_level_zero_dropped(self):
        hits = findings_from_backend(_matcher(), "丙丁")
        self.assertEqual(hits, [])

    def test_level_above_zero_kept(self):
        hits = findings_from_backend(_matcher(), "甲乙")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].kind, KIND_SCORED)

    def test_backend_protocol_accepted(self):
        backend = HncBackend(matcher=_matcher())
        hits = findings_from_backend(backend, "甲乙")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].sources, ("hnc",))

    def test_segmentation_backend_yields_nothing(self):
        # It reports level 0, which is not evidence.
        backend = SegmentationBackend.from_terms(["甲乙"])
        self.assertEqual(findings_from_backend(backend, "甲乙"), [])

    def test_no_action_is_ever_set(self):
        for source in (_matcher(), HncBackend(matcher=_matcher())):
            for hit in findings_from_backend(source, "戊己"):
                self.assertIsNone(hit.action)

    def test_span_points_at_term(self):
        text = "x甲乙y"
        hits = findings_from_backend(_matcher(), text)
        start, end = hits[0].span
        self.assertEqual(text[start:end], "甲乙")

    def test_attested_is_false(self):
        # Neither backend is the original engine.
        hits = findings_from_backend(HncBackend(matcher=_matcher()), "甲乙")
        self.assertFalse(hits[0].attested)

    def test_safe_level_constant(self):
        self.assertEqual(SAFE_LEVEL, 0)


class GatherTest(unittest.TestCase):
    def test_wordlists_only(self):
        sources = TextSources(wordlists=object())
        hits = gather(sources, "甲词", _engine(("甲词", 2)))
        self.assertTrue(hits)

    def test_backend_only(self):
        sources = TextSources(backend=HncBackend(matcher=_matcher()))
        hits = gather(sources, "甲乙", None)
        self.assertTrue(hits)

    def test_both_contribute(self):
        sources = TextSources(wordlists=object(),
                              backend=HncBackend(matcher=_matcher()))
        hits = gather(sources, "甲词甲乙", _engine(("甲词", 2)))
        self.assertGreaterEqual(len(hits), 2)

    def test_nothing_configured(self):
        self.assertEqual(gather(TextSources(), "甲乙", None), [])


class EvaluateTest(unittest.TestCase):
    def test_no_sources_gives_no_wordlists(self):
        got = evaluate(TextSources(), "anything", None)
        self.assertEqual(got.reason, "no_wordlists")
        self.assertIsNone(got.action)

    def test_configured_but_clean_gives_no_evidence(self):
        sources = TextSources(backend=HncBackend(matcher=_matcher()))
        got = evaluate(sources, "nothing here", None)
        self.assertEqual(got.reason, "no_evidence")

    def test_level_zero_only_gives_no_evidence(self):
        # The trap: a level-0 hit must not read as "found something".
        sources = TextSources(backend=HncBackend(matcher=_matcher()))
        got = evaluate(sources, "丙丁", None)
        self.assertEqual(got.findings, ())
        self.assertEqual(got.reason, "no_evidence")

    def test_backend_hit_yields_findings_without_action(self):
        sources = TextSources(backend=HncBackend(matcher=_matcher()))
        got = evaluate(sources, "甲乙", None)
        self.assertTrue(got.findings)
        self.assertIsNone(got.action)

    def test_wordlist_engine_supplies_action_mapping(self):
        engine = _engine(("甲词", 2))
        engine.policies.level_actions[2] = "block"
        sources = TextSources(wordlists=object())
        got = evaluate(sources, "甲词", engine)
        self.assertEqual(got.action, "block")

    def test_engine_verdict_used_when_present(self):
        engine = _engine(("甲词", 2))
        sources = TextSources(wordlists=object())
        got = evaluate(sources, "甲词", engine)
        self.assertTrue(got.findings)


class VerdictFromTest(unittest.TestCase):
    def test_no_findings(self):
        got = verdict_from([])
        self.assertIsNone(got.action)
        self.assertEqual(got.reason, "no_action")

    def test_findings_without_action(self):
        sources = TextSources(backend=HncBackend(matcher=_matcher()))
        hits = gather(sources, "甲乙", None)
        got = verdict_from(hits)
        self.assertIsNone(got.action)
        self.assertEqual(len(got.findings), len(hits))

    def test_picks_strongest_action(self):
        from green_dam.policy import Finding
        weak = Finding(span=(0, 1), surface="x", kind=KIND_SCORED,
                       sources=("t",), levels=(1,), attested=False,
                       action="log")
        strong = Finding(span=(1, 2), surface="x", kind=KIND_SCORED,
                         sources=("t",), levels=(2,), attested=False,
                         action="block")
        self.assertEqual(verdict_from([weak, strong]).action, "block")
        self.assertEqual(verdict_from([strong, weak]).action, "block")


class NoLeakTest(unittest.TestCase):
    def test_findings_do_not_print_terms(self):
        sources = TextSources(backend=HncBackend(matcher=_matcher()))
        hits = gather(sources, "甲乙", None)
        self.assertNotIn("甲乙", repr(hits))


if __name__ == "__main__":
    unittest.main()
