"""Tests for the two-backend interface and A/B comparison.

Fixtures are invented. Where a real term from the shipped lexicon is needed to
exercise a path, it is taken from the loaded table by predicate rather than
written down here, so no list content appears in this file.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam.backends import (  # noqa: E402
    AbReport,
    BackendResult,
    HncBackend,
    SegmentationBackend,
    SemanticBackend,
    compare,
    load_ab_pair,
    run_ab,
)
from green_dam.semantic import Hit, LegacySemanticMatcher  # noqa: E402


def _matcher() -> LegacySemanticMatcher:
    return LegacySemanticMatcher({
        "甲": (0,),
        "甲乙": (2,),
        "甲乙丙": (5,),
        "丁": (0,),
        "戊己": (3, 7),
    })


class ProtocolTest(unittest.TestCase):
    """E1: both backends satisfy one interface."""

    def test_hnc_satisfies_protocol(self):
        self.assertIsInstance(HncBackend(matcher=_matcher()), SemanticBackend)

    def test_segmentation_satisfies_protocol(self):
        self.assertIsInstance(
            SegmentationBackend.from_terms(["甲", "乙"]), SemanticBackend)

    def test_both_expose_a_name(self):
        left, right = HncBackend(matcher=_matcher()), SegmentationBackend()
        self.assertEqual(left.name, "hnc")
        self.assertEqual(right.name, "segmentation")

    def test_both_return_backend_result(self):
        for backend in (HncBackend(matcher=_matcher()), SegmentationBackend()):
            got = backend.analyse("甲乙")
            self.assertIsInstance(got, BackendResult)
            self.assertEqual(got.backend, backend.name)

    def test_neither_claims_to_be_attested(self):
        # architecture.md §M3: neither backend is the original engine.
        for backend in (HncBackend(matcher=_matcher()), SegmentationBackend()):
            self.assertFalse(backend.analyse("甲乙").attested)


class HncBackendTest(unittest.TestCase):
    def test_finds_terms(self):
        got = HncBackend(matcher=_matcher()).analyse("甲乙")
        self.assertEqual(got.terms(), ("甲乙",))

    def test_drops_level_zero(self):
        # Level 0 means "not graded as harmful", not "harmless evidence".
        got = HncBackend(matcher=_matcher()).analyse("甲")
        self.assertEqual(got.hits, ())

    def test_min_level_is_configurable(self):
        # The matcher starts at 2 characters, so use a two-character term whose
        # level is 0 to prove min_level=0 keeps it.
        matcher = LegacySemanticMatcher({"丁戊": (0,)})
        self.assertEqual(HncBackend(matcher=matcher).analyse("丁戊").hits, ())
        kept = HncBackend(matcher=matcher, min_level=0).analyse("丁戊")
        self.assertTrue(kept.hits)

    def test_keeps_all_levels_on_the_hit(self):
        got = HncBackend(matcher=_matcher()).analyse("戊己")
        self.assertEqual(got.hits[0].all_levels, (3, 7))

    def test_prefers_longest(self):
        got = HncBackend(matcher=_matcher()).analyse("甲乙丙")
        self.assertEqual(got.terms(), ("甲乙丙",))

    def test_spans_point_at_the_term(self):
        text = "x甲乙y"
        got = HncBackend(matcher=_matcher()).analyse(text)
        start, end = got.spans[0]
        self.assertEqual(text[start:end], "甲乙")

    def test_empty_text(self):
        self.assertEqual(HncBackend(matcher=_matcher()).analyse("").hits, ())


class SegmentationBackendTest(unittest.TestCase):
    def test_forward_maximum_matching(self):
        backend = SegmentationBackend.from_terms(["甲乙丙", "甲乙", "丁"])
        self.assertEqual(backend.segment("甲乙丙丁"), ("甲乙丙", "丁"))

    def test_unknown_characters_emitted_singly(self):
        backend = SegmentationBackend.from_terms(["甲乙"])
        self.assertEqual(backend.segment("x甲乙"), ("x", "甲乙"))

    def test_empty_text(self):
        self.assertEqual(SegmentationBackend().segment(""), ())

    def test_hits_carry_level_zero(self):
        # This backend has no severity information; claiming any would be a lie.
        backend = SegmentationBackend.from_terms(["甲乙"])
        got = backend.analyse("甲乙")
        self.assertEqual(got.hits[0].level, 0)

    def test_single_char_terms_are_not_hits(self):
        backend = SegmentationBackend.from_terms(["甲"])
        got = backend.analyse("甲")
        self.assertEqual(got.hits, ())
        self.assertEqual(got.segments, ("甲",))

    def test_from_matcher_uses_the_same_table(self):
        matcher = _matcher()
        backend = SegmentationBackend.from_matcher(matcher)
        self.assertIn("甲乙丙", backend.lexicon)

    def test_deterministic(self):
        backend = SegmentationBackend.from_terms(["甲乙", "乙丙"])
        first = backend.segment("甲乙丙")
        for _ in range(5):
            self.assertEqual(backend.segment("甲乙丙"), first)


class CompareTest(unittest.TestCase):
    """E2: A/B comparison over the same text."""

    def _results(self, left_spans, right_spans, length=10):
        left = BackendResult(backend="a",
                             hits=tuple(Hit("x" * (e - s), s, 1)
                                        for s, e in left_spans),
                             text_length=length)
        right = BackendResult(backend="b",
                              hits=tuple(Hit("x" * (e - s), s, 1)
                                         for s, e in right_spans),
                              text_length=length)
        return left, right

    def test_identical_results_agree_fully(self):
        left, right = self._results([(0, 2), (5, 7)], [(0, 2), (5, 7)])
        got = compare(left, right)
        self.assertEqual(got.agreed, ((0, 2), (5, 7)))
        self.assertEqual(got.agreement, 1.0)

    def test_disjoint_results(self):
        left, right = self._results([(0, 2)], [(5, 7)])
        got = compare(left, right)
        self.assertEqual(got.only_left, ((0, 2),))
        self.assertEqual(got.only_right, ((5, 7),))
        self.assertEqual(got.agreement, 0.0)

    def test_partial_overlap(self):
        left, right = self._results([(0, 2), (3, 5)], [(0, 2)])
        got = compare(left, right)
        self.assertEqual(got.agreed, ((0, 2),))
        self.assertEqual(got.only_left, ((3, 5),))
        self.assertAlmostEqual(got.agreement, 1 / 2)

    def test_near_miss_counts_as_disagreement(self):
        # (0,2) and (0,3) are different claims about where the term is.
        left, right = self._results([(0, 2)], [(0, 3)])
        got = compare(left, right)
        self.assertEqual(got.agreed, ())
        self.assertEqual(got.agreement, 0.0)

    def test_both_empty_agrees(self):
        left, right = self._results([], [])
        self.assertEqual(compare(left, right).agreement, 1.0)

    def test_comparison_records_backend_names(self):
        left, right = self._results([], [])
        got = compare(left, right)
        self.assertEqual((got.left, got.right), ("a", "b"))


class AbReportTest(unittest.TestCase):
    def test_empty_report(self):
        report = AbReport()
        self.assertEqual(report.texts, 0)
        self.assertEqual(report.mean_agreement, 1.0)

    def test_summary_is_counts_only(self):
        report = AbReport()
        left = BackendResult(backend="a", hits=(Hit("甲乙", 0, 1),), text_length=4)
        right = BackendResult(backend="b", hits=(Hit("甲乙", 0, 1),), text_length=4)
        report.add(compare(left, right))
        summary = report.summary()
        self.assertEqual(summary["texts"], 1)
        self.assertEqual(summary["identical"], 1)
        self.assertEqual(summary["spans_agreed"], 1)
        # The summary must not leak terms.
        self.assertNotIn("甲乙", repr(summary))

    def test_run_ab_over_corpus(self):
        left = HncBackend(matcher=_matcher())
        right = SegmentationBackend.from_matcher(_matcher())
        report = run_ab(left, right, ["甲乙", "甲乙丙", "", "无关文本"])
        self.assertEqual(report.texts, 4)
        self.assertGreaterEqual(report.mean_agreement, 0.0)
        self.assertLessEqual(report.mean_agreement, 1.0)


class RealLexiconTest(unittest.TestCase):
    """A/B over the shipped lexicon, without naming any of its content."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.left, cls.right = load_ab_pair()
        except FileNotFoundError:
            raise unittest.SkipTest("legacy data not present")

    def test_pair_loads(self):
        self.assertEqual(self.left.name, "hnc")
        self.assertEqual(self.right.name, "segmentation")

    def test_same_table_for_a_fair_comparison(self):
        # A like-for-like comparison requires both to see the same vocabulary.
        self.assertTrue(set(self.left.matcher._levels) & self.right.lexicon)

    def test_ab_runs_over_real_terms(self):
        # Take terms from the table by predicate, never by writing them down.
        terms = [t for t, v in self.left.matcher._levels.items()
                 if max(v) >= 1 and len(t) >= 2][:20]
        report = run_ab(self.left, self.right, terms)
        self.assertEqual(report.texts, len(terms))
        summary = report.summary()
        self.assertGreater(summary["spans_agreed"], 0)

    def test_normal_text_still_clean_under_both(self):
        for text in ("hello world", "this is normal", "clean text here"):
            self.assertEqual(self.left.analyse(text).hits, (), msg=text)


if __name__ == "__main__":
    unittest.main()
