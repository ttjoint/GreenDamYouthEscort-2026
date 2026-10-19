# -*- coding: utf-8 -*-
"""调用图测试：产品用的是 HNC 的哪一部分。

## 要分清的两件事

"用了 HNC 引擎" 和 "用了 HNC 的句类分析" 不是一回事：

- 产品**确实**通过 `IHncTextGroup` 调 `CorpGroup`（`Handler.dll` 里有 4 个
  vtable 槽的调用点），所以"没接 HNC"是错的；
- 但句类分析函数（`GetSentenceALC`/`Ini`/`MakeFandongWordList`/`CheckVV`/
  `GetALC`）**零调用点**：无 `call rel32`、无绝对地址、不在任何 vtable 里。

这组测试两边都锁住，避免以后再笼统地说"死代码"或"没用 HNC"。

依据见 `docs/engine-call-graph.md` §0 与 §1.5。
"""

from __future__ import annotations

import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TREE = ROOT.parent / "LH-installed-tree" / "Windows"


def _dll(name: str = "SentenceObj.dll") -> bytes:
    return (TREE / name).read_bytes()


def _calls_to(data: bytes, target_rva: int) -> list[int]:
    """找出所有 `call rel32` 指向 target_rva 的位置（文件偏移）。"""
    hits = []
    for i in range(len(data) - 5):
        if data[i] == 0xE8:
            rel = int.from_bytes(data[i + 1 : i + 5], "little", signed=True)
            if i + 5 + rel == target_rva:
                hits.append(i)
    return hits


def _abs_refs(data: bytes, rva: int) -> list[int]:
    """找出绝对地址 `0x10000000 + rva` 出现的位置。"""
    pat = struct.pack("<I", 0x10000000 + rva)
    out = []
    p = 0
    while True:
        p = data.find(pat, p)
        if p < 0:
            return out
        out.append(p)
        p += 1


@unittest.skipUnless((TREE / "SentenceObj.dll").exists(), "安装树不在")
class SentenceAnalysisHasNoCallSiteTest(unittest.TestCase):
    """句类分析的入口函数，全 DLL 范围内没有调用点。"""

    DEAD = {
        "Ini": 0x68D0,
        "GetSentenceALC": 0x6DC0,
        "GetALC": 0x4E70,
        "MakeFandongWordList": 0x5CC0,
        "CheckVV(CSentenceObj)": 0x5E10,
    }

    def test_no_calls_anywhere(self):
        d = _dll()
        for name, rva in self.DEAD.items():
            with self.subTest(function=name):
                self.assertEqual(
                    _calls_to(d, rva), [],
                    f"{name} 不应该有调用点，但找到了",
                )

    def test_no_absolute_address_reference(self):
        """连虚表都没进——只是导出符号。"""
        d = _dll()
        for name, rva in self.DEAD.items():
            with self.subTest(function=name):
                self.assertEqual(
                    _abs_refs(d, rva), [],
                    f"{name} 的绝对地址不应出现在文件里",
                )

    def test_ctor_is_called_exactly_once(self):
        """`CSentenceObj` 构造只被调一次：`CFWordLib` 构造自己的内嵌成员。

        构造出来之后从不使用 —— 这就是「有代码、无路径」的典型形态。
        """
        d = _dll()
        hits = _calls_to(d, 0x5190)
        self.assertEqual(len(hits), 1, f"应只有 1 处，实际 {hits}")
        self.assertEqual(hits[0], 0x3ECF)


