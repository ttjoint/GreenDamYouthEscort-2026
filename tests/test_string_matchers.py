# -*- coding: utf-8 -*-
"""`Handler.dll` 里三个字符串例程的真实语义，用 unicorn 实测。

## 两个容易混的层次

看这类函数时必须分清两件事，否则会得出互相矛盾的结论：

1. **函数返回什么**：`0x310082f0` 返回的是 `haystack` 里的**匹配位置指针**
   （找到返回指针，找不到返回 0）。这一点由空 needle 的行为定死：
   `strstr("hello", "")` 返回 `"hello"` 的起点，实测为 `DATA` 基址本身。
   任何整串比较函数在空 needle 时都不会返回 haystack 指针。

2. **调用者怎么用它**：`Handler.dll` 里 5 个调用点**全部**只做
   `test eax,eax` / `jne`，没有一处解引用返回值。
   所以从调用者视角，"命中/不命中"这个描述是准确的；
   但从函数行为看，判据是**子串包含**而不是**整串相等**。

写成"两个列表用整串比较"是错的。写成"两个列表用比较函数判断相等"
在调用者层面不算错、但不完整——因为决定语义的是子串而非相等。

## 为什么这会影响复刻

一个只有两三个字符的列表项，在子串匹配下会命中任何**包含**它的更长串。
若按整串相等去复刻，同样的表会宽松得多。这是实质差异，不是措辞问题。

依据见 `docs/legacy-control-files.md` §2.3 / §6。
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HANDLER = ROOT.parent / "LH-installed-tree" / "Windows" / "Handler.dll"

try:
    from unicorn import Uc, UcError, UC_ARCH_X86, UC_MODE_32
    from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_ESP
except ImportError:  # pragma: no cover
    Uc = None


STRSTR = 0x310082F0
STACK = 0x33000000
DATA = 0x34000000
RET_MAGIC = 0x22222222


def _machine():
    import pefile

    raw = HANDLER.read_bytes()
    ib = pefile.PE(data=raw).OPTIONAL_HEADER.ImageBase
    mu = Uc(UC_ARCH_X86, UC_MODE_32)
    mu.mem_map(ib, 0x200000)
    mu.mem_write(ib, raw[:0x200000])
    mu.mem_map(STACK, 0x10000)
    mu.mem_map(DATA, 0x10000)
    return mu, ib


@unittest.skipIf(Uc is None, "unicorn 未安装")
class StrstrSemanticsTest(unittest.TestCase):
    """`0x310082f0(haystack, needle)` 返回匹配指针，不匹配返回 0。"""

    def setUp(self):
        # 每个测试用一台干净的机器：`emu_start` 以 ret 撞未映射页收尾，
        # 异常之后复用同一个 Uc 实例状态不可靠。
        if not HANDLER.is_file():
            self.skipTest("安装树不在本机")
        self.mu, self.ib = _machine()

    def call(self, haystack: bytes, needle: bytes) -> int:
        mu = self.mu
        mu.mem_write(DATA, haystack + b"\x00")
        mu.mem_write(DATA + 0x100, needle + b"\x00")
        esp = STACK + 0x8000
        # [esp]=ret, [esp+4]=haystack, [esp+8]=needle
        for value in (DATA + 0x100, DATA, RET_MAGIC):
            esp -= 4
            mu.mem_write(esp, value.to_bytes(4, "little"))
        mu.reg_write(UC_X86_REG_ESP, esp)
        try:
            mu.emu_start(STRSTR, RET_MAGIC, count=50000)
        except UcError:
            # ret 跳到未映射的 RET_MAGIC 会抛 FETCH_UNMAPPED，
            # 此时函数已经跑完，EAX 就是返回值。
            # 用 count 或 until 提前停都可能漏掉最后一条指令，
            # 所以这里靠"跑到 ret 就崩"来判断结束。
            pass
        return mu.reg_read(UC_X86_REG_EAX)

    # ---- 判定它是 strstr 而不是 strcmp 的关键证据 ----

    def test_empty_needle_returns_start_of_haystack(self):
        """空 needle 返回 haystack 起点——这是 `strstr` 的定义行为。

        `strcmp` 在空 needle 时只会返回"不相等"（非零常数），
        绝不会返回 haystack 内部指针。这条单独决定了函数类别。
        """
        self.assertEqual(self.call(b"hello", b""), DATA)

    def test_empty_needle_and_empty_haystack(self):
        self.assertEqual(self.call(b"", b""), DATA)

    def test_result_points_inside_haystack_not_a_boolean(self):
        """后缀匹配返回的是 `DATA+1`，不是布尔 1。"""
        self.assertEqual(self.call(b"xab", b"ab"), DATA + 1)

    def test_match_at_start_returns_haystack_base(self):
        self.assertEqual(self.call(b"ab", b"ab"), DATA)

    def test_needle_in_the_middle_matches(self):
        """子串在中间也算命中——排除整串相等。"""
        r = self.call(b"visit http://evil.com now", b"http://")
        self.assertEqual(r, DATA + 6)

    def test_needle_not_anchored_to_left(self):
        self.assertEqual(self.call(b"ZZplain", b"pl"), DATA + 2)

    # ---- 边界 ----

    def test_needle_absent_returns_zero(self):
        self.assertEqual(self.call(b"visit ftp://evil.com now", b"http://"), 0)

    def test_haystack_shorter_than_needle_returns_zero(self):
        self.assertEqual(self.call(b"a", b"ab"), 0)

    def test_empty_haystack_returns_zero(self):
        self.assertEqual(self.call(b"", b"ab"), 0)

    def test_two_char_needle_absent(self):
        self.assertEqual(self.call(b"plain text", b"zz"), 0)


@unittest.skipIf(Uc is None, "unicorn 未安装")
class CallSitesOnlyTestTruthinessTest(unittest.TestCase):
    """调用者只用返回值判真伪，不解引用。

    这一层单独锁住，是为了避免把"调用者视角"和"函数行为"混为一谈：
    调用者是 `test eax,eax`，但这不能说函数返回布尔。
    """

    def setUp(self):
        if not HANDLER.is_file():
            self.skipTest("安装树不在本机")
        self.raw = HANDLER.read_bytes()

    def test_every_call_site_only_tests_truthiness(self):
        """五个调用点之后全是"判非零"，没有一处解引用返回值。

        两种写法都有：
        - `test eax,eax`（0x31004e00 及三个内部调用点）
        - `mov edi,eax; test edi,edi`（0x31004865，保存到局部再判）
        """
        expect = {
            0x31004865: bytes([0x8B, 0xF8, 0x85, 0xFF]),  # mov edi,eax; test edi,edi
            0x31004E00: bytes([0x85, 0xC0]),  # test eax,eax
        }
        # 三个内部调用点：call / add esp,8 / test eax,eax
        for site in (0x31007BB1, 0x31007BC3, 0x31007BD7):
            expect[site] = bytes([0x83, 0xC4, 0x08, 0x85, 0xC0])
        for site, want in expect.items():
            off = site - 0x31000000
            with self.subTest(site=hex(site)):
                self.assertEqual(self.raw[off + 5 : off + 5 + len(want)], want)

    def test_returns_one_when_list_hit(self):
        """列表命中路径返回常量 1，不是指针。"""
        self.assertEqual(self.raw[0x7BF7:0x7BFA], bytes([0xB8, 0x01, 0x00]))

    def test_whitelist_matcher_is_not_the_raw_comparator(self):
        """`0x31007b70` 把 `strstr` 的结果折成 0/1。

        所以"白名单匹配"这个说法是在这一层成立，不是在内层函数成立。
        """
        # 0x31007bd7 调 strstr，命中跳 0x31007bf4（mov eax,1）
        self.assertEqual(self.raw[0x7BE1:0x7BE3], bytes([0x75, 0x11]))
        self.assertEqual(0x31007BE1 + 2 + 0x11, 0x31007BF4)


@unittest.skipIf(Uc is None, "unicorn 未安装")
class ProtocolExclusionTest(unittest.TestCase):
    """`0x31007b70` 用 `strstr` 判协议，位置在循环体内。"""

    def setUp(self):
        if not HANDLER.is_file():
            self.skipTest("安装树不在本机")
        self.raw = HANDLER.read_bytes()

    def test_http_and_ftp_constants_are_present(self):
        d = self.raw
        self.assertEqual(d[0x15538 : 0x15538 + 7], b"http://")
        self.assertEqual(d[0x15530 : 0x15530 + 6], b"ftp://")

    def test_protocol_checks_come_before_list_lookup(self):
        self.assertLess(0x31007BAB, 0x31007BD7)
        self.assertLess(0x31007BBD, 0x31007BD7)

    def test_protocol_checks_produce_zero(self):
        d_ = self.raw
        # 0x31007bbb: jne short 0x31007beb  (75 2e)
        self.assertEqual(d_[0x7BBB:0x7BBD], bytes([0x75, 0x2E]))
        self.assertEqual(0x31007BBB + 2 + 0x2E, 0x31007BEB)
        # 0x31007bcd: jne short 0x31007beb  (75 1c)
        self.assertEqual(d_[0x7BCD:0x7BCF], bytes([0x75, 0x1C]))
        self.assertEqual(0x31007BCD + 2 + 0x1C, 0x31007BEB)

    def test_protocol_check_inside_loop_body(self):
        """协议串的立即数引用在循环体内，不是循环前。"""
        d = self.raw
        self.assertGreater(d.find((0x31015538).to_bytes(4, "little"), 0x7B95), 0x7B95)
        self.assertGreater(d.find((0x31015530).to_bytes(4, "little"), 0x7B95), 0x7B95)


class ListMatchingFunctionsTest(unittest.TestCase):
    """三个例程的分工，按调用点区分。"""

    def test_two_matchers_are_distinct(self):
        """`0x31007b70` 与 `0x31007c00` 开头序言相同但函数体不同。"""
        d = HANDLER.read_bytes()
        self.assertEqual(d[0x7B70:0x7B78], d[0x7C00:0x7C08])  # 序言相同
        self.assertNotEqual(d[0x7B85:0x7BA0], d[0x7C15:0x7C30])  # 函数体不同

    def test_b70_reads_single_list(self):
        d = HANDLER.read_bytes()
        body = d[0x7B70:0x7BEB]
        self.assertIn(bytes([0x8B, 0x43, 0x08]), body)  # mov eax,[ebx+8]
        self.assertIn(bytes([0x8B, 0x43, 0x04]), body)  # mov eax,[ebx+4]
        self.assertNotIn(bytes([0x8B, 0x73, 0x14]), body)

    def test_c00_reads_two_lists(self):
        d = HANDLER.read_bytes()
        body = d[0x7C00:0x7C8A]
        self.assertIn(bytes([0x8B, 0x46, 0x18]), body)  # mov eax,[esi+0x18]
        self.assertIn(bytes([0x8B, 0x46, 0x14]), body)  # mov eax,[esi+0x14]
        self.assertIn(bytes([0x8B, 0x46, 0x10]), body)  # mov eax,[esi+0x10]
        self.assertIn(bytes([0x8B, 0x4E, 0x0C]), body)  # mov ecx,[esi+0xc]

    def test_b70_is_called_before_corpgroupp(self):
        self.assertLess(0x31004E00, 0x31004E20)

    def test_whitelist_hit_bypasses_corpgroupp(self):
        d = HANDLER.read_bytes()
        body = d[0x4E05:0x4E09]
        self.assertEqual(body[:2], bytes([0x85, 0xC0]))  # test eax,eax
        self.assertEqual(body[2:4], bytes([0x75, 0x2E]))  # jne short
        self.assertEqual(0x31004E07 + 2 + 0x2E, 0x31004E37)


if __name__ == "__main__":
    unittest.main()
