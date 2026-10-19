# -*- coding: utf-8 -*-
"""分句层测试：碎片句清理与附着成分粘合。

## 为什么单独测这一层

`docs/corpus-eval.md` 记录过退化样本前几名：

    ×5123  '」'      ×2454  '”'      ×368  '「啊'      ×196  '啊'

这些**根本不是句子**，是分句器切出来的引号碎片和语气词。
它们没有谓语，必然退化成无标记块，把有效率拉低了好几个百分点。

修在分句层比修在分块层便宜得多：分块器不该为"输入不是句子"负责。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from green_dam.chunker import _split_clauses  # noqa: E402


def pieces(sentence: str) -> list[str]:
    return [sentence[a:b] for a, b in _split_clauses(sentence)]


class QuoteHandlingTest(unittest.TestCase):
    """引号内部不切，引号闭合后立刻断开。"""

    def test_quote_interior_comma_does_not_split(self):
        """`「啊，我不知道。」` 内部的逗号不能切开。

        切开会得到 `「啊` 这种孤立的感叹分句。
        """
        got = pieces("「啊，我不知道。」")
        self.assertEqual(got, ["「啊，我不知道。」"])

    def test_quote_close_breaks(self):
        """引文闭合后应断开，叙述另起一句。"""
        got = pieces("「喂，你过来一下。」她说")
        self.assertEqual(got, ["「喂，你过来一下。」", "她说"])

    def test_nested_double_quote(self):
        got = pieces("“不，我不去。”他摇了摇头")
        self.assertEqual(got, ["“不，我不去。”", "他摇了摇头"])

    def test_speaker_verb_kept_with_quote(self):
        """`他说道：` 和后面的引文不该被拆开。

        拆开会让 `他说道` 变成一个无内容的独立分句 —— 它本来就没有
        宾语，单独成句必然退化成无标记块。

        早期版本的断言是 `["他说道", "「好。」"]`（拆开），与它自己
        的 docstring 矛盾。分句层的 `_is_attach_fragment()` 现在把
        提示语和后面黏在一起，断言随之修正。
        """
        got = pieces("他说道：「好。」")
        self.assertEqual(got, ["他说道：「好。」"],
                         "说话动词必须和它的引文待在一起")

    def test_full_sentence(self):
        got = pieces("他说道：「啊，我不知道。」然后就走开了。")
        self.assertEqual(got,
                         ["他说道：「啊，我不知道。」", "然后就走开了"])


class FragmentDropTest(unittest.TestCase):
    """纯标点、纯语气词的碎片不产出分句。"""

    def test_punctuation_only(self):
        self.assertEqual(pieces("，。！"), [])

    def test_single_interjection(self):
        """`啊` `嗯` 单独成句时丢弃。

        它们没有谓语，留着只会退化成无标记块。
        """
        self.assertEqual(pieces("「啊」"), [])

    def test_two_char_word_kept(self):
        """`好的` 是两个词素，不能当语气词丢掉。"""
        self.assertIn("好的", pieces("好的，我这就来"))

    def test_quote_with_content_kept(self):
        self.assertEqual(pieces("「不」"), ["「不」"],
                         "带引号的单字有强调作用，保留")


class AttachTest(unittest.TestCase):
    """孤立连词/副词粘到后一句，不单独成句。"""

    def test_conjunction_attaches_forward(self):
        """`可是，他又能怎么办呢` 的 `可是` 不能单独成句。

        实测这是退化样本里的高频项（`但`×110、`所以`×56）。
        """
        got = pieces("可是，他又能怎么办呢")
        self.assertEqual(got, ["可是，他又能怎么办呢"])

    def test_adverb_attaches_forward(self):
        got = pieces("突然，门开了")
        self.assertEqual(got, ["突然，门开了"])

    def test_plain_clause_untouched(self):
        got = pieces("公司决定下周开始实施新方案")
        self.assertEqual(got, ["公司决定下周开始实施新方案"])


class SpanIntegrityTest(unittest.TestCase):
    """返回的是原串下标，必须能原样取回。"""

    CASES = [
        "他说道：「啊，我不知道。」然后就走开了。",
        "「喂，你过来一下。」她说。",
        "可是，他又能怎么办呢？",
        "第一句。第二句！第三句？",
    ]

    def test_spans_index_into_original(self):
        for text in self.CASES:
            with self.subTest(text=text):
                for a, b in _split_clauses(text):
                    self.assertTrue(0 <= a < b <= len(text))
                    self.assertEqual(text[a:b], text[a:b])

    def test_no_overlap_and_ordered(self):
        for text in self.CASES:
            with self.subTest(text=text):
                spans = _split_clauses(text)
                for (a1, b1), (a2, _b2) in zip(spans, spans[1:]):
                    self.assertLessEqual(b1, a2, "区间重叠或乱序")


if __name__ == "__main__":
    unittest.main()
