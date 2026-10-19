# -*- coding: utf-8 -*-
"""原厂「控制文件」与 `.urf` 加载点的行为测试。

这三组测试锁住本轮从反汇编里拿到的硬事实，防止以后被"看起来
解码成功"的假象带偏：

1. `sys.dat` / `sysEx.dat` / `xconfigs.dat` **不是**混淆文本，
   它们是裸的 little-endian `uint32` 数组。用词表 codec 硬解会
   得到一整串 `<`，这正说明"能解码"不等于"是混淆文本"。
2. `filtport.dat` 是**明文 ASCII**，不走 codec，格式是
   `名字/端口/协议`。
3. `XNet2.exe` 和 `gn.exe` 里都有一个 `%s\\%d.urf` 的 push 点，
   后面跟同一个三参数顺序 `push 1 / push 名字 / call reg`，
   这就是 `.urf` 加载器的调用约定。

测试只断言结构与计数，不回显任何条目的原文。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import audit_control_files as A  # noqa: E402
import disasm_urf_loader as D  # noqa: E402

LEGACY = ROOT.parent / "LH-installed-tree"
SYSDIR = LEGACY / "Windows" / "System32"
HAVE_TREE = SYSDIR.is_dir()


@unittest.skipUnless(HAVE_TREE, "原厂安装树不在")
class ControlStructIsNotTextTest(unittest.TestCase):
    """`sys*.dat` 是 uint32 数组，不是混淆文本。"""

    def test_sys_dat_is_67_dwords(self):
        f = A.audit_dword("sys.dat", (SYSDIR / "sys.dat").read_bytes())
        self.assertEqual(f.size, 268)
        self.assertEqual(f.word_count, 67)
        self.assertLessEqual(f.max_value, 14)

    def test_codec_on_sys_dat_produces_pure_garbage(self):
        """用词表 codec 解控制结构，只会得到占位符。

        这是「解码不报错」不能当证据的直接反例。
        """
        f = A.audit_dword("sys.dat", (SYSDIR / "sys.dat").read_bytes())
        self.assertTrue(f.codec_roundtrip_is_ascii_garbage)

    def test_xconfigs_is_a_duplicate_of_sys_dat(self):
        a = (SYSDIR / "sys.dat").read_bytes()
        b = (SYSDIR / "xconfigs.dat").read_bytes()
        self.assertEqual(a, b)

    def test_sysex_is_eight_dwords_with_a_version_word(self):
        f = A.audit_dword("sysEx.dat", (SYSDIR / "sysEx.dat").read_bytes())
        self.assertEqual(f.size, 32)
        self.assertEqual(f.word_count, 8)
        # first dword is 0x0004015E — a plausible version triple
        self.assertEqual(f.max_value, 0x4015E)


@unittest.skipUnless(HAVE_TREE, "原厂安装树不在")
class PlainPolicyListTest(unittest.TestCase):
    """`filtport.dat` 是明文，格式 `名字/端口/协议`。"""

    def test_is_plain_ascii(self):
        f = A.audit_plain("filtport.dat", (SYSDIR / "filtport.dat").read_bytes())
        self.assertTrue(f.ascii_only)
        self.assertFalse(f.trailing_crlf and f.line_count == 0)
        self.assertEqual(f.size, 33)

    def test_two_entries_three_fields_each(self):
        f = A.audit_plain("filtport.dat", (SYSDIR / "filtport.dat").read_bytes())
        self.assertEqual(f.line_count, 2)
        self.assertEqual(f.entry_fields, 3)
        self.assertEqual(f.entries_with_numeric_tail, 2)
        self.assertEqual(f.entries_with_named_proto, 2)


@unittest.skipUnless(HAVE_TREE, "原厂安装树不在")
class HiddenCodecListsTest(unittest.TestCase):
    """其余 `.dat` 确实是 64 符号混淆，且行数可复核。"""

    EXPECTED = {
        "wfile.dat": 5,
        "TrustUrl.dat": 1177,
        "adwapp.dat": 6856,
        "adwfil.dat": 2003,
        "iawfil.dat": 113,
        "lgwfil.dat": 54,
        "vgamfil.dat": 117,
    }

    def test_every_list_is_obfuscated_and_has_known_line_count(self):
        for name, lines in self.EXPECTED.items():
            with self.subTest(name=name):
                f = A.audit_codec(name, (SYSDIR / name).read_bytes())
                self.assertTrue(f.obfuscated, name)
                self.assertEqual(f.line_count, lines, name)

    def test_declared_but_missing_lists_are_reported(self):
        report = A.run(LEGACY)
        missing = set(report["missing_declared"])
        # Surfgd.dll 里出现、但包里根本没带的三张表
        self.assertIn("uFWORD.dat", missing)
        self.assertTrue(any(m.lower() == "filtpros.dat" for m in missing))


@unittest.skipUnless((SYSDIR / "XNet2.exe").is_file(), "XNet2.exe 不在")
class UrfLoaderCallSiteTest(unittest.TestCase):
    """两个宿主模块里 `.urf` 加载器的调用约定必须一致。"""

    @classmethod
    def setUpClass(cls):
        cls.net = D.scan(SYSDIR / "XNet2.exe")
        cls.gn = D.scan(SYSDIR / "gn.exe")

    def test_each_host_pushes_the_format_string_exactly_once(self):
        self.assertEqual(len(self.net["urf_format_sites"]), 1)
        self.assertEqual(len(self.gn["urf_format_sites"]), 1)

    def test_loader_name_string_is_identical_across_hosts(self):
        """两个模块 push 的 32 字母名字串哈希相同 —— 同一个密钥名。"""
        n = D.loader_call_prologue(self.net)
        g = D.loader_call_prologue(self.gn)
        self.assertIn("name", n)
        self.assertIn("name", g)
        self.assertEqual(n["name"]["sha1_8"], g["name"]["sha1_8"])
        # 第三个参数固定为 1，两个宿主一致。
        self.assertEqual(n["flag"]["value"], 1)
        self.assertEqual(g["flag"]["value"], 1)

    def test_call_is_indirect_through_a_struct_field(self):
        """加载器不是直接 call，而是 `call eax`（来自 [host+0xc8]）。

        这条断言解释了为什么早期「搜不到 call 引用」并不等于「没人调用」。
        """
        rows = self.net["windows"][0]["rows"]
        mnemonics = [(r["mnemonic"], r["ops"]) for r in rows]
        self.assertIn(("call", "eax"), mnemonics)
        self.assertTrue(any("c8" in ops for _, ops in mnemonics))

    def test_index_is_clamped_before_use(self):
        """两个宿主都在拼路径前把索引钳到 1..N。"""
        for report in (self.net, self.gn):
            rows = report["windows"][0]["rows"]
            mnems = [r["mnemonic"] for r in rows]
            self.assertIn("cmp", mnems)
            self.assertIn("jl", mnems)
            self.assertIn("jle", mnems)

    def test_urf_magic_is_not_embedded_in_either_host(self):
        """`.urf` 的 magic 不在宿主里 —— 解码逻辑不在宿主。"""
        self.assertFalse(self.net["urf_magic_present"])
        self.assertFalse(self.gn["urf_magic_present"])


if __name__ == "__main__":
    unittest.main()
