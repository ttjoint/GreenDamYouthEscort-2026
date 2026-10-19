"""lv 准则（论文 11.1 / 11.2）与晋耀红 9 模块中的工程约束。

## 这组测试的来历

全部来自本轮实测到的**真实误切**。每一条都能在
`data/hnc_archive/text/books_hnc_book2111.htm.txt` 或
`books_hnc_book7072.htm.txt` 找到对应的原则。

原则一旦不落实，句子就会被切得面目全非——例如：

    我们班同学在教室里认真地看书
    修前：B(我们班) + B(学) + Cn(教室里认真地看书)   ← 谓语被吞进辅块
    修后：B(我们班同学) + Cn(教室里认真地) + E(看书)

## 核心原则（原文）

> l 准则仅用于判定辅语义块，对主语义块的判定只有 v 准则可以利用。
> 所以，标准格式语句的主语义块切分通常反而比较困难。

早期实现正好反了：拿介词去切主块，于是辅块延到分句尾、把谓语吃掉。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam.chunker import SentenceChunker  # noqa: E402


class LVPrincipleTest(unittest.TestCase):
    """l 准则只切辅块；辅块右边界靠 v 信号（体标记 / 状语尾）定。"""

    def setUp(self):
        self.c = SentenceChunker()

    def test_aux_block_does_not_swallow_predicate(self):
        # 实测 bug：“在教室里认真地看书”整段被当成 Cn，谓语消失。
        r = self.c.chunk("我们班同学在教室里认真地看书。")
        self.assertIn("E", r.pattern)
        e = [x for x in r.chunks if x.role.value == "E"]
        self.assertEqual([x.text for x in e], ["看书"])

    def test_aspect_marker_starts_predicate(self):
        # 实测 bug：“在图书馆遇到了”切成 Cn(图书馆遇) + B(到了老朋友)。
        # 体标记“了”是 v 事件的高精度信号，必须在它前面断开。
        r = self.c.chunk("他昨天在图书馆遇到了老朋友。")
        texts = [x.text for x in r.chunks]
        self.assertIn("图书馆", texts)
        self.assertIn("遇到了", texts)

    def test_de_marks_predicate_start(self):
        # “…地 + 谓语”：状语尾“地”之后一定是谓语部分。
        # 当前实现把状语留在 A 块里（已知粗糙），但 E 必须切在“地”后。
        r = self.c.chunk("他认真地分析问题。")
        e = [x for x in r.chunks if x.role.value == "E"]
        self.assertEqual([x.text for x in e], ["分析"])

    def test_de_works_without_verb_list(self):
        # “看书”不在动词表也能靠“地”切开——这正是 v 准则形态信号的价值。
        r = self.c.chunk("他认真地看书。")
        self.assertIn("E", r.pattern)

    def test_main_marker_is_not_a_cut_point(self):
        # “同”是纯主标记符。旧实现把它当切点，“同学”被劈成“同|学”。
        r = self.c.chunk("我们班同学在教室里认真地看书。")
        self.assertIn("我们班同学", [x.text for x in r.chunks])


class DesignatedCharTest(unittest.TestCase):
    """晋耀红 §2.1 的 7 个指定字：的 了 和 是 有 不 在。

    原文：“对它们采取指定的方式，可以大大减轻 E 语义块感知的模糊性。”
    """

    def setUp(self):
        self.c = SentenceChunker()

    def test_none_in_designed_set(self):
        from green_dam.chunker import DESIGNATED_CHARS

        self.assertEqual(DESIGNATED_CHARS, frozenset("的了和是有不在"))

    def test_aspect_le_attaches_to_e(self):
        # 实测 bug：句末“了”被切成独立 B 块（E=取消, B=了）。
        r = self.c.chunk("航班取消了。")
        self.assertNotIn("了", [x.text for x in r.chunks])
        self.assertTrue(any(x.text.endswith("了") for x in r.chunks))

    def test_orphan_designated_char_makes_no_block(self):
        # 指定字单独成段时不独立成块。
        r = self.c.chunk("张三打断了李四的腿。")
        self.assertNotIn("了", [x.text for x in r.chunks])


class WordIntegrityTest(unittest.TestCase):
    """单字 l 标记不能切开词。这是 lv 准则的隐含要求。"""

    def setUp(self):
        self.c = SentenceChunker()

    def test_word_tail_protected(self):
        # 实测 bug：“天气原因”切成 Cn(天气原) + B(因)。
        r = self.c.chunk("由于天气原因，航班取消了。")
        self.assertIn("天气原因", [x.text for x in r.chunks])

    def test_word_head_protected(self):
        # 实测 bug：“现在正在”切成 B(他现) + Cn(正, 在=标记)。
        r = self.c.chunk("他现在正在处理这个问题。")
        texts = [x.text for x in r.chunks]
        self.assertIn("现在", texts)
        self.assertNotIn("正", texts)

    def test_men_ban_tongxue_intact(self):
        # 三个字连在一起都不能丢：“我们班同学”曾被切成“我们班”+ 蒸发“同学”。
        r = self.c.chunk("我们班同学在教室里认真地看书。")
        self.assertEqual(r.chunks[0].text, "我们班同学")


class ConsecutiveVerbsTest(unittest.TestCase):
    """论题 17《论连动句和兼语句》：紧邻的两个动词取后者作 E。"""

    def setUp(self):
        self.c = SentenceChunker()

    def test_pick_e_adjacent(self):
        self.assertEqual(SentenceChunker._pick_e([(0, 2), (2, 4)]), (2, 4))

    def test_pick_e_non_adjacent(self):
        # 带宾语时不能合并（“让他/走”不是连动）
        self.assertEqual(SentenceChunker._pick_e([(0, 2), (3, 4)]), (0, 2))

    def test_biaoshi_fandui(self):
        # 实测 bug：E=表示, B=反（“反对”被拆开且后半丢失）。
        r = self.c.chunk("他对我的建议表示反对。")
        e = [x for x in r.chunks if x.role.value == "E"]
        self.assertEqual([x.text for x in e], ["表示反对"])

    def test_kaishi_shishi(self):
        r = self.c.chunk("按照规定，我们下周一开始实施新方案。")
        e = [x for x in r.chunks if x.role.value == "E"]
        self.assertEqual([x.text for x in e], ["开始实施"])


class EmptyChunkTest(unittest.TestCase):
    """空内容不建块。"""

    def setUp(self):
        self.c = SentenceChunker()

    def test_no_empty_chunk(self):
        # 实测 bug：“由于天气原因”产生一个 text="" 的 Pr 块
        # （扫描器把“因”当第二个标记符）。
        for sent in ("由于天气原因，航班取消了。",
                     "为了完成任务，大家都在加班。"):
            r = self.c.chunk(sent)
            for ch in r.chunks:
                self.assertTrue(ch.text, f"{sent} 产生了空块 {ch}")


if __name__ == "__main__":
    unittest.main()


class MathSymbolTest(unittest.TestCase):
    """论题 34《语义块表示式及其应用》的四类符号对照。"""

    def setUp(self):
        self.c = SentenceChunker()

    def test_math_symbol_mapping(self):
        from green_dam.chunker import ChunkRole

        self.assertEqual(ChunkRole.E.math_symbol, "E")
        for r in (ChunkRole.A, ChunkRole.B, ChunkRole.C):
            self.assertEqual(r.math_symbol, "JK", r)
        for r in (ChunkRole.BK_MS, ChunkRole.BK_CN, ChunkRole.BK_PR):
            self.assertEqual(r.math_symbol, "fK", r)

    def test_math_pattern_formula_2_1(self):
        # 晋耀红式 (2.1)：FJ = Σk(JKi) + E + Σm(JKi)
        r = self.c.chunk("张三打断了李四的腿。")
        self.assertEqual(r.math_pattern(), "JK + E + JK")

    def test_aux_not_in_math_pattern(self):
        # 辅块不是句类函数的一部分，不进结构表示式。
        r = self.c.chunk("他拍着手笑。")
        self.assertNotIn("fK", r.math_pattern())
        self.assertEqual(r.math_pattern(), "JK + E")
