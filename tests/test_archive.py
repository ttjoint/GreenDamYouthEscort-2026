"""HNC 原始文献归档的可核验性测试。

这里不测"我们实现得对不对"，只测**归档本身还在、且 README 的断言有据**。
理由：`data/hnc_archive/README.md` 里写了不少"原文如此"的话，如果哪天文
件被删或抓取退化，那些断言就成了无据之说。测试就是把它们钉住。
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "data" / "hnc_archive" / "text"


def _read(name: str) -> str:
    p = ARCHIVE / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


@unittest.skipUnless(ARCHIVE.exists(), "归档未随仓库分发")
class ArchiveTest(unittest.TestCase):
    def test_archive_size(self):
        files = list(ARCHIVE.glob("*.txt"))
        self.assertGreater(len(files), 100, "归档页数异常偏少")
        total = sum(len(p.read_text(encoding="utf-8")) for p in files)
        self.assertGreater(total, 400_000, "归档总字数异常偏少")

    def test_wayback_toolbar_stripped(self):
        # 抓取时若没剥掉 Wayback 工具栏，每页开头都会有 "TIMESTAMPS"
        for p in list(ARCHIVE.glob("*.txt"))[:20]:
            head = p.read_text(encoding="utf-8")[:200]
            self.assertNotIn("The Wayback Machine", head,
                             f"{p.name} 开头残留 Wayback 工具栏")

    def test_wuyuanzu_definitions(self):
        """附表 1 的五元组定义（原文）。"""
        s = _read("books_hnc_book2a1.htm.txt")
        self.assertTrue(s, "附表 1 缺失")
        for token in ("动态概念", "因静态", "属性概念", "值表示", "果静态"):
            self.assertIn(token, s, f"五元组定义缺 {token}")

    def test_semantic_block_definitions(self):
        """附表 2 的语义块定义（原文）。"""
        s = _read("books_hnc_book2a2.htm.txt")
        self.assertTrue(s, "附表 2 缺失")
        for token in ("作用者块素", "对象块素", "特征语义块", "内容块素"):
            self.assertIn(token, s, f"语义块定义缺 {token}")

    def test_basic_sentence_categories_57(self):
        """57 个基本句类一级子类。"""
        s = _read("books_hnc_book2a2.htm.txt")
        self.assertIn("总计57种", s)
        self.assertIn("总计3192种", s)

    def test_auxiliary_block_rule3(self):
        """论题 7 第三条：无标记辅块必然是条件辅块。"""
        s = _read("books_hnc_book307.htm.txt")
        self.assertTrue(s, "论题 7 缺失")
        self.assertIn("不带标记的辅块必然是以", s)
        self.assertIn("条件辅块", s)

    def test_auxiliary_block_rule1(self):
        """论题 7 第一条：辅块绝不出现于 E 块之后。"""
        s = _read("books_hnc_book307.htm.txt")
        self.assertIn("绝不会出现在E块之后", s.replace(" ", ""))

    def test_52_topics(self):
        s = _read("hnc52ml.html.txt")
        self.assertTrue(s, "52 论题目录缺失")
        for token in ("论句类假设的策略", "论辅块", "论复杂特征集和句类代码",
                      "论句类转换", "语义块表示式及其应用"):
            self.assertIn(token, s)


if __name__ == "__main__":
    unittest.main()
