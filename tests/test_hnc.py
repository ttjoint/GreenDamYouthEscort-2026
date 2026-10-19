"""HNC 句类判定器的测试。

关键的一条是 `test_matches_native_binary`：用 Unicorn 模拟原 DLL 的
`0x10030650`，逐条比对 Python 复刻。这是等价性验证，不是"看起来对"。

这一验证抓出了四个真实错误，全部记录在案：

1. 跳表前两项读反，`role_a=1/2` 结果整体互换。
2. `role_b==2` 分支漏掉内层对 `[+0x14]` 的再判定。
3. STATE 分支漏掉 `role2==2` 前置条件。
4. `role_b==0` 分支漏掉 `spare` 二级判据。

如果哪天有人"简化"了 `classify`，这里会立刻红。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from green_dam.hnc import (  # noqa: E402
    TABLE,
    ConceptClass,
    Frame,
    SentenceCategory,
    classify,
    concept_level,
)

LEGACY_DLL = ROOT.parent / "LH-installed-tree" / "Windows" / "SentenceObj.dll"


class TableTest(unittest.TestCase):
    def test_table_has_all_codes(self):
        self.assertGreaterEqual(len(TABLE), 24)

    def test_families_are_1_2_3(self):
        # `!0` 是 2 字节短码（"!" + "0"），family 读出来是 0。
        # 它是原表里真实存在的条目，不是笔误，所以按特例处理而不是删掉。
        families = {c.family for c in TABLE.values()}
        self.assertEqual(families - {0}, {1, 2, 3}, families)

    def test_codes_start_with_bang(self):
        for code in TABLE:
            self.assertTrue(code.startswith("!"), code)

    def test_short_codes_exist(self):
        # 原表里确实存在 !0 / !233 / !320 / !330 这类短码，
        # 不能因为"看起来不像句类码"就删掉。
        for short in ("!0", "!233", "!320", "!330"):
            self.assertIn(short, TABLE)


class GuardTest(unittest.TestCase):
    def test_participants_over_three_rejected(self):
        self.assertIsNone(classify(Frame(1, 1, 4, ConceptClass.ACTION, 0, role2=1)))

    def test_role_b_over_two_rejected(self):
        for b in (3, 4, 9):
            self.assertIsNone(classify(Frame(1, b, 1, ConceptClass.ACTION, 0, role2=b)))

    def test_unknown_concept_class_rejected(self):
        self.assertIsNone(classify(Frame(1, 1, 1, 0x50, 0, role2=1)))

    def test_role_zero_must_be_two(self):
        self.assertIsNone(classify(Frame(1, 0, 1, ConceptClass.ACTION, 0, role2=0)))
        self.assertIsNotNone(classify(Frame(2, 0, 2, ConceptClass.ACTION, 0, role2=0)))


class KnownOutcomeTest(unittest.TestCase):
    """具体输出，来自反汇编与模拟器双向确认。"""

    def test_action_a4_b1_is_31123(self):
        self.assertEqual(
            str(classify(Frame(4, 1, 1, ConceptClass.ACTION, 0, role2=1))), "!31123"
        )

    def test_action_a3_b1_n1_is_31114(self):
        self.assertEqual(
            str(classify(Frame(3, 1, 1, ConceptClass.ACTION, 0, role2=1))), "!31114"
        )

    def test_action_a3_b1_n2_is_114(self):
        self.assertEqual(
            str(classify(Frame(3, 1, 2, ConceptClass.ACTION, 0, role2=1))), "!114"
        )

    def test_action_a2_b1_n2_is_113(self):
        self.assertEqual(
            str(classify(Frame(2, 1, 2, ConceptClass.ACTION, 0, role2=1))), "!113"
        )

    def test_action_a1_b2_n2_is_31125(self):
        self.assertEqual(
            str(classify(Frame(1, 2, 2, ConceptClass.ACTION, 0, role2=2))), "!31125"
        )

    def test_state_requires_role2_two(self):
        self.assertIsNone(classify(Frame(1, 2, 1, ConceptClass.STATE, 0, role2=3)))
        self.assertEqual(
            str(classify(Frame(1, 2, 1, ConceptClass.STATE, 0, role2=2))), "!31133"
        )

    def test_role_b_zero_uses_spare(self):
        self.assertEqual(
            str(classify(Frame(2, 0, 1, ConceptClass.ACTION, 2, role2=0))), "!0"
        )
        self.assertEqual(
            str(classify(Frame(2, 0, 1, ConceptClass.ACTION, 1, role2=0))), "!32133"
        )


class ConceptLevelTest(unittest.TestCase):
    def test_four_positions(self):
        self.assertEqual(concept_level(0x30, "single"), 0x33)
        self.assertEqual(concept_level(0x30, "first"), 0x31)
        self.assertEqual(concept_level(0x30, "last"), 0x32)
        self.assertEqual(concept_level(0x30, "middle"), 0x34)

    def test_all_bases_share_rule(self):
        for base in (0x10, 0x20, 0x30, 0x60, 0x70):
            self.assertEqual(concept_level(base, "first"), base + 1)
            self.assertEqual(concept_level(base, "last"), base + 2)
            self.assertEqual(concept_level(base, "single"), base + 3)
            self.assertEqual(concept_level(base, "middle"), base + 4)

    def test_bad_position_raises(self):
        with self.assertRaises(ValueError):
            concept_level(0x30, "nonsense")


@unittest.skipUnless(LEGACY_DLL.exists(), "legacy tree not available")
class NativeEquivalenceTest(unittest.TestCase):
    """用 Unicorn 跑原 DLL，逐条比对。

    这是本模块最有价值的一条测试：它把"我觉得逻辑对"变成"逻辑确实等价"。
    """

    @classmethod
    def setUpClass(cls):
        try:
            import unicorn  # noqa: F401
        except ImportError:  # pragma: no cover
            raise unittest.SkipTest("unicorn not installed")
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "verify_hnc", ROOT / "tools" / "verify_hnc.py"
        )
        cls.v = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.v)
        cls.uc, cls.base = cls.v.build_image()

    def test_matches_native_binary(self):
        import itertools

        mismatches = []
        checked = 0
        for a, b, n, cc, spare in itertools.product(
            range(0, 7), range(0, 4), range(0, 5), (0x30, 0x40), (0, 1, 2, 3)
        ):
            ret, native = self.v.run_case(self.uc, self.base, a, b, n, cc, spare, cc)
            if ret is None:
                continue
            checked += 1
            py = classify(Frame(a, b, n, cc, spare, role2=b))
            py_code = str(py) if py else None
            native_code = None if ret == 0 else native
            if py_code != native_code:
                mismatches.append(
                    f"a={a} b={b} n={n} cc={hex(cc)} spare={spare}: "
                    f"native={native_code!r} python={py_code!r}"
                )
        self.assertGreater(checked, 500, "验证用例太少，等于没验证")
        self.assertEqual(mismatches, [], "\n".join(mismatches[:20]))


if __name__ == "__main__":
    unittest.main()