@unittest.skipUnless((TREE / "SentenceObj.dll").exists(), "安装树不在")
class CorpGroupLeadsToWordLibraryTest(unittest.TestCase):
    """`CorpGroup` 是分析主入口，它调到的是词库方法，不是句类方法。"""

    def test_corpgroupp_wchar_calls_makealc(self):
        """`CorpGroup(wchar_t*)` @0x2DF0 里只有一个 call，指向 MakeALC。"""
        d = _dll()
        rva = 0x2DF0
        code = d[rva : rva + 0x40]
        calls = []
        for i in range(len(code) - 5):
            if code[i] == 0xE8:
                rel = int.from_bytes(code[i + 1 : i + 5], "little", signed=True)
                calls.append(rva + i + 5 + rel)
        self.assertEqual(calls, [0x28D0], f"应只有 MakeALC，实际 {calls}")

    def test_this_adjustment_present(self):
        """`add ecx,-0x124` 把子对象指针退回主对象。

        没有这一步，`this` 就是错的，调用会崩。
        """
        d = _dll()
        blob = d[0x2DF0 : 0x2DF0 + 0x40]
        self.assertIn(bytes.fromhex("81c1dcfeffff"), blob,
                      "缺少 this - 0x124 调整")

    def test_makealc_never_calls_sentence_functions(self):
        """`MakeALC` 的全部 call 都在词库内部，没有一个碰句类分析。"""
        d = _dll()
        rva = 0x28D0
        code = d[rva : rva + 0x3D0]
        targets = set()
        for i in range(len(code) - 5):
            if code[i] == 0xE8:
                rel = int.from_bytes(code[i + 1 : i + 5], "little", signed=True)
                targets.add(rva + i + 5 + rel)
        forbidden = {0x5190, 0x68D0, 0x6DC0, 0x4E70, 0x5CC0, 0x5E10, 0x69A0}
        overlap = targets & forbidden
        self.assertEqual(overlap, set(),
                         f"MakeALC 竟然调用了句类函数：{overlap}")
        # 至少确认它调了 SearchChar（trie 查找）
        self.assertIn(0x2500, targets, "MakeALC 应该调用 SearchChar")


@unittest.skipUnless((TREE / "Handler.dll").exists(), "安装树不在")
class HandlerUsesOnlyTwoExportsTest(unittest.TestCase):
    """`Handler.dll` 是绿坝真正的过滤模块，只用 2 个函数。"""

    def test_only_two_sentenceobj_imports(self):
        d = _dll("Handler.dll")
        names = set(
            n.decode("ascii")
            for n in [
                b"?CreateHncTextGroup@@YAPAVIHncTextGroup@@PAD@Z",
                b"?ReleaseHncTextGroup@@YAXPAVIHncTextGroup@@@Z",
            ]
        )
        for name in names:
            with self.subTest(name=name):
                self.assertIn(name.encode(), d)

    def test_no_analysis_entry_imported(self):
        """`GetSentenceALC` / `Ini` 等一个字都没导入。"""
        d = _dll("Handler.dll")
        for word in (b"GetSentenceALC", b"GetALC", b"MakeALC",
                     b"CSentenceObj", b"MakeFandongWordList"):
            with self.subTest(word=word):
                self.assertNotIn(word, d, f"Handler.dll 不该出现 {word!r}")

    def test_only_falunword_lib_referenced(self):
        """只引用 `FalunWord.lib`，与 `Ini` 要的 12 个文件无关。"""
        d = _dll("Handler.dll")
        self.assertIn(b"FalunWord.lib", d)
        for name in (b"Word.lib", b"SentLib.lib", b"JiBenJL.lib", b"rule.lib"):
            with self.subTest(lib=name):
                # 注意 FalunWord.lib 里含 "Word.lib"，要排除掉
                idx = d.find(name)
                while idx >= 0:
                    prefix = d[max(0, idx - 8) : idx]
                    self.assertIn(b"Falun", prefix,
                                  f"{name!r} 出现了，但不是 FalunWord 的一部分")
                    idx = d.find(name, idx + 1)

    def test_hnceng_also_only_two(self):
        d = _dll("HncEng.exe")
        self.assertIn(b"?CreateHncTextGroup@@YAPAVIHncTextGroup@@PAD@Z", d)
        self.assertIn(b"?ReleaseHncTextGroup@@YAXPAVIHncTextGroup@@@Z", d)


