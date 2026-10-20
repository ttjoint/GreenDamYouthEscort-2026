# -*- coding: utf-8 -*-
"""`Handler.dll` 的白名单表加载链。

## 为什么单独立一个测试

`Handler.dll` 里有两张"标题白名单"表，出厂包里一个都不存在。
如果不把这件事钉住，复刻时很容易以为"原厂会按信任标题放行"，
从而凭空造出一套原厂根本没有生效的行为。

锁三件事：

1. 路径确实是 `<Windows>\\hnclib\\`，不是 `System32\\`；
2. 先读 `uFWORD.dat`，失败才退到 `TrustUrl.Dat`；
3. 出厂包里 `hnclib\\` 只有 `FalunWord.lib`，所以这条链必然读空。

依据见 `docs/legacy-control-files.md` §2.1 / §2.2。
"""

from __future__ import annotations

import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TREE = ROOT.parent / "LH-installed-tree"
WIN = TREE / "Windows"


def _handler() -> bytes:
    return (WIN / "Handler.dll").read_bytes()


def _str_at(d: bytes, off: int, n: int = 64) -> str:
    raw = d[off : off + n]
    return raw.split(b"\x00")[0].decode("ascii", "replace")


class WordlistPathStringsTest(unittest.TestCase):
    """两条路径串在 `.data` 里的位置与内容。"""

    def test_ufword_path_string(self):
        """`0x3101551c` 是 `\\hnclib\\uFWORD.dat`。"""
        self.assertEqual(_str_at(_handler(), 0x1551C), "\\hnclib\\uFWORD.dat")

    def test_trusturl_path_string(self):
        """`0x31015500` 是 `\\hnclib\\TrustUrl.Dat`。"""
        self.assertEqual(_str_at(_handler(), 0x15500), "\\hnclib\\TrustUrl.Dat")

    def test_directory_is_hnclib_not_system32(self):
        """两条串都指向 `hnclib\\`，不是 `System32\\`。"""
        d = _handler()
        for off in (0x1551C, 0x15500):
            with self.subTest(off=hex(off)):
                s = _str_at(d, off)
                self.assertTrue(s.startswith("\\hnclib\\"), s)
                self.assertNotIn("system32", s.lower())


class LoaderReadsUfwordFirstTest(unittest.TestCase):
    """`0x310075d0` 的读取顺序与失败回退。"""

    def test_loader_calls_getwindowsdirectory(self):
        """路径基址来自 `GetWindowsDirectoryA`。"""
        d = _handler()
        body = d[0x75FC:0x760A]
        self.assertIn((0x310120B0).to_bytes(4, "little"), body)

    def test_loader_opens_with_rb(self):
        """打开模式串是 `rb`。"""
        d = _handler()
        self.assertEqual(_str_at(d, 0x151E8, 3), "rb")

    def test_first_attempt_is_ufword(self):
        """第一处 `strcat` 目标是 `\\hnclib\\uFWORD.dat`。"""
        d = _handler()
        body = d[0x760A:0x7648]
        self.assertIn((0x3101551C).to_bytes(4, "little"), body)

    def test_fallback_branch_is_trusturl(self):
        """`fopen` 返回空之后跳到 `0x310077c4`，那里拼 `TrustUrl.Dat`。"""
        d = _handler()
        # 0x31007658 je 0x310077c4
        self.assertEqual(d[0x7658:0x765A], bytes([0x0F, 0x84]))
        body = d[0x77C4:0x77E8]
        self.assertIn((0x31015500).to_bytes(4, "little"), body)

    def test_line_caps_differ(self):
        """`uFWORD.dat` 上限 400 行，`TrustUrl.Dat` 上限 700 行。"""
        d = _handler()
        # cmp edi,0x190 / cmp esi,0x2bc
        self.assertIn(bytes([0x81, 0xFF, 0x90, 0x01, 0x00, 0x00]), d[0x76A2:0x76AC])
        self.assertIn(bytes([0x81, 0xFE, 0xBC, 0x02, 0x00, 0x00]), d[0x786D:0x7877])

    def test_two_lists_land_in_separate_fields(self):
        """`uFWORD` 写 `[+4]`，`TrustUrl` 写 `[+0xc]`。"""
        d = _handler()
        self.assertIn(bytes([0x89, 0x45, 0x04]), d[0x76D4:0x76D9])  # mov [ebp+4],eax
        self.assertIn(bytes([0x89, 0x45, 0x0C]), d[0x789F:0x78A4])  # mov [ebp+0xc],eax


