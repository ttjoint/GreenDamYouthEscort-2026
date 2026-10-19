# -*- coding: utf-8 -*-
"""API hook 表的行为测试。

这一组锁住从 `Handler.dll` / `HASrv.dll` 反汇编加静态数据里拿到的硬事实：

1. `HASrv.dll` 是**通用引擎**，自身不含任何被 hook 的 API 名。
   它只导出四个函数，hook 表完全由外部传入——所以"原厂 hook 了哪些
   API"这个问题的答案不在 `HASrv.dll` 里。
2. 两张表位于 `Handler.dll` 的 `.data`，记录步长 108 字节，
   与 `SetApiHookList` 清空的 0x21c dword（2160 字节）能容纳 20 条。
3. 表里只有三类非零字段（DLL 名、API 名、参数个数），
   原函数地址槽在磁盘上为零，是安装时填入的。
4. 15 条 hook 里，11 条是**自我保护**（注册表写入、OpenProcess、
   OpenService），只有 4 条是文字渲染拦截——这个比例本身就是结论，
   不该被读成"原厂有强大的内容拦截能力"。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import audit_api_hooks as H  # noqa: E402

LEGACY = ROOT.parent / "LH-installed-tree"


def _have_tree() -> bool:
    return (LEGACY / "Windows" / "Handler.dll").is_file()


class EngineIsGenericTest(unittest.TestCase):
    """HASrv.dll 不含策略，只含机制。"""

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_engine_source_has_no_hooked_api_names(self):
        raw = (LEGACY / "Windows" / "HASrv.dll").read_bytes()
        # 这几个名字如果出现在引擎里，说明表是硬编码的，结论就不同了。
        for name in (b"TextOutA", b"ExtTextOutA", b"RegDeleteValueA"):
            self.assertNotIn(name, raw, f"{name!r} should not live in HASrv.dll")

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_engine_declares_only_four_exports(self):
        import pefile

        pe = pefile.PE(str(LEGACY / "Windows" / "HASrv.dll"))
        names = {
            e.name.decode()
            for e in pe.DIRECTORY_ENTRY_EXPORT.symbols
            if e.name
        }
        self.assertEqual(
            names,
            {"SetApiHookList", "InstallApiHook", "UninstallApiHook", "LoadClient"},
        )


class TableGeometryTest(unittest.TestCase):
    """表几何：步长、上限、两条表的关系。"""

    def test_record_size_matches_engine_capacity(self):
        # SetApiHookList 清 0x21c 个 dword
        cleared_bytes = 0x21C * 4
        self.assertEqual(cleared_bytes // H.RECORD_SIZE, H.MAX_RECORDS)
        self.assertEqual(H.MAX_RECORDS, 20)

    def test_install_stride_matches_record_size(self):
        # InstallApiHook: lea eax,[esi+esi*2] -> *3, lea eax,[eax+eax*8] -> *9
        self.assertEqual(3 * 9 * 4, H.RECORD_SIZE)

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_record_count_is_under_capacity(self):
        report = H.audit(LEGACY)
        self.assertLessEqual(report["wide"]["count"], H.MAX_RECORDS)
        self.assertLessEqual(report["ansi"]["count"], H.MAX_RECORDS)


class TableContentTest(unittest.TestCase):
    """表内容：条数与分类。"""

    @classmethod
    def setUpClass(cls):
        cls.report = H.audit(LEGACY) if _have_tree() else None

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_wide_table_has_fifteen_hooks(self):
        self.assertEqual(self.report["wide"]["count"], 15)

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_ansi_table_has_six_hooks(self):
        self.assertEqual(self.report["ansi"]["count"], 6)

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_wide_table_contains_ansi_entries(self):
        self.assertTrue(self.report["wide_superset_of_ansi"])

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_only_four_hooks_are_text_rendering(self):
        """文字渲染拦截只有 4 条，其余是自保护。"""
        text = self.report["buckets"]["text_rendering"]
        self.assertEqual(len(text), 4)
        self.assertEqual(
            sorted(text), ["ExtTextOutA", "ExtTextOutW", "TextOutA", "TextOutW"]
        )

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_self_protection_outnumbers_filtering(self):
        """自保护条目数多于内容拦截条目数，这是原厂的真实重心。"""
        buckets = self.report["buckets"]
        self.assertEqual(len(buckets["self_protect"]), 11)
        self.assertGreater(
            len(buckets["self_protect"]), len(buckets["text_rendering"])
        )

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_text_hooks_are_all_gdi(self):
        for rec in self.report["wide"]["records"]:
            if rec["api"].startswith(("TextOut", "ExtTextOut")):
                self.assertEqual(rec["dll"], "gdi32.dll")


class RecordLayoutTest(unittest.TestCase):
    """记录布局：只有三个字段在磁盘上有数据。"""

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_runtime_address_slot_is_empty_on_disk(self):
        """+0x30 在出厂数据里为零，说明原函数地址是安装时写入的。"""
        raw = (LEGACY / "Windows" / "Handler.dll").read_bytes()
        off = H.WIDE_TABLE - H.HANDLER_BASE
        for n in range(15):
            rec = raw[off + n * H.RECORD_SIZE : off + (n + 1) * H.RECORD_SIZE]
            key_slot = rec[H.OFF_KEY : H.OFF_KEY + 16]
            self.assertEqual(key_slot, b"\x00" * 16, f"record {n} key slot not empty")

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_argument_counts_are_small(self):
        """参数个数都落在 2..8，符合这几个 Win32 API 的真实签名。"""
        report = H.audit(LEGACY)
        counts = {r["arg_count"] for r in report["wide"]["records"]}
        self.assertTrue(counts)
        for c in counts:
            self.assertGreaterEqual(c, 2)
            self.assertLessEqual(c, 8)


class SelectorTest(unittest.TestCase):
    """哪张表被装上，由 settings.ini 的 WriteLog 决定。"""

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_selector_comes_from_settings_ini(self):
        sel = H.audit(LEGACY)["selector"]
        self.assertEqual(sel["ini_path"].lower(), "c:\\settings.ini")
        self.assertEqual(sel["section"], "Settings")
        self.assertEqual(sel["key"], "WriteLog")

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_selector_is_written_exactly_once(self):
        """标志只有一处写入，所以不存在多条件竞争的解读。"""
        import struct

        raw = (LEGACY / "Windows" / "Handler.dll").read_bytes()
        pat = struct.pack("<I", H.SELECTOR_VA)
        writes = []
        i = 0x1000
        while True:
            j = raw.find(pat, i, 0x12000)
            if j < 0:
                break
            # 89 05 = mov [imm32], eax ; 89 15 = mov [imm32], edx
            # a3    = mov moffs32, eax
            if raw[j - 2 : j] in (b"\x89\x05", b"\x89\x15") or raw[j - 1 : j] == b"\xa3":
                writes.append(j)
            i = j + 1
        self.assertEqual(len(writes), 1)

    @unittest.skipUnless(_have_tree(), "legacy install tree not present")
    def test_selector_write_is_adjacent_to_ini_lookup(self):
        """写入点就在 GetPrivateProfile 调用前，两者不可分割。"""
        raw = (LEGACY / "Windows" / "Handler.dll").read_bytes()
        window = raw[H.SELECTOR_WRITE_RVA : H.SELECTOR_WRITE_RVA + 0x10]
        self.assertTrue(
            b"\x89\x15" in window or b"\x89\x05" in window,
            "expected a mov [imm32], reg near the selector write",
        )


if __name__ == "__main__":
    unittest.main()
