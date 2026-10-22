"""Tests for semantic chunking as a text-evidence source.

The load-bearing point: this module contributes *structure*, never a verdict.
Its findings carry level 0 and action None, and it refuses to feed
``hnc.classify`` because the integer encodings that function needs were never
shipped.

Fixtures are ordinary Chinese sentences with no relation to any word list.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam.chunker import ChunkRole, SentenceChunker  # noqa: E402
from green_dam.chunkpath import (  # noqa: E402
    NO_PREDICATE,
    SINGLE_BLOCK,
    STRUCTURED,
    ChunkSource,
    ChunkSummary,
    classify_shape,
    is_structured,
    summarise,
)


class ShapeTest(unittest.TestCase):
    def _chunk(self, sentence):
        return SentenceChunker().chunk(sentence)

    def test_full_clause_is_structured(self):
        got = self._chunk("张三打断了李四的腿")
        self.assertEqual(classify_shape(got), STRUCTURED)
        self.assertEqual(got.pattern, "A+E+B")

    def test_no_predicate(self):
        # A fragment with no verb: the chunker finds no E block.
        self.assertEqual(classify_shape(self._chunk("甲乙")), NO_PREDICATE)

    def test_empty_text(self):
        self.assertEqual(classify_shape(self._chunk("")), NO_PREDICATE)

    def test_single_block(self):
        got = self._chunk("取消")
        self.assertEqual(classify_shape(got), SINGLE_BLOCK)

    def test_conditional_clause_is_structured(self):
        got = self._chunk("如果下雨，比赛就取消")
        self.assertEqual(classify_shape(got), STRUCTURED)


class SummaryTest(unittest.TestCase):
    def test_counts_and_pattern(self):
        chunking = SentenceChunker().chunk("张三打断了李四的腿")
        summary = summarise(chunking)
        self.assertEqual(summary.block_count, 3)
        self.assertEqual(summary.main_count, 3)
        self.assertEqual(summary.secondary_count, 0)
        self.assertEqual(summary.pattern, "A+E+B")
        self.assertEqual(summary.length, len("张三打断了李四的腿"))

    def test_secondary_blocks_counted_separately(self):
        chunking = SentenceChunker().chunk("如果下雨，比赛就取消")
        summary = summarise(chunking)
        self.assertEqual(summary.secondary_count, 1)
        self.assertEqual(summary.main_count, 2)

    def test_min_confidence(self):
        chunking = SentenceChunker().chunk("如果下雨，比赛就取消")
        summary = summarise(chunking)
        self.assertAlmostEqual(summary.min_confidence,
                               min(c.confidence for c in chunking.chunks))

    def test_to_dict_is_json_friendly(self):
        summary = summarise(SentenceChunker().chunk("他认真地看书"))
        json.dumps(summary.to_dict())

    def test_summary_contains_no_text(self):
        # This is what makes it safe to log.
        sentence = "张三打断了李四的腿"
        blob = json.dumps(summarise(SentenceChunker().chunk(sentence)).to_dict(),
                          ensure_ascii=False)
        for fragment in ("张三", "李四", "打断"):
            self.assertNotIn(fragment, blob)

    def test_empty_chunking(self):
        summary = summarise(SentenceChunker().chunk(""))
        self.assertEqual(summary.block_count, 0)
        self.assertEqual(summary.shape, NO_PREDICATE)
        self.assertEqual(summary.min_confidence, 0.0)


class SourceTest(unittest.TestCase):
    def test_findings_off_by_default(self):
        source = ChunkSource()
        self.assertEqual(source.findings("甲乙"), [])

    def test_findings_emitted_for_unstructured_text(self):
        source = ChunkSource(emit_findings=True)
        got = source.findings("甲乙")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].reason, NO_PREDICATE)

    def test_no_finding_for_structured_text(self):
        source = ChunkSource(emit_findings=True)
        self.assertEqual(source.findings("张三打断了李四的腿"), [])

    def test_findings_carry_no_action(self):
        # Structure is a feature, not evidence of harm.
        source = ChunkSource(emit_findings=True)
        for finding in source.findings("甲乙"):
            self.assertIsNone(finding.action)
            self.assertEqual(finding.levels, (0,))

    def test_findings_are_attested(self):
        # The chunker's output is measured, not guessed - unlike a level from
        # an unshipped table.
        source = ChunkSource(emit_findings=True)
        self.assertTrue(source.findings("甲乙")[0].attested)

    def test_summarise_convenience(self):
        source = ChunkSource()
        got = source.summarise("张三打断了李四的腿")
        self.assertIsInstance(got, ChunkSummary)
        self.assertEqual(got.shape, STRUCTURED)

    def test_is_structured_helper(self):
        self.assertTrue(is_structured("张三打断了李四的腿"))
        self.assertFalse(is_structured("甲乙"))


class NoFrameInventedTest(unittest.TestCase):
    """The reason this module exists instead of feeding hnc.classify."""

    def test_to_frame_hint_still_returns_none(self):
        chunking = SentenceChunker().chunk("张三打断了李四的腿")
        self.assertIsNone(chunking.to_frame_hint())

    def test_chunkpath_does_not_import_hnc_classify(self):
        source = (ROOT / "src" / "green_dam" / "chunkpath.py").read_text(
            encoding="utf-8")
        self.assertNotIn("classify(", source,
                         msg="chunkpath must not call hnc.classify: the "
                             "role->integer mapping was never shipped")
        self.assertNotIn("Frame(", source)

    def test_no_integer_role_codes_anywhere(self):
        """role_a / role_b / participants are the values we must not invent.

        Checked against the AST rather than the raw text, because the module's
        own comments explain why those fields are absent and would otherwise
        trip the assertion.
        """
        import ast
        tree = ast.parse((ROOT / "src" / "green_dam" / "chunkpath.py")
                         .read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.keyword) and node.arg:
                names.add(node.arg)
        for token in ("role_a", "role_b", "participants", "spare", "role2"):
            self.assertNotIn(token, names)


class FilterIntegrationTest(unittest.TestCase):
    def _filter(self, emit=True):
        from green_dam.filter import Filter
        return Filter(chunker=ChunkSource(emit_findings=emit))

    def test_chunk_structure_available(self):
        got = self._filter().chunk_structure("张三打断了李四的腿")
        self.assertEqual(got["shape"], STRUCTURED)
        self.assertEqual(got["pattern"], "A+E+B")

    def test_chunk_structure_none_without_chunker(self):
        from green_dam.filter import Filter
        self.assertIsNone(Filter().chunk_structure("甲乙"))

    def test_findings_reach_the_text_path(self):
        got = self._filter().check_text("甲乙")
        self.assertTrue(any(f.kind == "structure" for f in got.findings))

    def test_findings_absent_when_not_emitting(self):
        got = self._filter(emit=False).check_text("甲乙")
        self.assertEqual(got.findings, ())

    def test_status_reports_chunker(self):
        self.assertTrue(self._filter().status()["chunker_loaded"])

    def test_structured_text_yields_nothing(self):
        got = self._filter().check_text("张三打断了李四的腿")
        self.assertEqual(got.findings, ())
        self.assertEqual(got.reason, "no_evidence")


if __name__ == "__main__":
    unittest.main()