class HandlerDoesCallHncTest(unittest.TestCase):
    """`Handler.dll` 真的调用了 HNC，结论不再是"没接这条线"。

    之前的结论只看了导入表，漏了 IAT 立即数。补上。
    """

    CREATE_IAT = 0x3101216C
    RELEASE_IAT = 0x31012168
    WIN_DIR = 0x310120B0  # GetWindowsDirectoryA

    def test_create_and_release_are_each_called_once(self):
        d = _dll("Handler.dll")
        for iat, name in (
            (self.CREATE_IAT, "CreateHncTextGroup"),
            (self.RELEASE_IAT, "ReleaseHncTextGroup"),
        ):
            with self.subTest(fn=name):
                self.assertEqual(d.count(iat.to_bytes(4, "little")), 1)

    def test_create_call_is_fed_by_getwindowsdirectory(self):
        d = _dll("Handler.dll")
        # 找 call [0x310120b0] = ff 15 <imm32>。不能只找 imm32，
        # 也不能用 find()：导入 thunk 区里也有一份。
        win_call = bytes([0xFF, 0x15]) + self.WIN_DIR.to_bytes(4, "little")
        create = self.CREATE_IAT.to_bytes(4, "little")
        i_create = d.find(create)
        self.assertGreater(i_create, 0)
        i_win = d.rfind(win_call, 0, i_create)
        self.assertGreater(i_win, 0, "Create 之前应有一次 GetWindowsDirectoryA")
        self.assertLess(i_create - i_win, 0x80, "两者应在同一函数内")
        # 中间应出现 mov edi, \hnclib\ 目录串的立即数
        hnclib = (0x310154E0).to_bytes(4, "little")
        self.assertIn(hnclib, d[i_win:i_create])

    def test_interface_is_used_through_vtable_slots(self):
        """槽 4 设参数、槽 5 触发分析。"""
        d = _dll("Handler.dll")
        create = self.CREATE_IAT.to_bytes(4, "little")
        i = d.find(create)
        window = d[i : i + 0x100]
        self.assertIn(bytes.fromhex("ff5210"), window, "slot[4] call")
        self.assertIn(bytes.fromhex("ff5214"), window, "slot[5] call")
        # 三个配置项之一：1000 = 0x3e8 作为 push imm32
        self.assertIn(bytes.fromhex("68e8030000"), window)

    def test_vtable_slots_point_at_the_expected_functions(self):
        """slot[4]=0x2dc0 存参数，slot[5]=0x2e30 是 CorpGroup。"""
        d = _dll("SentenceObj.dll")
        base = 0x10000000
        vt_rva = 0x451DC  # CFWordLib::IHncTextGroup vtable
        slots = [
            struct.unpack("<I", d[vt_rva + i * 4 : vt_rva + i * 4 + 4])[0]
            for i in range(12)
        ]
        self.assertEqual(slots[4] - base, 0x2DC0)
        self.assertEqual(slots[5] - base, 0x2E30)
        # 槽 0 / 1 / 3 是空函数：ret 0xc / ret / ret 4
        self.assertEqual(d[slots[0] - base : slots[0] - base + 3], bytes([0xC2, 0x0C, 0x00]))
        self.assertEqual(d[slots[1] - base : slots[1] - base + 1], bytes([0xC3]))
        self.assertEqual(d[slots[3] - base : slots[3] - base + 3], bytes([0xC2, 0x04, 0x00]))

    def test_corpgroupp_has_three_transcode_modes(self):
        """0x2e30 按第三个参数选转码：0/1/2，最终落到 MakeALC。"""
        d = _dll("SentenceObj.dll")
        body = d[0x2E30 : 0x2E30 + 0x90]
        self.assertIn(bytes([0x83, 0xFA, 0x01]), body)  # cmp edx, 1
        self.assertIn(bytes([0x83, 0xFA, 0x02]), body)  # cmp edx, 2
        # call 0x28d0（相对位移按指令尾部计算）
        rel = 0x28D0 - (0x2EA6 + 5)
        self.assertIn(b"\xe8" + rel.to_bytes(4, "little", signed=True), body)


if __name__ == "__main__":
    unittest.main()
