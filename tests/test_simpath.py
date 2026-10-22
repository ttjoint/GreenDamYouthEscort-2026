"""Tests for sentence similarity as a text-evidence source.

The load-bearing property: a pair the chunker cannot structure is **refused**,
not scored. The verb table has 413 entries and cannot cover every predicate, so
a plausible-looking score from bad chunking would be worse than no score.

Fixtures are ordinary sentences unrelated to any word list.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam.simpath import (  # noqa: E402
    DEFAULT_THRESHOLD,
    DEGRADED_CHUNKING,
    NO_STRUCTURE,
    TOO_SHORT,
    Comparison,
    SimilaritySource,
    is_scorable,
    summarise,
)

#: Sentences whose predicates are in the verb table, so chunking succeeds.
STRUCTURED_A = "张三打断了李四的腿"
STRUCTURED_B = "他认真地看书"
#: Same sentence as A, reordered - a genuine paraphrase.
PARAPHRASE = "李四的腿被张三打断"
#: Predicate "擦" is not in the table: chunking produces a single block.
UNCHUNKABLE = "王五擦干净了桌子"


class ScorableTest(unittest.TestCase):
    def setUp(self):
        self.source = SimilaritySource()

    def _chunk(self, text):
        return self.source.chunker.chunk(text)

    def test_structured_pair_is_scorable(self):
        ok, reason = is_scorable(self._chunk(STRUCTURED_A),
                                 self._chunk(STRUCTURED_B))
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_too_short(self):
        ok, reason = is_scorable(self._chunk("甲"), self._chunk(STRUCTURED_A))
        self.assertFalse(ok)
        self.assertEqual(reason, TOO_SHORT)

    def test_empty(self):
        ok, reason = is_scorable(self._chunk(""), self._chunk(STRUCTURED_A))
        self.assertFalse(ok)
        self.assertEqual(reason, TOO_SHORT)

    def test_no_structure(self):
        # A text the chunker cannot split at all.
        ok, reason = is_scorable(self._chunk("甲乙"), self._chunk(STRUCTURED_A))
        self.assertFalse(ok)
        self.assertIn(reason, (NO_STRUCTURE, DEGRADED_CHUNKING))

    def test_missing_predicate_is_refused(self):
        # The case that matters: a sentence the verb table does not cover.
        ok, reason = is_scorable(self._chunk(UNCHUNKABLE),
                                 self._chunk(STRUCTURED_A))
        self.assertFalse(ok)
        self.assertEqual(reason, DEGRADED_CHUNKING)


class CompareTest(unittest.TestCase):
    def setUp(self):
        self.source = SimilaritySource()

    def test_identical_scores_high(self):
        got = self.source.compare(STRUCTURED_A, STRUCTURED_A)
        self.assertTrue(got.scored)
        # 0.53 after the empty-secondary fix (was 0.70 when an absent dimension
        # contributed a free 0.3).
        self.assertAlmostEqual(got.total, 0.53, places=2)
        self.assertTrue(got.similar)

    def test_paraphrase_scores_higher_than_unrelated(self):
        related = self.source.compare(STRUCTURED_A, PARAPHRASE)
        unrelated = self.source.compare(STRUCTURED_A, STRUCTURED_B)
        self.assertTrue(related.scored and unrelated.scored)
        self.assertGreater(related.total, unrelated.total)

    def test_passive_rewrite_falls_just_below_threshold(self):
        """A known limitation, pinned rather than papered over.

        ``char_overlap_similarity`` compares character bigrams, so a passive
        rewrite of the same event scores 0.176 - below the 0.2 threshold. The
        threshold was set from what the measurement can do, not lowered until
        this hand-picked pair passed. Raising it would need real lexical
        similarity, which similarity.py marks as a substitute term.
        """
        got = self.source.compare(STRUCTURED_A, PARAPHRASE)
        self.assertLess(got.total, self.source.threshold)
        self.assertFalse(got.similar)
        self.assertGreater(got.total, 0.15,
                           msg="it must still be visibly closer than unrelated")

    def test_unrelated_is_below_threshold(self):
        got = self.source.compare(STRUCTURED_A, STRUCTURED_B)
        self.assertFalse(got.similar)

    def test_reworded_sentence_beats_unrelated_by_a_wide_margin(self):
        # A light rewording, which bigram overlap does capture.
        reworded = self.source.compare("他认真地看书", "他仔细地看书")
        unrelated = self.source.compare(STRUCTURED_A, STRUCTURED_B)
        self.assertTrue(reworded.scored and unrelated.scored)
        self.assertGreater(reworded.total - unrelated.total, 0.2,
                           msg="similarity must separate meaning from shape")

    def test_unscorable_pair_is_not_scored(self):
        got = self.source.compare(STRUCTURED_A, UNCHUNKABLE)
        self.assertFalse(got.scored)
        self.assertEqual(got.reason, DEGRADED_CHUNKING)
        self.assertFalse(got.similar)
        self.assertEqual(got.total, 0.0)

    def test_threshold_is_configurable(self):
        low = SimilaritySource(threshold=0.1)
        self.assertTrue(low.compare(STRUCTURED_A, STRUCTURED_B).similar)

    def test_default_threshold_sits_between(self):
        # Measured: paraphrase 0.370, same-structure-unrelated 0.105.
        self.assertGreater(DEFAULT_THRESHOLD, 0.105)
        self.assertLess(DEFAULT_THRESHOLD, 0.370)

    def test_threshold_admits_a_reworded_sentence(self):
        source = SimilaritySource()
        self.assertTrue(source.compare("他认真地看书", "他仔细地看书").similar)

    def test_threshold_rejects_same_structure(self):
        source = SimilaritySource()
        self.assertFalse(source.compare(STRUCTURED_A, STRUCTURED_B).similar)

    def test_degraded_terms_are_reported(self):
        got = self.source.compare(STRUCTURED_A, STRUCTURED_A)
        self.assertTrue(got.degraded,
                        msg="similarity.py substitutes two terms; that must "
                            "surface here")

    def test_metrics_are_numbers(self):
        got = self.source.compare(STRUCTURED_A, STRUCTURED_A)
        self.assertEqual(set(got.metrics),
                         {"cat", "gse", "sem", "gsem", "gsef", "semm", "semf"})
        for value in got.metrics.values():
            self.assertIsInstance(value, float)


class BestMatchTest(unittest.TestCase):
    def setUp(self):
        self.source = SimilaritySource()

    def test_picks_the_closest(self):
        got = self.source.best_match(STRUCTURED_A,
                                     [STRUCTURED_B, PARAPHRASE])
        self.assertIsNotNone(got)
        candidate, result = got
        self.assertEqual(candidate, PARAPHRASE)
        self.assertGreater(result.total, 0)

    def test_skips_unscorable_candidates(self):
        got = self.source.best_match(STRUCTURED_A,
                                     [UNCHUNKABLE, STRUCTURED_B])
        self.assertIsNotNone(got)
        self.assertEqual(got[0], STRUCTURED_B)

    def test_all_unscorable_returns_none(self):
        self.assertIsNone(self.source.best_match(STRUCTURED_A, [UNCHUNKABLE]))

    def test_empty_candidates(self):
        self.assertIsNone(self.source.best_match(STRUCTURED_A, []))


class FindingsTest(unittest.TestCase):
    def setUp(self):
        self.source = SimilaritySource()

    def test_no_finding_below_threshold(self):
        self.assertEqual(
            self.source.findings(STRUCTURED_A, [STRUCTURED_B]), [])

    def test_finding_above_threshold(self):
        got = self.source.findings("他认真地看书", ["他仔细地看书"])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].kind, "similarity")

    def test_finding_has_no_action(self):
        got = self.source.findings("他认真地看书", ["他仔细地看书"])
        self.assertIsNone(got[0].action)
        self.assertEqual(got[0].levels, (0,))

    def test_finding_is_not_attested(self):
        # Two of the three terms are substitutes; similarity.py says so.
        got = self.source.findings("他认真地看书", ["他仔细地看书"])
        self.assertFalse(got[0].attested)

    def test_unscorable_yields_no_finding(self):
        self.assertEqual(
            self.source.findings(STRUCTURED_A, [UNCHUNKABLE]), [])


class LogSafetyTest(unittest.TestCase):
    def test_summary_has_no_text(self):
        result = SimilaritySource().compare(STRUCTURED_A, PARAPHRASE)
        blob = json.dumps(summarise(result), ensure_ascii=False)
        for fragment in ("张三", "李四", "打断"):
            self.assertNotIn(fragment, blob)

    def test_comparison_dict_is_json_friendly(self):
        result = SimilaritySource().compare(STRUCTURED_A, STRUCTURED_A)
        json.dumps(result.to_dict())

    def test_unscorable_dict_is_serialisable(self):
        result = SimilaritySource().compare(STRUCTURED_A, UNCHUNKABLE)
        json.dumps(result.to_dict())


class HonestLimitsTest(unittest.TestCase):
    """The module must not pretend the chunker is better than it is."""

    def test_verb_table_gap_is_documented_in_behaviour(self):
        # '擦' is absent from the 413-entry table; the module refuses to score.
        got = SimilaritySource().compare(UNCHUNKABLE, UNCHUNKABLE)
        self.assertFalse(got.scored,
                         msg="an identical unchunkable pair must still be "
                             "refused: identical text does not fix a missing "
                             "predicate")

    def test_no_hardcoded_threshold_in_compare(self):
        source = (ROOT / "src" / "green_dam" / "simpath.py").read_text(
            encoding="utf-8")
        self.assertIn("self.threshold", source)


if __name__ == "__main__":
    unittest.main()
