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
