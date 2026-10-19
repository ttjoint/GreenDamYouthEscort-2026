"""l 类概念层级（论文 11.1《语义块切分的基本准则：lv 准则》）。

锚点全部来自原文 `data/hnc_archive/text/books_hnc_book2111.htm.txt`。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam.l_concepts import (  # noqa: E402
    LClass,
    L_TABLE,
    cuts_aux_block,
    cuts_main_block,
    l_of,
)


class LVQuoteTest(unittest.TestCase):
    """原文点名的分派必须在表里。"""

    def test_l1_l3_are_aux_indicators(self):
        # 原文：“l1 和 l3 是辅语义块指示符”
        self.assertEqual(LClass.L1.value, "l1")
        self.assertEqual(LClass.L3.value, "l3")

    def test_l0_l2_are_main_indicators(self):
        # 原文：“l0 和 l2 是主语义块指示符”
        self.assertEqual(LClass.L0.value, "l0")
        self.assertEqual(LClass.L2.value, "l2")

    def test_attested_entries(self):
        # 原文明确点名的几个：l41=的、l42=of、l40=关系代词、
        # “根据”=lv8012l、“从”=lj80425、“到”=lj80426
        for surface in ("的", "of", "根据", "从", "到"):
            node = l_of(surface)
            self.assertIsNotNone(node, surface)
            self.assertTrue(node.attested, f"{surface} 应是原文点名项")

    def test_l9_is_demonstrative_pronoun(self):
        # 原文：“我们仅将指示代词纳入语言逻辑概念 l9”
        for w in ("这", "那", "此"):
            self.assertEqual(l_of(w).cls, LClass.L9)


class MainCuttingIsEmptyTest(unittest.TestCase):
    """l 准则不切主块——这是准则性结论，不是实现欠缺。"""

    def test_quote(self):
        # 原文：“l 准则仅用于判定辅语义块，对主语义块的判定只有
        # v 准则可以利用。”
        for w in ("的", "这", "根据", "从", "到", "在", "按照"):
            self.assertFalse(cuts_main_block(w), w)

    def test_aux_cutting_works(self):
        self.assertTrue(cuts_aux_block("在"))
        self.assertTrue(cuts_aux_block("按照"))
        self.assertTrue(cuts_aux_block("根据"))  # l8 也是辅块切分标志

    def test_composition_l4_is_not_a_cutter(self):
        # l4 是组合指示符（“的”），不是块边界。
        self.assertFalse(cuts_aux_block("的"))
        self.assertFalse(cuts_main_block("的"))


class HonestyBoundaryTest(unittest.TestCase):
    """没把握的条目必须标 attested=False，不许冒充原文事实。"""

    def test_inferred_entries_flagged(self):
        node = l_of("按照")
        self.assertIsNotNone(node)
        self.assertFalse(node.attested)

    def test_table_nonempty(self):
        self.assertGreater(len(L_TABLE), 20)


if __name__ == "__main__":
    unittest.main()
