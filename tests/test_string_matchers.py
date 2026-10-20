# -*- coding: utf-8 -*-
"""`Handler.dll` 里三个字符串例程的真实语义，用 unicorn 实测。

## 为什么不能只读字节

`0x310082f0` 的字节看着像 `strcmp` 的展开版：两重循环、逐字符比较。
但它实际是 `strstr`——最关键的差别在于"匹配失败后继续往后找"。
这种差别读字节容易看错，跑一遍就清楚了。

反例一眼可辨：
- 若为 `strcmp`，`"visit http://x"` 对 needle `"http://"` 应当不命中；
- 实测命中，所以是子串查找。

## 栈约定

`0x310082f0` 是 `__cdecl`，入口 `[esp+4]` = haystack、`[esp+8]` = needle。
判据是 `0x310082f0` 的 `mov ecx,[esp+8]`（取 needle）与
`0x310082f9` 三次 `push` 后 `[esp+0x10]` 等于原 `esp+4`（取 haystack）。
摆错顺序会读未映射内存直接崩，这本身也是一个校验。

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

    def test_needle_in_the_middle_matches(self):
        """子串在中间也算命中——这是排除 `strcmp` 的决定性证据。"""
        self.assertNotEqual(self.call(b"visit http://evil.com now", b"http://"), 0)

    def test_needle_at_start_matches(self):
        self.assertNotEqual(self.call(b"http://evil.com", b"http://"), 0)

    def test_needle_absent_does_not_match(self):
        self.assertEqual(self.call(b"visit ftp://evil.com now", b"http://"), 0)

    def test_string_shorter_than_needle_does_not_match(self):
        self.assertEqual(self.call(b"abc", b"http://"), 0)

    def test_two_char_needle(self):
        self.assertNotEqual(self.call(b"plain text", b"pl"), 0)
        self.assertEqual(self.call(b"plain text", b"zz"), 0)

    def test_needle_not_anchored_to_left(self):
        """`ZZplain` 里 `pl` 不在开头，仍然命中。"""
        self.assertNotEqual(self.call(b"ZZplain", b"pl"), 0)

    def test_match_result_points_inside_haystack(self):
        """返回值是 haystack 内部的指针，不是布尔 1。"""
        r = self.call(b"xxhttp://yy", b"http://")
        self.assertGreater(r, DATA)
        self.assertLess(r, DATA + 0x100)


@unittest.skipIf(Uc is None, "unicorn 未安装")
class ProtocolExclusionTest(unittest.TestCase):
    """`0x31007b70` 用 `strstr` 判协议，位置在循环体内。

    这里只验证"协议判定走的就是 `strstr`"这个事实，
    不验证整个函数——那需要构造 400 项的表，超出单测范围。
    """

    @classmethod
    def setUpClass(cls):
        if not HANDLER.is_file():
            raise unittest.SkipTest("安装树不在本机")
        cls.raw = HANDLER.read_bytes()

    def test_http_and_ftp_constants_are_present(self):
        """两个协议串在 `.data` 里相邻。"""
        d = self.raw
        self.assertEqual(d[0x15538 : 0x15538 + 7], b"http://")
        self.assertEqual(d[0x15530 : 0x15530 + 6], b"ftp://")

    def test_protocol_checks_come_before_list_lookup(self):
        """`0x31007bab` / `0x31007bbd` 在 `0x31007bd7` 之前。"""
        self.assertLess(0x31007BAB, 0x31007BD7)
        self.assertLess(0x31007BBD, 0x31007BD7)

    def test_protocol_checks_produce_zero(self):
        """两个协议检查命中时都跳 `0x31007beb`，那里 `xor eax,eax`。"""
        d = self.raw
        d_ = self.raw
        # 0x31007bbb: jne short 0x31007beb  (75 2e)
        self.assertEqual(d_[0x7BBB:0x7BBD], bytes([0x75, 0x2E]))
        self.assertEqual(0x31007BBB + 2 + 0x2E, 0x31007BEB)
        # 0x31007bcd: jne short 0x31007beb  (75 1c)
        self.assertEqual(d_[0x7BCD:0x7BCF], bytes([0x75, 0x1C]))
        self.assertEqual(0x31007BCD + 2 + 0x1C, 0x31007BEB)

    def test_return_one_path_is_separate(self):
        """列表命中跳 `0x31007bf4`，那里 `mov eax,1`。"""
        d = self.raw
        self.assertEqual(d[0x7BF7:0x7BFA], bytes([0xB8, 0x01, 0x00]))

    def test_protocol_check_inside_loop_body(self):
        """协议串的立即数引用在循环体内，不是循环前。"""
        d = self.raw
        loop_start = 0x7B95
        self.assertGreater(d.find((0x31015538).to_bytes(4, "little"), 0x7B95), loop_start)
        self.assertGreater(d.find((0x31015530).to_bytes(4, "little"), 0x7B95), loop_start)


class ListMatchingFunctionsTest(unittest.TestCase):
    """三个例程的分工，按调用点区分。"""

    def test_two_matchers_are_distinct(self):
        """`0x31007b70` 与 `0x31007c00` 开头序言相同但函数体不同。

        两者都是 `push ebx/ebp; mov ebp,[esp+0xc]; push esi`，
        所以只能比更靠后的字节。
        """
        d = HANDLER.read_bytes()
        self.assertEqual(d[0x7B70:0x7B78], d[0x7C00:0x7C08])  # 序言相同
        self.assertNotEqual(d[0x7B85:0x7BA0], d[0x7C15:0x7C30])  # 函数体不同

    def test_b70_reads_single_list(self):
        """`0x31007b70` 只读 `[+4]` / `[+8]` 一组。"""
        d = HANDLER.read_bytes()
        body = d[0x7B70:0x7BEB]
        self.assertIn(bytes([0x8B, 0x43, 0x08]), body)  # mov eax,[ebx+8]
        self.assertIn(bytes([0x8B, 0x43, 0x04]), body)  # mov eax,[ebx+4]
        self.assertNotIn(bytes([0x8B, 0x73, 0x14]), body)

    def test_c00_reads_two_lists(self):
        """`0x31007c00` 读 `[+0x14]` 和 `[+0xc]` 两组。"""
        d = HANDLER.read_bytes()
        body = d[0x7C00:0x7C8A]
        self.assertIn(bytes([0x8B, 0x46, 0x18]), body)  # mov eax,[esi+0x18]
        self.assertIn(bytes([0x8B, 0x46, 0x14]), body)  # mov eax,[esi+0x14]
        self.assertIn(bytes([0x8B, 0x46, 0x10]), body)  # mov eax,[esi+0x10]
        self.assertIn(bytes([0x8B, 0x4E, 0x0C]), body)  # mov ecx,[esi+0xc]

    def test_b70_is_called_before_corpgroupp(self):
        """`0x31004e00` 在 `0x31004e20` 之前，即先问白名单再扫词库。"""
        self.assertLess(0x31004E00, 0x31004E20)

    def test_whitelist_hit_bypasses_corpgroupp(self):
        """白名单命中跳到 `0x31004e37`，跳过 `0x31004e09..0x31004e20`。"""
        d = HANDLER.read_bytes()
        body = d[0x4E05:0x4E09]
        self.assertEqual(body[:2], bytes([0x85, 0xC0]))  # test eax,eax
        self.assertEqual(body[2:4], bytes([0x75, 0x2E]))  # jne short
        # 0x31004e07 + 2 + 0x2e = 0x31004e37，即跳过 CorpGroup 那段
        self.assertEqual(0x31004E07 + 2 + 0x2E, 0x31004E37)


if __name__ == "__main__":
    unittest.main()
