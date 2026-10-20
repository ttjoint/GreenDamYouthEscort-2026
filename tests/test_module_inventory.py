# -*- coding: utf-8 -*-
"""模块清单的行为测试。

这里锁的不是"哪个文件多大"，而是**能用一条断言推翻的结论**：

1. 产品线是三方拼起来的，第三方库可识别；
2. 过滤链上有哪些边，哪些函数根本没有调用者；
3. `SentenceObj.dll` 里带 `%s\\` 前缀的文件名和不带的，
   差别是决定性的——前者真加载，后者是调试字符串；
4. 有一批文件被引用但不在树上，不能当成"还没找到"。
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import audit_modules as M  # noqa: E402

LEGACY = ROOT.parent / "LH-installed-tree"


def _have_tree() -> bool:
    return (LEGACY / "Windows").is_dir()


def _read(pe, data: bytes, va: int, size: int) -> bytes:
    """按段映射把 VA 换成文件偏移；不能直接用 VA-ImageBase 当偏移。"""
    ib = pe.OPTIONAL_HEADER.ImageBase
    for sec in pe.sections:
        start = sec.VirtualAddress + ib
        if start <= va < start + sec.Misc_VirtualSize:
            off = sec.PointerToRawData + (va - start)
            return data[off : off + size]
    raise AssertionError(f"VA {va:#x} 不在任何段内")


class VendorSplitTest(unittest.TestCase):
    """三方产品线 + 第三方库。"""

    @classmethod
    def setUpClass(cls):
        cls.report = M.collect(LEGACY) if _have_tree() else None
        cls.mods = cls.report["modules"] if cls.report else {}

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_all_expected_modules_are_seen(self):
        self.assertEqual(self.report["module_count"], 33)

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_three_hnc_modules_share_a_vendor(self):
        hnc = {
            n for n, r in self.mods.items() if r.get("vendor") == "hnc-engine"
        }
        self.assertEqual(hnc, {"HASrv.dll", "Handler.dll", "InjLib32.dll"})

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_kingway_modules_are_the_lsp_stack(self):
        kw = {n for n, r in self.mods.items() if r.get("vendor") == "kingway"}
        self.assertEqual(kw, {"Surfgd.dll", "Xcv.dll", "Xtool.dll", "xcore.dll"})

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_third_party_libraries_are_labelled(self):
        for name in ("appface.dll", "cximage.dll", "kwimage.dll"):
            self.assertTrue(
                self.mods[name]["vendor"].startswith("thirdparty-"),
                f"{name} should be marked third party",
            )

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_the_672_byte_dll_is_not_a_pe(self):
        """kwselectinfopp.dll is in the tree but cannot be loaded."""
        self.assertIn("error", self.mods["kwselectinfopp.dll"])


class FilteringEdgesTest(unittest.TestCase):
    """过滤链上的边，以及不存在的边。"""

    @classmethod
    def setUpClass(cls):
        cls.report = M.collect(LEGACY) if _have_tree() else None
        cls.mods = cls.report["modules"] if cls.report else {}

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_ipgate_only_wraps_surfgd(self):
        deps = set(self.mods["IPGate.dll"]["depends_on"])
        self.assertEqual(deps, {"SurfGd.dll"})

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_dbfilter_is_an_lsp_provider(self):
        """WSPStartup is the LSP entry point; that is the whole claim."""
        self.assertIn("WSPStartup", self.mods["dbfilter.dll"]["exports"])

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_handler_imports_sentenceobj(self):
        deps = self.mods["Handler.dll"]["depends_on"]
        self.assertIn("SentenceObj.dll", deps)
        self.assertTrue(
            any("CreateHncTextGroup" in f for f in deps["SentenceObj.dll"])
        )

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_filterwords_has_no_consumer(self):
        """MPSvcDll exports FilterWords and nothing anywhere calls it."""
        self.assertIn("FilterWords", self.mods["MPSvcDll.dll"]["exports"])
        for name, rec in self.mods.items():
            if name == "MPSvcDll.dll":
                continue
            for dll, fns in rec.get("depends_on", {}).items():
                self.assertNotIn("FilterWords", fns, f"{name} imports FilterWords")
            raw = (LEGACY / rec["path"]).read_bytes()
            self.assertNotIn(b"FilterWords", raw, f"{name} mentions FilterWords")

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_net2_is_the_one_that_touches_everything(self):
        """XNet2.exe is the hub: surf, image, popup, injection."""
        deps = self.mods["XNet2.exe"]["depends_on"]
        for dll in ("SurfGd.dll", "CImage.dll", "poppo.dll"):
            self.assertIn(dll, deps)
        raw = (LEGACY / self.mods["XNet2.exe"]["path"]).read_bytes()
        self.assertIn(b"InjectLibrary", raw)


class SentenceObjLibsTest(unittest.TestCase):
    """带 `%s\\` 前缀的才真加载，裸名字是调试字符串。"""

    @classmethod
    def setUpClass(cls):
        cls.path = LEGACY / "Windows" / "SentenceObj.dll"
        cls.raw = cls.path.read_bytes() if cls.path.is_file() else b""

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_prefixed_lib_names_exist(self):
        for name in (b"%s\\FalunWord.lib", b"%s\\FMean.lib", b"%s\\FMean.tmp"):
            self.assertIn(name, self.raw)

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_bare_lib_names_are_present_but_unprefixed(self):
        bare = [
            b"Word.lib", b"JiBenJL.lib", b"TYPE.LIB", b"XU.LIB",
            b"rule.lib", b"SENTEN.LIB", b"ElTest.lib", b"EgPerc.lib",
            b"UnifyPerc.lib", b"DefIndex.lib", b"Defl0Index.lib",
            b"Defl0Data.lib", b"ActiveMode.lib", b"AttribE.lib",
        ]
        for name in bare:
            self.assertIn(name, self.raw)
            # 裸名字前面不能紧跟 `%s\`，否则就成了加载路径
            self.assertNotIn(b"%s\\" + name, self.raw)

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_only_falunword_lib_ships(self):
        libs = sorted(p.name.lower() for p in (LEGACY / "Windows" / "HNCLIB").iterdir())
        self.assertEqual(libs, ["falunword.lib"])

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_prefixed_names_are_referenced_from_code(self):
        """`%s\\FalunWord.lib` must be pushed from .text, not just sit in .data."""
        import struct

        va = 0x10000000 + self.raw.index(b"%s\\FalunWord.lib")
        pat = struct.pack("<I", va)
        self.assertIn(pat, self.raw[0x1000:0x44000])


class DeclaredButAbsentTest(unittest.TestCase):
    """被引用却不在树上的文件，必须明确列出来。"""

    @classmethod
    def setUpClass(cls):
        cls.report = M.collect(LEGACY) if _have_tree() else None
        cls.declared = cls.report["declared_data_files"] if cls.report else {}

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_present_files_are_found(self):
        for name in ("FalunWord.lib", "TrustUrl.Dat", "wfile.dat", "filtport.Dat"):
            key = next((k for k in self.declared if k.lower() == name.lower()), None)
            self.assertIsNotNone(key, f"{name} not declared")
            self.assertTrue(self.declared[key], f"{name} reported absent")

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_xword_tables_never_shipped(self):
        for name in ("XWORDH.DAT", "XWORDM.DAT", "XWORDL.DAT"):
            self.assertIn(name, self.declared)
            self.assertEqual(self.declared[name], [])

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_format_strings_are_not_mistaken_for_filenames(self):
        """%02d%02d%02d.dat is built at runtime; it is not a missing file."""
        for name in self.declared:
            self.assertNotIn("%", name)
        self.assertFalse(any(n.endswith("02d.dat") for n in self.declared))


if __name__ == "__main__":
    unittest.main()


class MpSvcServerTest(unittest.TestCase):
    """`MPSvcDll.dll!FilterWords` 在服务端落到的那个实现。

    这一层断言的都是"能被字节推翻"的事实，不是推测。
    """

    @classmethod
    def setUpClass(cls):
        import pefile

        cls.pe_dll = pefile.PE(str(LEGACY / "Windows" / "MPSvcDll.dll"), fast_load=True)
        cls.pe_exe = pefile.PE(str(LEGACY / "Windows" / "MPSvcC.exe"), fast_load=True)
        cls.dll = (LEGACY / "Windows" / "MPSvcDll.dll").read_bytes()
        cls.exe = (LEGACY / "Windows" / "MPSvcC.exe").read_bytes()

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_forwarder_is_cdecl_not_stdcall(self):
        """`FilterWords` 以裸 `ret` 收尾，参数由调用者清。"""
        self.assertEqual(self.dll[0x14A0], 0xC3)

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_forwarder_calls_vtable_offset_0x34(self):
        """`call [edx+0x34]` 的完整编码在位。"""
        body = self.dll[0x1440:0x14A1]
        self.assertIn(bytes([0xFF, 0x52, 0x34]), body)

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_vtable_0x40c278_entry_0x34_is_0x403a30(self):
        """`FilterWords` 服务的虚表是 `0x40c278`，第 13 项落到 `0x403a30`。"""
        import struct

        pe = self.pe_exe
        ib = pe.OPTIONAL_HEADER.ImageBase
        for sec in pe.sections:
            if sec.VirtualAddress + ib <= 0x40C278 < sec.VirtualAddress + ib + sec.Misc_VirtualSize:
                off = sec.PointerToRawData + (0x40C278 - sec.VirtualAddress - ib)
                break
        else:
            self.skipTest("段映射失败")
        slots = struct.unpack("<16I", self.exe[off : off + 64])
        self.assertEqual(slots[13], 0x403A30)
        # 前三位是 COM 三件套的标准顺序：AddRef / QueryInterface / Release
        self.assertEqual(slots[1], 0x403D50)  # QueryInterface
        self.assertEqual(slots[2], 0x403D30)  # Release

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_the_other_vtable_has_a_different_0x34(self):
        """`0x40c260` 是另一个对象，`+0x34` 不是 `0x403a30`。

        两个虚表顺序不同，混用会让"FilterWords 做什么"整个错位。
        """
        import struct

        pe = self.pe_exe
        ib = pe.OPTIONAL_HEADER.ImageBase
        for sec in pe.sections:
            if sec.VirtualAddress + ib <= 0x40C260 < sec.VirtualAddress + ib + sec.Misc_VirtualSize:
                off = sec.PointerToRawData + (0x40C260 - sec.VirtualAddress - ib)
                break
        else:
            self.skipTest("段映射失败")
        slots = struct.unpack("<16I", self.exe[off : off + 64])
        self.assertEqual(slots[13], 0x403780)
        self.assertNotEqual(slots[13], 0x403A30)

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_server_method_always_returns_s_ok(self):
        """`0x403a30` 是 `xor eax,eax; ret 8`，返回值不含判定结果。

        所以 `MPSvcDll.dll` 那层 `jl` 永远不跳，`FilterWords` 恒返回 1。
        """
        # 0x403a55: xor eax,eax (33 C0), then ret 8 (C2 08 00)
        self.assertEqual(_read(self.pe_exe, self.exe, 0x403A55, 2), bytes([0x33, 0xC0]))
        self.assertEqual(_read(self.pe_exe, self.exe, 0x403A57, 3), bytes([0xC2, 0x08, 0x00]))

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_server_method_loads_the_same_file_handler_reads(self):
        """服务端读的是 hnclib 下那个 uFWORD 数据文件，与 Handler.dll 同源。"""
        blob = _read(self.pe_exe, self.exe, 0x40E1F4, 0x20)
        self.assertIn(b"uFWORD.dat", blob)
        self.assertIn(b"hnclib", blob)
        handler = (LEGACY / "Windows" / "Handler.dll").read_bytes()
        self.assertIn(b"uFWORD.dat", handler)

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_row_limits_match_handler(self):
        """400 行上限 / 50 字节单条上限，与 Handler 侧数字一致。"""
        # 0x401f38 cmp esi,0x190  -> 400 行
        self.assertEqual(_read(self.pe_exe, self.exe, 0x401F38, 2), bytes([0x81, 0xFE]))
        self.assertEqual(int.from_bytes(_read(self.pe_exe, self.exe, 0x401F3A, 4), "little"), 0x190)
        # 0x401f7a push 0x32 -> 50 字节
        self.assertEqual(_read(self.pe_exe, self.exe, 0x401F7A, 2), bytes([0x6A, 0x32]))
