"""chunk_tags：语义块标记符表（CCL-99-003）的完整性测试。

数字锚点全部来自论文原文，防止表在后续维护中被悄悄改坏。
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam.chunk_tags import (  # noqa: E402
    TAG_TABLE,
    TagRole,
    maximal_tag_at,
    roles_of,
    tag_of,
)


class TableIntegrityTest(unittest.TestCase):
    def test_unique_count(self):
        # 三张表逐项清单的唯一介词数（已对照论文 p.18/19 原图逐项核对）。
        # 论文正文说"总计 80 个"，但那是三张表的行内列出次数：
        # "为（去声）/从"三角色兼备，在主表兼辅行与辅表兼两可行
        # 被重复计入（17+11+4=32 主、43+11+2=56 辅恰好对上论文的
        # 32/56，而唯一数 = 17+11+2+43+5 = 78）。
        self.assertEqual(len(TAG_TABLE), 78)

    def test_pure_counts_match_paper(self):
        # 论文：纯主 17（21%/53%）、纯辅 43（54%/77%）、纯两可 5（6%/56%）
        pure = {
            TagRole.MAIN: 0,
            TagRole.SECONDARY: 0,
            TagRole.DUAL: 0,
        }
        for t in TAG_TABLE.values():
            if bin(t.roles.value).count("1") == 1:
                pure[t.roles] += 1
        self.assertEqual(pure[TagRole.MAIN], 17)
        self.assertEqual(pure[TagRole.SECONDARY], 43)
        self.assertEqual(pure[TagRole.DUAL], 5)

    def test_dual_total_is_9(self):
        # 论文：两可标记符 9 = 纯两可 5 + 兼主两可 4（给/至于/为去声/从）
        dual = sum(1 for t in TAG_TABLE.values() if TagRole.DUAL in t.roles)
        self.assertEqual(dual, 9)


class LookupTest(unittest.TestCase):
    def test_pure_main(self):
        self.assertEqual(roles_of("把"), TagRole.MAIN)
        self.assertEqual(roles_of("被"), TagRole.MAIN)

    def test_pure_secondary(self):
        self.assertEqual(roles_of("按照"), TagRole.SECONDARY)
        self.assertEqual(roles_of("为了"), TagRole.SECONDARY)

    def test_pure_dual(self):
        self.assertEqual(roles_of("作为"), TagRole.DUAL)
        self.assertEqual(roles_of("关于"), TagRole.DUAL)

    def test_multi_role(self):
        # "打" 兼主辅；"给" 兼主两可
        self.assertEqual(roles_of("打"), TagRole.MAIN | TagRole.SECONDARY)
        self.assertEqual(roles_of("给"), TagRole.MAIN | TagRole.DUAL)
        # "从"、"为（去声）" 三者皆可
        tri = TagRole.MAIN | TagRole.SECONDARY | TagRole.DUAL
        self.assertEqual(roles_of("从"), tri)
        self.assertEqual(roles_of("为"), tri)  # 默认映射到"为（去声）"

    def test_non_tag(self):
        self.assertEqual(roles_of("苹果"), TagRole.NONE)
        self.assertIsNone(tag_of("苹果"))

    def test_zhe_variants(self):
        # 向着/朝着 映射到 向（着）/朝（着）
        self.assertEqual(roles_of("向着"), TagRole.MAIN | TagRole.SECONDARY)
        self.assertEqual(roles_of("朝着"), TagRole.MAIN | TagRole.SECONDARY)


class MaximalMatchTest(unittest.TestCase):
    def test_longest_first(self):
        # "按照" 应整体命中，而不是退化为单字"按"
        tag, n = maximal_tag_at("按照规定办理", 0)
        self.assertEqual(n, 2)
        self.assertEqual(tag.surface, "按照")

    def test_no_match(self):
        self.assertIsNone(maximal_tag_at("今天天气不错", 0))

    def test_mid_sentence(self):
        tag, n = maximal_tag_at("他为了理想奋斗", 1)
        self.assertEqual(n, 2)
        self.assertEqual(roles_of("为了"), TagRole.SECONDARY)


if __name__ == "__main__":
    unittest.main()
