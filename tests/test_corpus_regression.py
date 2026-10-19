"""真实语料回归：`docs/corpus-eval.md` 的实测结论。

## 这组测试的性质

和其它测试不同，这里钉住的不是"应该怎样"，而是**实测到的现状**。
目的是防止改动词表后无意识地变好或变坏——以及防止有人看到
134 项全绿就以为分块器已经可用。

## 数字锚点来源

`tools/bench_corpus.py` + `tools/diagnose_chunker.py` 跑
`D:/Docs/_w/小说`（103 文件）与 `D:/Docs/_w/涩涩/限级、成人`
（1029 文件）得到。语料不在仓库里，所以这些测试**不重跑语料**，
只把结论固化成小样本。

## 已知失败清单（不是 TODO，是诚实的账）

下面这些句子来自真实小说，都有谓语，但 `_find_verbs_safe` 认不出：

    看着这个行星上存在了五千年的文明      “看着”“存在”都不在动词表
    它曾经历过比这大得多的巨变          “经历”不在
    他们不想惹麻烦                 “不想”不在
    被毛毛虫国吞并                  “吞并”不在
    那个地球表面可以看得更细了            “看得”不在

根因：动词表 208 条，覆盖真实文本双字 token 的 2.47%。
补到 top-3000 时单块率能从 62.3% 降到 4.5%（作弊上界，混入名词）。
详见 `docs/corpus-eval.md`。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam.chunker import DEFAULT_VERBS, SentenceChunker  # noqa: E402

#: 真实语料里仍会退化成单块的分句。
#: **这份清单变短是好事**——修了词表就把对应行删掉。
KNOWN_UNCHUNKED = (
    "看着这个行星上存在了五千年的文明",
    "它曾经历过比这大得多的巨变",
    "他们不想惹麻烦",
    "被毛毛虫国吞并",
    "那个地球表面可以看得更细了",
    "周围还有一圈亮着屏幕的电脑",
    "他们还能干什么呢",
    "劳动强度更大",
    "这孩子学得很快",
)


class VocabularyCoverageTest(unittest.TestCase):
    """词汇覆盖率是分块成功率的瓶颈（实测）。"""

    def test_verb_table_size_anchor(self):
        # 实测 208 条，覆盖真实双字 token 的 2.47%。
        # 这个数字变大是好事，但如果因为塞进名词而变大，会引入误切。
        self.assertGreaterEqual(len(DEFAULT_VERBS), 200)

    def test_known_gaps_still_gap(self):
        """确认已知缺陷的现状，不是断言它正确。

        这些句子都**有**谓语，却识别不出来。测试通过说明缺陷仍然存在
        且没有被意外修好——如果有人补了词表，这条会失败，那时应该
        把对应句子从 KNOWN_UNCHUNKED 移到 `test_chunked_after_fix`。
        """
        c = SentenceChunker()
        for sent in KNOWN_UNCHUNKED:
            with self.subTest(sent=sent):
                self.assertEqual(c._find_verbs_safe(sent), [],
                                 f"{sent!r} 居然认出谓语了，请更新测试")


class ChunkedRealSentencesTest(unittest.TestCase):
    """真实语料里能切开的句子（证明不是全军覆没）。"""

    def setUp(self):
        self.c = SentenceChunker()

    def test_real_sentences_produce_structure(self):
        ok = [
            "他昨天在图书馆遇到了老朋友",
            "我们班同学在教室里认真地看书",
            "张三打断了李四的腿",
            "日本代表带着满意的笑容离开会场",
            "美国军方向波斯尼亚战争的受害者空投救援物资",
        ]
        for sent in ok:
            with self.subTest(sent=sent):
                self.assertGreater(len(self.c.chunk(sent).chunks), 1,
                                   f"{sent!r} 退化成单块了")


class StructuralFeaturesHaveNoDiscriminativePowerTest(unittest.TestCase):
    """实测结论：结构特征不能当屏蔽判据。

    `tools/bench_discriminate.py` 在 103 vs 1029 个文件上算：
    所有特征的准确率都低于"全判成人"基准 90.90%。

    这条测试固化的是**结论**，不是算法：分块层只输出块角色，
    不输出任何"敏感度"分值。如果哪一天加上了，这里会失败，
    那时必须同时把 `docs/corpus-eval.md` 的结论推翻重写。
    """

    def test_chunking_exposes_no_sensitivity_score(self):
        c = SentenceChunker()
        chunking = c.chunk("他昨天在图书馆遇到了老朋友。")
        # 块对象只有角色/文本/位置/置信度，没有敏感度维度。
        self.assertFalse(hasattr(chunking, "score"))
        for ch in chunking.chunks:
            self.assertFalse(hasattr(ch, "sensitivity"))
            self.assertFalse(hasattr(ch, "level"))


if __name__ == "__main__":
    unittest.main()
