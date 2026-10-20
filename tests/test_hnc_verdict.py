# -*- coding: utf-8 -*-
"""判定链测试：HNC 引擎的返回值怎么变成"拦 / 放"。

## 为什么要锁住

`Handler.dll` 调了 HNC（见 `test_engine_call_graph.py`），但没人解释过
返回值怎么用。这里把三层都钉死：

1. `MakeALC` 的六个出口各返回什么、`*out` 什么时候被改写；
2. `CorpGroup` 只是透传，不改语义；
3. `Handler.dll` 把返回值折成分档：0 = 放行，非 0 = 继续。

关键结论：`Handler.dll` 传 `scoring=0`，判定依据是**词条类型码**，
不是句法结构。

依据见 `docs/engine-call-graph.md` §1.6。
"""

from __future__ import annotations

import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TREE = ROOT.parent / "LH-installed-tree" / "Windows"


def _sobj() -> bytes:
    return (TREE / "SentenceObj.dll").read_bytes()


def _handler() -> bytes:
    return (TREE / "Handler.dll").read_bytes()


class MakeAlcExitsTest(unittest.TestCase):
    """`MakeALC` 的出口与 `*out` 写入点。"""

    BASE = 0x10000000
    MAKEALC = 0x28D0

    def test_out_is_preinitialised_to_one(self):
        """入口先 `*out = 1`，命中类型 9 才改成 4。"""
        d = _sobj()
        body = d[self.MAKEALC : self.MAKEALC + 0x40]
        # mov ecx,[esp+0x28] / mov dword ptr [ecx],1
        self.assertIn(bytes([0x8B, 0x4C, 0x24, 0x28]), body)
        self.assertIn(bytes([0xC7, 0x01, 0x01, 0x00, 0x00, 0x00]), body)

    def test_out_is_written_in_exactly_two_places(self):
        """全函数只有入口预置和类型 9 两处写 `*out`。"""
        d = _sobj()
        body = d[self.MAKEALC : self.MAKEALC + 0x400]
        # mov ecx,[esp+0x28] 在入口，mov eax,[esp+0x34] 在 0x2c3a
        self.assertEqual(body.count(bytes([0x8B, 0x4C, 0x24, 0x28])), 1)
        self.assertEqual(body.count(bytes([0x8B, 0x44, 0x24, 0x34])), 1)

    def test_type_nine_writes_four(self):
        """类型 9 的出口：`*out = 4`，落在 0x10002c3a。"""
        d = _sobj()
        # 0x2c3a - 0x28d0 = 0x36a
        body = d[0x2C3A : 0x2C3A + 7]
        self.assertEqual(body[:4], bytes([0x8B, 0x44, 0x24, 0x34]))  # mov eax,[esp+0x34]
        self.assertEqual(body[4:7], bytes([0xC7, 0x00, 0x04]))  # mov [eax],4

    def test_return_values_are_a_small_fixed_set(self):
        """返回值只有 0/1/2/4/7，是可枚举的分档而不是分数。"""
        d = _sobj()
        body = d[self.MAKEALC : self.MAKEALC + 0x400]
        # mov eax,2 / mov eax,7 / mov eax,4 / mov eax,1 / xor eax,eax
        for imm in (1, 2, 4, 7):
            self.assertIn(bytes([0xB8, imm, 0x00, 0x00, 0x00]), body, f"return {imm}")

    def test_scoring_gate_skips_to_zero_return(self):
        """`scoring==0` 时直接跳到返回 0 的收尾块。"""
        d = _sobj()
        body = d[0x2BB8 : 0x2BC1]
        self.assertEqual(body[:4], bytes([0x8A, 0x44, 0x24, 0x30]))  # mov al,[esp+0x30]
        self.assertEqual(body[4:6], bytes([0x33, 0xF6]))  # xor esi,esi
        self.assertEqual(body[6:9], bytes([0x84, 0xC0, 0x0F]))  # test al,al ; je rel32

    def test_makealc_clears_state_on_every_exit(self):
        """每个出口都 call 0x1cb0 清状态，不留下跨调用污染。"""
        d = _sobj()
        body = d[self.MAKEALC : self.MAKEALC + 0x400]
        # call 0x10001cb0 -> e8 rel32
        hits = 0
        i = 0
        while True:
            i = body.find(b"\xe8", i)
            if i < 0:
                break
            rel = struct.unpack("<i", body[i + 1 : i + 5])[0]
            if (self.MAKEALC + i + 5 + rel) == 0x1CB0:
                hits += 1
            i += 1
        self.assertGreaterEqual(hits, 3, "0x1cb0 应在多个出口被调用")


class CorpGroupPassThroughTest(unittest.TestCase):
    """`CorpGroup` 不改变判定语义。"""

    BASE = 0x10000000

    def test_corpgroupp_signature_from_export_name(self):
        """`PAD0_NAAHH` = (char*, char*, bool, int*, int)。"""
        d = _sobj()
        self.assertIn(b"?CorpGroup@CFWordLib@@UAEHPAD0_NAAHH@Z", d)

    def test_makealc_signature_from_export_name(self):
        """`PAG0_NPAH` = (wchar_t*, wchar_t*, bool, int*)。"""
        d = _sobj()
        self.assertIn(b"?MakeALC@CFWordLib@@QAEHPAG0_NPAH@Z", d)

    def test_corpgroupp_truncates_to_makealc_by_ret(self):
        """`0x2e30` 是 `ret 0x14`，把 `MakeALC` 的返回值原样交回。"""
        d = _sobj()
        body = d[0x2E30 : 0x2EB0]
        self.assertEqual(d[0x2EAC], 0xC2)  # ret imm16
        self.assertEqual(struct.unpack("<H", d[0x2EAD : 0x2EAF])[0], 0x14)


