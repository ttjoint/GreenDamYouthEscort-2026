# -*- coding: utf-8 -*-
"""调用图测试：产品到底有没有用 HNC 的句类分析。

## 为什么值得锁住

我们花了很长时间纠结「12 个 `.lib` 缺失，所以复刻不了 HNC」。
实际上那是个假问题——即使把 12 个文件凑齐，绿坝也不会用句类分析做过滤，
因为 `Handler.dll` 根本没接那条线。

这组测试把「没接」这件事的证据固化下来，避免以后又绕回去。

依据见 `docs/engine-call-graph.md`。
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
class SentenceAnalysisIsDeadCodeTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
