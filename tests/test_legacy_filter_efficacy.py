# -*- coding: utf-8 -*-
"""原厂过滤效果测试。

锁住 `docs/legacy-filter-efficacy.md` 里的关键数字，防止以后改动
评测脚本时数字悄悄漂移。

## 核心结论

原厂这套东西的效果完全来自「词表小」：

- trie 层（142,742 条，含大量普通词）：正样本全中，但**无害句子也全中**。
- 打分表层（4,693 条，原文片段）：0 误报，但**换个词形就漏**。

这不是调参能解决的，是纯字面匹配的结构性上限。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import bench_legacy_filter as B  # noqa: E402


@unittest.skipUnless(B.FILTER_JSON.exists() and B.LEGACY_JSON.exists(),
                     "评测数据不在")
class TrieLayerIsUnusableTest(unittest.TestCase):
    """trie 层不能当敏感词判据。"""

    @classmethod
    def setUpClass(cls):
        cls.m = B.Matcher(B.load_terms(), B.load_scored())

    def test_trie_flags_every_harmless_sentence(self):
        """10 句无害日常句，trie 层全部误报。

        `FalunWord.lib` 是通用词库，`今天`/`公司`/`工作` 都在里面。
        """
        r = B.evaluate(self.m, "trie_all", B.POSITIVE, B.NEGATIVE)
        self.assertEqual(r["neg_hit"], r["neg_total"],
                         "trie 层应当全部误报，这是它不可用的证据")

    def test_len2_still_flags_everything(self):
        """去掉单字中间节点也没用。"""
        r = B.evaluate(self.m, "trie_len2", B.POSITIVE, B.NEGATIVE)
        self.assertEqual(r["neg_hit"], r["neg_total"],
                         "≥2 字也不能救 —— 普通双字词太多")

    def test_common_words_present_in_trie(self):
        """误报的机制：普通词确实在词库里。

        注意 `工作` 会被最长匹配成 `务工作`（词库里有这个片段），
        这本身也是 trie 层的另一个毛病 —— 它连词边界都不保证。
        """
        hits = self.m.scan("他在公司负责财务工作，同事都很喜欢他", "trie_len2")
        self.assertIn("公司", hits)
        self.assertIn("同事", hits)
        self.assertIn("喜欢", hits)
        # 词边界不可靠：`工作` 被切成了 `务工作`
        self.assertTrue(any("工作" in h for h in hits),
                        f"应能匹配到含「工作」的片段，实际 {hits}")


@unittest.skipUnless(B.FILTER_JSON.exists(), "评测数据不在")
class ScoredLayerTest(unittest.TestCase):
    """打分表层：0 误报，但漏掉换词形的写法。"""

    @classmethod
    def setUpClass(cls):
        cls.m = B.Matcher(B.load_terms(), B.load_scored())

    def test_no_false_positives(self):
        r = B.evaluate(self.m, "scored", B.POSITIVE, B.NEGATIVE)
        self.assertEqual(r["neg_hit"], 0, "打分表层不应误报无害句")

    def test_catches_most_positives(self):
        r = B.evaluate(self.m, "scored", B.POSITIVE, B.NEGATIVE)
        self.assertEqual(r["pos_hit"], 7,
                         f"实测是 7/10，实际 {r['pos_hit']}")

    def test_bare_word_is_the_failure_mode(self):
        """`他是法轮功学员` 漏掉 —— 这就是纯字面匹配的死穴。

        打分表里只有 `'法轮功'`（带引号），裸词没登记。
        """
        hits = self.m.scan("他是法轮功学员", "scored")
        self.assertEqual(hits, [], "裸词不应命中（证明漏检机制）")

    def test_quoted_form_does_hit(self):
        """带引号的形态能命中，说明它认的是原文片段不是概念。"""
        hits = self.m.scan("'法轮功'", "scored")
        self.assertNotEqual(hits, [], "带引号形态应当在表里")


@unittest.skipUnless(B.FILTER_JSON.exists(), "评测数据不在")
class ScoredTableShapeTest(unittest.TestCase):
    """打分表的形态决定了它的能力上限。"""

    def test_quote_decorated_entries_exist(self):
        """287 条带引号/括号修饰 —— 说明是原文片段不是规范词表。"""
        entries = B.load_scored()
        decorated = [
            e for e in entries
            if e["word"].startswith(("\"", "'", "“", "《", "(", "[", "<"))
        ]
        self.assertGreater(len(decorated), 250,
                           f"带修饰的条目应当有 250+，实际 {len(decorated)}")
        self.assertLess(len(decorated), 350)

    def test_plain_forms_dominate(self):
        """但绝大多数是不带修饰的 —— 所以不是全都不可用。"""
        entries = B.load_scored()
        plain = len(entries) - 287
        self.assertGreater(plain, 4000)


class StrategyComparisonTest(unittest.TestCase):
    """三种策略的排序关系必须稳定：trie 全误报 > 打分表 0 误报。"""

    @unittest.skipUnless(B.FILTER_JSON.exists() and B.LEGACY_JSON.exists(),
                         "评测数据不在")
    def test_scored_has_fewest_false_positives(self):
        m = B.Matcher(B.load_terms(), B.load_scored())
        fp = {}
        for mode in ("trie_all", "trie_len2", "scored"):
            r = B.evaluate(m, mode, B.POSITIVE, B.NEGATIVE)
            fp[mode] = r["neg_hit"]
        self.assertEqual(fp["scored"], 0)
        self.assertGreater(fp["trie_all"], fp["scored"])
        self.assertGreaterEqual(fp["trie_len2"], fp["scored"])


if __name__ == "__main__":
    unittest.main()