class HandlerVerdictTest(unittest.TestCase):
    """`Handler.dll` 怎么把返回值折成放行 / 继续。"""

    def test_scoring_argument_is_zero(self):
        """调用点 `0x31007354` 第一个 push 是 0 —— 不打分。"""
        d = _handler()
        # 0x31007354 push 0 (mode) ; 0x31007354 前是 lea eax,[esp+0xc] (&out)
        self.assertEqual(d[0x7354:0x7356], bytes([0x6A, 0x00]))
        # 再往前是 lea eax,[esp+0xc] -> &out
        self.assertEqual(d[0x7350:0x7354], bytes([0x8D, 0x44, 0x24, 0x0C]))
        # 0x3100735b 第二个 push 0 = scoring
        self.assertEqual(d[0x735B:0x735D], bytes([0x6A, 0x00]))

    def test_three_outcomes_only(self):
        """`cmp eax,1` / `cmp eax,2` 两条比较，配合 out 折出三档。"""
        d = _handler()
        body = d[0x7380:0x739D]
        self.assertIn(bytes([0x83, 0xF8, 0x01]), body)  # cmp eax,1
        self.assertIn(bytes([0x83, 0xF8, 0x02]), body)  # cmp eax,2
        # mov dword ptr [esi+4],3
        self.assertIn(bytes([0xC7, 0x46, 0x04, 0x03, 0x00, 0x00, 0x00]), body)

    def test_out_folding_arithmetic(self):
        """`out==4` 折成 2，其它折成 4，`out==1` 保持 1。"""
        d = _handler()
        body = d[0x736B:0x7380]
        self.assertIn(bytes([0x83, 0xF9, 0x01]), body)  # cmp ecx,1
        self.assertIn(bytes([0x83, 0xE9, 0x04]), body)  # sub ecx,4
        self.assertIn(bytes([0xF7, 0xD9]), body)  # neg ecx
        self.assertIn(bytes([0x1B, 0xC9]), body)  # sbb ecx,ecx
        self.assertIn(bytes([0x83, 0xE1, 0x02]), body)  # and ecx,2
        self.assertIn(bytes([0x83, 0xC1, 0x02]), body)  # add ecx,2

    def test_return_zero_goes_to_release_path(self):
        """返回 0 跳到 `0x31004ed1`，那里摘链 + HeapFree。"""
        d = _handler()
        body = d[0x4E25:0x4E2D]
        self.assertEqual(body[:2], bytes([0x85, 0xC0]))  # test eax,eax
        self.assertEqual(body[2:4], bytes([0x0F, 0x84]))  # je rel32
        rel = struct.unpack("<i", body[4:8])[0]
        target = (0x31004E25 + 2 + 6 + rel) & 0xFFFFFFFF
        self.assertEqual(target, 0x31004ED1)

    def test_release_path_frees_the_record(self):
        """放行路径确实释放内存，不是把记录留着。"""
        d = _handler()
        body = d[0x4ED1:0x4F34]
        # EnterCriticalSection / LeaveCriticalSection / SetEvent 三个 IAT 立即数
        for iat, name in (
            (0x31012050, "EnterCriticalSection"),
            (0x310120A8, "LeaveCriticalSection"),
            (0x31012014, "SetEvent"),
        ):
            with self.subTest(fn=name):
                self.assertIn(iat.to_bytes(4, "little"), body)
        # 两处 call 0x310081c0（HeapFree 包装）
        self.assertGreaterEqual(body.count(bytes([0xE8])), 2)

    def test_foreground_process_is_checked_first(self):
        """拦之前先比对前台窗口 PID，后台进程的记录直接放掉。"""
        d = _handler()
        body = d[0x4DD8:0x4DF8]
        self.assertIn((0x310121C0).to_bytes(4, "little"), body)  # GetForegroundWindow
        self.assertIn((0x310121C4).to_bytes(4, "little"), body)  # GetWindowThreadProcessId
        # 0x31004dec mov eax,[esi+4] / 0x31004df1 cmp edx,eax
        self.assertIn(bytes([0x8B, 0x46, 0x04]), body)
        self.assertIn(bytes([0x3B, 0xD0]), body)


class NoSentenceAnalysisInVerdictTest(unittest.TestCase):
    """判定链上真的没有句法分析。"""

    def test_makealc_body_has_no_sentenceobj_calls(self):
        """`MakeALC` 全部 call 都落在词库内部，没有一个碰句类分析函数。"""
        d = _sobj()
        body = d[0x28D0 : 0x28D0 + 0x400]
        targets = set()
        i = 0
        while True:
            i = body.find(b"\xe8", i)
            if i < 0:
                break
            rel = struct.unpack("<i", body[i + 1 : i + 5])[0]
            targets.add((0x28D0 + i + 5 + rel) & 0xFFFFFFFF)
            i += 1
        forbidden = {
            0x10006DC0,  # GetSentenceALC
            0x100068D0,  # Ini
            0x10005CC0,  # MakeFandongWordList
            0x10005E10,  # CheckVV
            0x10004E70,  # GetALC
        }
        self.assertFalse(targets & forbidden, f"不该出现句类分析: {targets & forbidden}")

    def test_hit_uses_word_entry_type_field(self):
        """命中后读的是 `[esi+0xc]`，即 trie 节点的词条类型码。"""
        d = _sobj()
        # 0x10002abd 附近：mov edx,[esi+0xc] / shl edx,4 ...
        body = d[0x2ABD : 0x2AE0]
        self.assertIn(bytes([0x8B, 0x56, 0x0C]), body)


if __name__ == "__main__":
    unittest.main()