class TrimHelpersTest(unittest.TestCase):
    """两个 trim 辅助函数的存在与方向。"""

    def test_trim_left_moves_tail_forward(self):
        """`0x31007470` 从左剥字符：`[edx] == cl` 就前进。"""
        d = _handler()
        body = d[0x747B:0x7495]
        self.assertIn(bytes([0x8A, 0x02]), body)  # mov al,[edx]
        self.assertIn(bytes([0x3A, 0xC1]), body)  # cmp al,cl
        # rep movsd 把尾巴搬到前面
        self.assertIn(bytes([0xA5]), d[0x74A6:0x74B0])

    def test_trim_right_rewrites_nul(self):
        """`0x310074c0` 从右剥字符：往中间写 0。"""
        d = _handler()
        body = d[0x74E7:0x74EE]
        self.assertIn(bytes([0xC6, 0x44]), body)  # mov byte [..],0

    def test_trim_char_is_space(self):
        """两处调用都传 `0x20`，即空格。"""
        d = _handler()
        self.assertEqual(d[0x7708:0x770A], bytes([0x6A, 0x20]))
        self.assertEqual(d[0x7714:0x7716], bytes([0x6A, 0x20]))


class WhitelistMatchTest(unittest.TestCase):
    """`0x31007c00` 的匹配方式。"""

    def test_both_lists_use_the_same_comparator(self):
        """两个列表共用 `0x310082f0`，它是 `strstr`（子串匹配）。"""
        d = _handler()
        body = d[0x7C1C:0x7C90]
        self.assertEqual(body.count(bytes([0xE8])), 2)
        # 两处 call 0x310082f0
        hits = 0
        i = 0
        while True:
            i = body.find(b"\xe8", i)
            if i < 0:
                break
            rel = struct.unpack("<i", body[i + 1 : i + 5])[0]
            if (0x31007C1C + i + 5 + rel) & 0xFFFFFFFF == 0x310082F0:
                hits += 1
            i += 1
        self.assertEqual(hits, 2)

    def test_empty_input_returns_zero_early(self):
        """空串直接返回 0，不去碰两个列表。"""
        d = _handler()
        body = d[0x7C07:0x7C1C]
        self.assertEqual(body[:2], bytes([0x85, 0xED]))  # test ebp,ebp
        self.assertIn(bytes([0x74]), body)  # je


class TablesAreAbsentOnDiskTest(unittest.TestCase):
    """出厂包里两张表都不存在。"""

    def test_hnclib_has_only_falunword(self):
        """`hnclib\\` 目录只有 `FalunWord.lib`。"""
        hnclib = WIN / "hnclib"
        if not hnclib.is_dir():
            self.skipTest("安装树不在本机")
        names = sorted(p.name.lower() for p in hnclib.iterdir() if p.is_file())
        self.assertEqual(names, ["falunword.lib"])

    def test_ufword_absent(self):
        """`uFWORD.dat` 在整棵树里都找不到。"""
        if not TREE.is_dir():
            self.skipTest("安装树不在本机")
        found = [p for p in TREE.rglob("*") if p.is_file() and p.name.lower() == "ufword.dat"]
        self.assertEqual(found, [])

    def test_trusturl_exists_but_only_under_system32(self):
        """`TrustUrl.dat` 只在 `System32\\`，不在 `hnclib\\`。

        `Handler.dll` 去 `hnclib\\` 找，所以它读不到这份。
        """
        if not TREE.is_dir():
            self.skipTest("安装树不在本机")
        found = [p for p in TREE.rglob("*") if p.is_file() and p.name.lower() == "trusturl.dat"]
        self.assertTrue(found)
        self.assertTrue(all("System32" in str(p) for p in found), [str(p) for p in found])


class SurfgdLoadsFromSystem32Test(unittest.TestCase):
    """`Surfgd.dll` 用的是另一套目录，不是同一批文件。"""

    def test_surfgd_table_names_are_contiguous(self):
        """六个名字在 `.data` 里相邻排列，说明是一张表扫出来的。"""
        d = (WIN / "Surfgd.dll").read_bytes()
        for off, name in (
            (0x17838, "TrustUrl.Dat"),
            (0x17848, "uFWORD.dat"),
            (0x17854, "uTUrl.Dat"),
            (0x17860, "uFUrl.Dat"),
            (0x1786C, "filtport.Dat"),
            (0x1787C, "filtpros.Dat"),
        ):
            with self.subTest(name=name):
                self.assertEqual(_str_at(d, off, 20), name)

    def test_surfgd_builds_path_from_system_directory(self):
        """`0x10007f77` 用 `GetSystemDirectoryA` 拼路径。"""
        d = (WIN / "Surfgd.dll").read_bytes()
        body = d[0x7F80:0x7F8F]
        # call dword ptr [0x10220064]
        self.assertIn((0x10220064).to_bytes(4, "little"), body)

    def test_surfgd_separator_is_backslash(self):
        """分隔符串是单个反斜杠。"""
        d = (WIN / "Surfgd.dll").read_bytes()
        self.assertEqual(_str_at(d, 0x177F4, 4), "\\")


if __name__ == "__main__":
    unittest.main()
