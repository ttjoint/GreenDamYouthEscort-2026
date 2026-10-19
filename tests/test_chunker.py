"""chunker：语义块分块器测试。

测试分三类：
1. **规则锚点**——CCL-99-003 原文例子必须切对（`v+着+v`、介词辅块）。
2. **回归锁**——开发中实测出来的误切，全部钉死，防止改词表时退化。
3. **接口**——`to_frame_hint()` 必须明确返回 None，不许悄悄造假映射。
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam.chunker import (  # noqa: E402
    ChunkRole,
    Chunking,
    SentenceChunker,
)


class PaperExamplesTest(unittest.TestCase):
    """`[张]` §3/§4 原文例子。"""

    def setUp(self):
        self.c = SentenceChunker()

    def test_v_zhe_v(self):
        # “他拍着手笑” —— 辅块“拍着”+ E“笑”，主语留在 A
        r = self.c.chunk("他拍着手笑。")
        self.assertEqual(r.pattern, "A+Ms+E")
        roles = {ch.role for ch in r.chunks}
        self.assertIn(ChunkRole.BK_MS, roles)
        self.assertIn(ChunkRole.E, roles)

    def test_zhipei_verb_not_to_e(self):
        # “他拉长了脸瞧着我们” —— “拉”不应被单字规则抢成 E
        r = self.c.chunk("他拉长了脸瞧着我们。")
        self.assertNotEqual(r.pattern, "A+E+B")

    def test_purpose_marker(self):
        # “为了更好地……” —— “为了” 表目的，辅块 Rt
        r = self.c.chunk("为了更好地担负起理论工作的历史重任，我们必须严格要求自己。")
        self.assertTrue(r.chunks)
        first = r.chunks[0]
        self.assertEqual(first.marker, "为了")
        self.assertEqual(first.role, ChunkRole.BK_RT)
        self.assertAlmostEqual(first.confidence, 1.0)

    def test_transfer_sentence(self):
        # `[王]` 的物转移句例子：应得到 A+E+B
        r = self.c.chunk("美国军方向波斯尼亚战争的受害者空投救援物资")
        self.assertEqual(r.pattern, "A+E+B")
        self.assertEqual(r.main[1].text, "空投")

    def test_marker_roles_come_from_table(self):
        # “在”是辅块标记符（CCL-99 表）
        r = self.c.chunk("在北京召开会议")
        markers = [ch.marker for ch in r.chunks if ch.marker]
        self.assertIn("在", markers)


class RegressionLockTest(unittest.TestCase):
    """开发中实测到的真实误切。每条都对应一个修过的 bug。"""

    def setUp(self):
        self.c = SentenceChunker()

    def test_bad_single_char_split(self):
        # bug：单字“打”把“张三打了个…”切成 ? 块
        r = self.c.chunk("张三打断了李四的腿。")
        self.assertEqual(r.pattern, "A+E+B")
        # 体标记“了”属于 E 自身形态，不是独立块。
        # 旧断言写的是 "打断"，丢弃了体标记；后来按 lv 准则修正。
        self.assertEqual(r.main[1].text, "打断了")

    def test_verb_beats_single_char_marker(self):
        # bug：“离开会场”被切成辅块“开会场”，因为“离”是标记符
        r = self.c.chunk("日本代表带着满意的笑容离开会场。")
        self.assertIn("离开", [ch.text for ch in r.chunks])
        self.assertNotIn("开会场", [ch.text for ch in r.chunks])

    def test_compound_noun_not_split(self):
        # bug：“笑容”被单字“笑”切开成 E+B。辅块可能吞入“满意的笑容”，
        # 那属于边界偏宽，可接受；不可接受的是把“笑”单独立为 E。
        r = self.c.chunk("日本代表带着满意的笑容离开会场。")
        self.assertNotIn("笑", [ch.text for ch in r.chunks])
        self.assertIn("离开", [ch.text for ch in r.chunks])

    def test_orphan_marker_dropped(self):
        # bug：“于”残留成孤立 B 块
        r = self.c.chunk("公司将于明天召开会议。")
        self.assertNotIn("于", [ch.text for ch in r.chunks])

    def test_ba_is_not_secondary(self):
        # “把”纯主标记符，不该产生辅块
        r = self.c.chunk("他们把大批工程都包揽下来了。")
        self.assertEqual(r.secondary, [])


class InterfaceTest(unittest.TestCase):
    def test_frame_hint_is_none(self):
        # 刻意不伪造 role_a/role_b 整数映射
        r = SentenceChunker().chunk("张三打断了李四的腿。")
        self.assertIsNone(r.to_frame_hint())

    def test_empty_sentence(self):
        self.assertEqual(SentenceChunker().chunk("").chunks, [])

    def test_no_verb_falls_back_to_b(self):
        # 无谓语 → 退化为低置信 B 块，而不是硬猜 E
        r = SentenceChunker().chunk("美丽的春天")
        self.assertEqual(r.pattern, "B")
        self.assertLess(r.chunks[0].confidence, 0.6)

    def test_pattern_stable(self):
        # pattern 是 `[王]` 式 4 的输入，必须是纯 ASCII 块名
        r = SentenceChunker().chunk("张三打断了李四的腿。")
        self.assertTrue(all(ord(ch) < 128 for ch in r.pattern))


class TopicSevenTest(unittest.TestCase):
    """论题 7《论辅块》三条实证原则（`data/hnc_archive/text/book307.htm`）。

    这三条是原文直接可落地的判据，不是二手转述：
      1. "辅块……绝不会出现在 E 块之后"
      2. "辅块之是否带标记不理会句类格式之变"
      3. "不带标记的辅块必然是以 K(jm) 为内容的条件辅块，
          其中主要是时间条件辅块"
    """

    def setUp(self):
        self.c = SentenceChunker()

    def test_rule1_no_secondary_after_e(self):
        # 原则 1：E 之后不得出现辅块
        r = self.c.chunk("张三打断了李四的腿。")
        seen_e = False
        for ch in r.chunks:
            if ch.role == ChunkRole.E:
                seen_e = True
            elif seen_e:
                self.assertTrue(
                    ch.role.is_main,
                    f"E 之后出现了辅块 {ch.role.value}，违反论题 7 原则 1",
                )

    def test_rule3_bare_clause_is_conditional(self):
        # 原则 3：无标记辅块 → 条件辅块
        r = self.c.chunk("如果下雨，比赛就取消。")
        self.assertEqual(r.chunks[0].role, ChunkRole.BK_CN)

    def test_rule3_time_prefix_split_from_subject(self):
        # 原则 3：时间前缀单独成 Cn，不能把主语一起吞进去
        r = self.c.chunk("三天后我们将开始施工。")
        self.assertEqual(r.chunks[0].role, ChunkRole.BK_CN)
        self.assertEqual(r.chunks[0].text, "三天后")
        self.assertIn(ChunkRole.A, [ch.role for ch in r.chunks])

    def test_chinese_numeral_time(self):
        # 回归：早期用 str.isdigit()，"三天后" 永远判不成时间
        self.assertTrue(self.c._looks_conditional("三天后我们开会"))
        self.assertTrue(self.c._looks_conditional("一星期后开会"))


if __name__ == "__main__":
    unittest.main()
