"""句类相似度测试（`[王]` 式 1/3/4/5/6）。

测试重点不是"分数好看"，而是：

1. **公式结构**必须与论文一致（各分项真的参与了加权）。
2. **权重**必须可从外部替换（做 A/B 实验的前提）。
3. **退化项**必须被如实标注，不能拿替代实现冒充论文原法。
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam.similarity import (  # noqa: E402
    SentenceSimilarity,
    Weights,
    char_overlap_similarity,
)


class FormulaTest(unittest.TestCase):
    def setUp(self):
        self.s = SentenceSimilarity()

    def test_eq1_weighted_sum(self):
        # 式 (1)：total = α·cat + β·gse + γ·sem
        r = self.s.compare("什么是防火墙", "防火墙是什么")
        w = self.s.w
        expect = w.alpha * r.sim_cat + w.beta * r.sim_gse + w.gamma * r.sim_sem
        self.assertAlmostEqual(r.total, expect, places=9)

    def test_eq3_gse_composition(self):
        # 式 (3)：sim_gse = β1·sim_gsem + β2·sim_gsef
        r = self.s.compare("张三打断了李四的腿", "张三打断了王五的腿")
        w = self.s.w
        self.assertAlmostEqual(
            r.sim_gse, w.beta1 * r.sim_gsem + w.beta2 * r.sim_gsef, places=9
        )

    def test_eq6_sem_composition(self):
        # 式 (6)：sim_sem = γ1·sim_semm + (1-γ1)·sim_semf
        r = self.s.compare("张三打断了李四的腿", "公司明天在北京召开会议")
        w = self.s.w
        self.assertAlmostEqual(
            r.sim_sem, w.gamma1 * r.sim_semm + (1 - w.gamma1) * r.sim_semf,
            places=9,
        )

    def test_weights_default_from_paper(self):
        # 论文调优值，改动需有据
        w = Weights()
        self.assertEqual(
            (w.alpha, w.beta, w.gamma, w.beta1, w.beta2, w.gamma1),
            (0.3, 0.3, 0.4, 0.7, 0.3, 0.8),
        )
        self.assertEqual((w.beta11, w.beta21, w.beta22), (0.5, 0.7, 0.4))

    def test_weights_injectable(self):
        # 权重必须可换，否则没法做 A/B
        s2 = SentenceSimilarity(weights=Weights(alpha=0.0, beta=0.0, gamma=1.0))
        r = s2.compare("什么是防火墙", "防火墙是什么")
        self.assertEqual(r.total, r.sim_sem)


class BehaviourTest(unittest.TestCase):
    def setUp(self):
        self.s = SentenceSimilarity()

    def test_paraphrase_beats_unrelated(self):
        near = self.s.compare("什么是防火墙", "防火墙是什么").total
        far = self.s.compare("什么是防火墙", "为什么天空是蓝色").total
        self.assertGreater(near, far)

    def test_unrelated_is_zero(self):
        r = self.s.compare("张三打断了李四的腿", "公司明天在北京召开会议")
        self.assertEqual(r.total, 0.0)

    def test_focus_word_longest_wins(self):
        # 回归：“天空为什么是蓝色”里的“是什么”曾抢走“为什么”，
        # 导致同属“原因”类的两句被判为不同类。
        r = self.s.compare("为什么天空是蓝色的", "天空为什么是蓝色")
        self.assertGreater(r.sim_cat, 0.0)

    def test_synonym_question_words(self):
        # “怎么”与“如何”同属过程类
        r = self.s.compare("怎么办理护照", "如何办理护照")
        self.assertEqual(r.sim_cat, 1.0)


class HonestyTest(unittest.TestCase):
    """退化项必须被标注，否则分数会被误当成论文原法结果。"""

    def setUp(self):
        self.s = SentenceSimilarity()

    def test_degraded_is_reported(self):
        r = self.s.compare("什么是防火墙", "防火墙是什么")
        self.assertTrue(r.degraded)
        joined = " ".join(r.degraded)
        self.assertIn("sim_cat", joined)
        self.assertIn("词汇语义相似度", joined)

    def test_empty_sentence_flagged(self):
        r = self.s.compare("", "")
        self.assertTrue(r.notes)


class OverlapTest(unittest.TestCase):
    def test_identical(self):
        self.assertEqual(char_overlap_similarity("张三", "张三"), 1.0)

    def test_disjoint(self):
        self.assertEqual(char_overlap_similarity("张三", "李四"), 0.0)

    def test_word_order_insensitive(self):
        # 选择 n-gram Jaccard 的理由：中文短句语序变动常见
        a = char_overlap_similarity("张三打断了李四", "李四被张三打断")
        self.assertGreater(a, 0.0)


if __name__ == "__main__":
    unittest.main()
