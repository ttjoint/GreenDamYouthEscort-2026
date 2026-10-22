"""语义匹配器测试。

覆盖三层：数据装载是否正确、匹配机制是否健全、语料断言是否成立。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import corpus  # noqa: E402
from green_dam import semantic  # noqa: E402


class MatcherLoadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.m = semantic.load_default()

    def test_terms_loaded(self):
        self.assertGreater(len(self.m), 5000)

    def test_pool_slice_is_a_candidate(self):
        # 池切片（sense 的 text 字段）也是可匹配候选。
        self.assertIsNotNone(self.m.levels_of("大法弟子"))

    def test_chain_term_is_a_candidate(self):
        # 链词条（term_senses 的 word）优先，等级来自它引用的 sense。
        levels = self.m.levels_of("大法弟子")
        self.assertIn(6, levels)


class MatchingMechanicsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.m = semantic.load_default()

    def test_clean_text_produces_no_hits(self):
        v = self.m.analyse("今天天气不错。")
        self.assertEqual(v.hits, [])
        self.assertFalse(v.blocked)

    def test_hit_records_position(self):
        v = self.m.analyse("前缀大法弟子后缀")
        self.assertTrue(v.hits)
        hit = v.hits[0]
        self.assertEqual(hit.term, "大法弟子")
        self.assertEqual(hit.start, 2)

    def test_prefers_longest_match(self):
        # 长候选优先，避免把长词切成碎片。
        v = self.m.analyse("大纪元新闻网")
        self.assertEqual(len(v.hits), 1)
        self.assertGreaterEqual(len(v.hits[0].term), 4)

    def test_multiple_levels_preserved(self):
        # 同一字符串可能带多个等级；保留全集供上层判断，取高者作保守值。
        levels = self.m.levels_of("4.25")
        self.assertIsNotNone(levels)
        self.assertGreater(len(levels), 1)
        v = self.m.analyse("4.25")
        self.assertEqual(v.max_level, max(levels))

    def test_no_overlapping_hits(self):
        v = self.m.analyse("大法弟子和大纪元新闻网")
        spans = sorted((h.start, h.start + len(h.term)) for h in v.hits)
        for (_, end_a), (start_b, _) in zip(spans, spans[1:]):
            self.assertLessEqual(end_a, start_b)

    def test_empty_text(self):
        v = self.m.analyse("")
        self.assertEqual(v.hits, [])
        self.assertEqual(v.text_length, 0)


class CorpusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.m = semantic.load_default()

    def test_all_cases_in_expected_range(self):
        failures = []
        for case in corpus.load():
            v = self.m.analyse(str(case["text"]))
            lo, hi = case["expect_min"], case["expect_max"]
            if not (lo <= v.max_level <= hi):
                failures.append(
                    f"{case['id']}: level={v.max_level} expected {lo}..{hi}"
                )
        self.assertEqual(failures, [], "\n".join(failures))

    def test_safe_cases_are_truly_clean(self):
        for case in corpus.load():
            if case["class"] != "safe":
                continue
            v = self.m.analyse(str(case["text"]))
            self.assertEqual(v.max_level, 0, case["id"])


class HonestLimitsTest(unittest.TestCase):
    """把"做不到"的部分钉成测试，防止文档与代码漂移。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.m = semantic.load_default()

    def test_hong_hei_is_not_a_full_grading(self):
        # 记录真实数据形状：绝大多数 sense 等级为 0。若未来数据变化，
        # 说明导入或解码改动了，需要重新审视 docs/falunword-format.md。
        data = semantic.load_default()
        zero = sum(1 for t in data._levels.values() if max(t) == 0)
        self.assertGreater(zero, len(data) * 0.8)

    def test_jiuping_is_level_zero(self):
        # 反直觉但真实的案例：所有「九评」词条等级都是 0。
        v = self.m.analyse("《九评共产党》")
        self.assertLessEqual(v.max_level, 1)


if __name__ == "__main__":
    unittest.main()


class BridgeTest(unittest.TestCase):
    """Converting HNC hits into policy findings, without inventing actions."""

    @classmethod
    def setUpClass(cls):
        cls.matcher = semantic.load_default()

    def test_level_zero_hits_are_not_evidence(self):
        # 89% of the score table is level 0, much of it short ASCII fragments
        # that occur inside ordinary words. Emitting them made normal sentences
        # look suspicious.
        for text in ("clean text here", "hello world", "this is normal"):
            self.assertEqual(
                semantic.analyse_findings(self.matcher, text), [],
                msg=f"false positive on {text!r}")

    def test_finding_shape(self):
        from green_dam.policy import KIND_SCORED
        # Find any level>=1 term to exercise the converter without assuming
        # specific content: take one from the table itself.
        levels = self.matcher._levels
        term = next(t for t, v in levels.items() if max(v) >= 1)
        findings = semantic.analyse_findings(self.matcher, f"x{term}y")
        self.assertTrue(findings)
        got = findings[0]
        self.assertEqual(got.kind, KIND_SCORED)
        self.assertTrue(got.attested)
        self.assertIsNone(got.action)
        self.assertEqual(got.sources, ("falun-terms",))

    def test_span_points_at_the_hit(self):
        levels = self.matcher._levels
        term = next(t for t, v in levels.items() if max(v) >= 1)
        text = f"prefix{term}suffix"
        findings = semantic.analyse_findings(self.matcher, text)
        start, end = findings[0].span
        self.assertEqual(text[start:end], findings[0].matched)

    def test_no_action_even_for_high_levels(self):
        # The score-to-action mapping is unknown; a high level is still just a
        # level.
        levels = self.matcher._levels
        term = next(t for t, v in levels.items() if max(v) >= 7)
        findings = semantic.analyse_findings(self.matcher, term)
        self.assertTrue(all(f.action is None for f in findings))
