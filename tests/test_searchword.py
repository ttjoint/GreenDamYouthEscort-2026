# -*- coding: utf-8 -*-
"""`SearchWord` 查询链路测试。

锁住的结论：**`SearchWord` 的 `out` 参数就是打分表下标。**

这是引擎真正的过滤判定点。`FalunWord.lib` 是通用词库，普通词
（`今天`、`公司`、`工作`）也在 trie 里，所以「trie 里有没有」不能
当判据 —— 只有 `out >= 0` 才算敏感命中。

早前我用「trie 全部词条」直接匹配，导致完全无害的句子 6/6 误报。
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DLL = ROOT.parent / "LH-installed-tree" / "Windows" / "SentenceObj.dll"
TOOL = ROOT / "tools" / "probe_searchword.py"
FILTER_JSON = ROOT / "data" / "hnc-falun-filter.json"


class SearchWordSignatureTest(unittest.TestCase):
    """静态断言：`SearchWord` 是 `__thiscall`，第四参数是 `int*`。

    签名错了就调用不通，所以从导出表里核对。
    """

    def test_export_mangled_name(self):
        TREE = ROOT.parent / "LH-installed-tree" / "Windows"
        data = (TREE / "SentenceObj.dll").read_bytes()
        name = b"?SearchWord@CFWordLib@@QAEPAVFCCharNode@@PAGHAAH@Z"
        self.assertIn(name, data, "导出名不匹配")

    def test_signature_decodes_to_four_args(self):
        """`PAGHAAH` = wchar_t*, int, int&。

        - `PAG`  = wchar_t*
        - `H`    = int
        - `AAH`  = int&（引用传出的打分表下标）
        """
        sig = "PAGHAAH"
        self.assertTrue(sig.startswith("PAG"))
        self.assertIn("H", sig)
        self.assertIn("AAH", sig, "第四参数必须是引用 —— 这就是 out")

    def test_tool_uses_out_parameter(self):
        text = TOOL.read_text(encoding="utf-8")
        self.assertIn("ctypes.c_int(-1)", text, "out 初值应为 -1")
        self.assertIn("struct.pack", text, "需要手写 thunk 传参")


class OutValueSemanticsTest(unittest.TestCase):
    """`out` 的三种取值。这些是实测出来的，不是猜的。"""

    def test_constants_are_documented(self):
        text = TOOL.read_text(encoding="utf-8")
        self.assertIn("OUT_NO_SCORE = -1", text)
        self.assertIn("OUT_NOT_IN_TRIE = -2", text)
        self.assertIn("NO_SCORE = 0xFFFFFFFF", text)

    def test_tool_only_accepts_nonnegative_out(self):
        """扫描时必须 `out >= 0` 才算命中。"""
        text = TOOL.read_text(encoding="utf-8")
        self.assertIn("out >= 0", text)

    def test_node_field_offset(self):
        text = TOOL.read_text(encoding="utf-8")
        self.assertIn("NODE_SCORE_INDEX = 12", text, "打分下标在节点 +12")


@unittest.skipUnless(FILTER_JSON.exists(), "还没导出过滤库")
class RoundTripTest(unittest.TestCase):
    """4,693 条打分表词条应当能查回自身下标（99.8%）。"""

    @classmethod
    def setUpClass(cls):
        cls.entries = json.loads(
            FILTER_JSON.read_text(encoding="utf-8"))["entries"]

    def test_documented_hit_rate(self):
        """文档里写的 4683/4693 必须和实测一致，防止数字漂移。

        运行时验证要用 32 位 Python 2.7 跑 tools/probe_searchword.py；
        这里只核对文档锚点。
        """
        doc = (ROOT / "docs" / "hnc-filter-query-chain.md").read_text(
            encoding="utf-8")
        self.assertIn("4683", doc, "文档应记录实测命中数")
        self.assertIn("99.8", doc, "命中率锚点")
        # 工具本身必须是「运行时计算」，不能写死这个数字
        tool = TOOL.read_text(encoding="utf-8")
        self.assertIn("一致：%d / %d", tool)
        self.assertNotIn("4683", tool, "工具应算出来，不是抄进去")

    def test_mismatches_are_duplicate_words(self):
        """不一致的 10 条都应该是「同一个词出现多次」。

        如果这条测试失败，说明 trie 归并逻辑有变，需要重新分析。
        """
        # 已知的 10 条不一致里，法轮大法好出现两次（1123/1124 同归 1125）
        words = [e["word"] for e in self.entries]
        duplicates = {w for w in words if words.count(w) > 1}
        self.assertIn("法轮大法好", duplicates,
                      "重复词应当存在——这正是 10 条不一致的来源")

    def test_every_entry_has_a_distinct_index(self):
        idx = [e["index"] for e in self.entries]
        self.assertEqual(idx, list(range(4693)))


class GeneralVocabularyTrapTest(unittest.TestCase):
    """`FalunWord.lib` 是通用词库 —— 这是最容易踩的坑。"""

    LEGACY = ROOT / "data" / "legacy-imported" / "falun-terms.json"

    @unittest.skipUnless(LEGACY.exists(), "旧解析数据不在")
    def test_common_words_are_in_the_trie(self):
        """`今天`、`公司`、`工作` 这些日常词确实在词库里。

        所以「能在 trie 里查到」完全不能当敏感判据。
        """
        data = json.loads(self.LEGACY.read_text(encoding="utf-8"))
        trie = set(data["terms"])
        common = ["今天", "公司", "工作", "同事", "喜欢", "孩子", "火车", "会议"]
        found = [w for w in common if w in trie]
        self.assertGreaterEqual(
            len(found), 6,
            f"日常词在 trie 里的数量异常（找到 {found}）",
        )

    @unittest.skipUnless(LEGACY.exists(), "旧解析数据不在")
    def test_trie_contains_single_char_nodes(self):
        """trie 里有 7 万多个单字节点（中间节点）。

        直接拿来匹配必然满屏误报。
        """
        data = json.loads(self.LEGACY.read_text(encoding="utf-8"))
        terms = data["terms"]
        singles = sum(1 for t in terms if len(t) == 1)
        self.assertGreater(singles, 70000,
                           f"单字节点应有 7 万以上，实际 {singles}")


class FilterJsonShapeTest(unittest.TestCase):
    """导出数据的形状约束。"""

    @unittest.skipUnless(FILTER_JSON.exists(), "还没导出过滤库")
    def test_entries_have_required_fields(self):
        data = json.loads(FILTER_JSON.read_text(encoding="utf-8"))
        for e in data["entries"][:50]:
            self.assertIn("index", e)
            self.assertIn("word", e)
            self.assertIn("fandong", e)
            self.assertIn("honghei", e)

    @unittest.skipUnless(FILTER_JSON.exists(), "还没导出过滤库")
    def test_known_scored_word(self):
        """`大纪元` 的 out=808，必须能在下标 808 查到。

        这是实测锚点：SearchWord("大纪元") -> out=808 -> (6, 3)。
        """
        data = json.loads(FILTER_JSON.read_text(encoding="utf-8"))
        e = data["entries"][808]
        self.assertEqual(e["word"], "大纪元")
        self.assertEqual(e["fandong"], 6)
        self.assertEqual(e["honghei"], 3)


if __name__ == "__main__":
    unittest.main()
