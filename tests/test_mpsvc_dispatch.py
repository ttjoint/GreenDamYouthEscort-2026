# -*- coding: utf-8 -*-
"""`MPSvcC.exe` 里 FilterWords / UpdateWords 的真实分工。

结论一句话：`FilterWords` 只把词表装进全局对象，`UpdateWords`
才拿文本去比。名字和职责是反的。

这里的断言全部来自字节，可以被推翻。
"""

from __future__ import annotations

import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEGACY = ROOT.parent / "LH-installed-tree"
EXE = LEGACY / "Windows" / "MPSvcC.exe"
DLL = LEGACY / "Windows" / "MPSvcDll.dll"
HANDLER = LEGACY / "Windows" / "Handler.dll"


def _have_tree() -> bool:
    return EXE.is_file() and DLL.is_file()


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pefile

        cls.exe = EXE.read_bytes()
        cls.dll = DLL.read_bytes()
        cls.pe = pefile.PE(data=cls.exe)
        cls.ib = cls.pe.OPTIONAL_HEADER.ImageBase

    def read(self, va: int, size: int) -> bytes:
        for sec in self.pe.sections:
            start = sec.VirtualAddress + self.ib
            if start <= va < start + sec.Misc_VirtualSize:
                off = sec.PointerToRawData + (va - start)
                return self.exe[off : off + size]
        raise AssertionError(f"VA {va:#x} 不在任何段内")

    def dwords(self, va: int, n: int):
        return struct.unpack(f"<{n}I", self.read(va, 4 * n))


@unittest.skipUnless(_have_tree(), "install tree not present")
class ObjLayoutTest(_Base):
    """全局对象 `0x40f6b8` 的布局由构造函数 `0x402c60` 定。"""

    def test_constructor_installs_vtable_0x40c278(self):
        # 0x402c69: mov dword ptr [esi], 0x40c278
        self.assertEqual(self.read(0x402C69, 2), bytes([0xC7, 0x06]))
        self.assertEqual(self.dwords(0x402C6B, 1)[0], 0x40C278)

    def test_reset_zeroes_the_two_fields(self):
        """`0x401de0` 把 `+4` / `+8` 清掉——就是数组和计数。"""
        # 0x401de2: xor ecx,ecx ; then mov [eax+4],ecx ; mov [eax+8],ecx
        self.assertEqual(self.read(0x401DE2, 2), bytes([0x33, 0xC9]))
        self.assertEqual(self.read(0x401DEA, 2), bytes([0x89, 0x48]))  # mov [eax+4],ecx
        self.assertEqual(self.read(0x401DED, 2), bytes([0x89, 0x48]))  # mov [eax+8],ecx

    def test_global_object_is_different_from_the_lock(self):
        """`this` 和临界区不是同一块内存。"""
        self.assertEqual(self.dwords(0x40F6B8, 1)[0], 0)  # 运行前是 0
        # 两个地址相差 0x10
        self.assertEqual(0x40F6C8 - 0x40F6B8, 0x10)


@unittest.skipUnless(_have_tree(), "install tree not present")
class WorkerIsThiscallTest(_Base):
    """`0x401e60` 用 `ebp` 装 `this`，不是栈帧。"""

    def test_prologue_has_no_stack_frame(self):
        """只有 `push ebp`，没有 `mov ebp,esp`。"""
        # 0x401e66 push ebx / 0x401e67 push ebp / 0x401e69 mov ebp,ecx
        self.assertEqual(self.read(0x401E66, 1), bytes([0x53]))
        self.assertEqual(self.read(0x401E67, 1), bytes([0x55]))
        self.assertEqual(self.read(0x401E68, 1), bytes([0x56]))  # push esi
        self.assertEqual(self.read(0x401E69, 2), bytes([0x8B, 0xE9]))  # mov ebp,ecx

    def test_fields_are_this_plus_4_and_8(self):
        """写回的是 `[ebp+4]`（数组）和 `[ebp+8]`（行数）。"""
        self.assertEqual(self.read(0x401F63, 3), bytes([0x89, 0x45, 0x04]))  # mov [ebp+4],eax
        self.assertEqual(self.read(0x401F3E, 3), bytes([0x89, 0x75, 0x08]))  # mov [ebp+8],esi

    def test_these_are_the_same_offsets_handler_reads(self):
        """`Handler.dll` 的 `0x31007b70` 读 `[+4]` / `[+8]`。"""
        handler = HANDLER.read_bytes()
        body = handler[0x7B70:0x7BEB]
        self.assertIn(bytes([0x8B, 0x43, 0x04]), body)  # mov eax,[ebx+4]
        self.assertIn(bytes([0x8B, 0x43, 0x08]), body)  # mov eax,[ebx+8]


@unittest.skipUnless(_have_tree(), "install tree not present")
class MatchWorkerTest(_Base):
    """`0x402020` 是唯一的匹配循环。"""

    def test_hit_returns_one(self):
        # 0x40206a: pop edi / pop esi / pop ebp ; 0x40206d: mov eax,1 ; ret 4
        self.assertEqual(self.read(0x40206A, 3), bytes([0x5F, 0x5E, 0x5D]))
        self.assertEqual(self.read(0x40206D, 5), bytes([0xB8, 0x01, 0x00, 0x00, 0x00]))
        self.assertEqual(self.read(0x402072, 1), bytes([0x5B]))  # pop ebx
        self.assertEqual(self.read(0x402073, 3), bytes([0xC2, 0x04, 0x00]))

    def test_miss_returns_zero(self):
        # 0x402064: xor eax,eax ; pop ebx ; ret 4
        self.assertEqual(self.read(0x402064, 2), bytes([0x33, 0xC0]))
        self.assertEqual(self.read(0x402066, 1), bytes([0x5B]))  # pop ebx
        self.assertEqual(self.read(0x402067, 3), bytes([0xC2, 0x04, 0x00]))

    def test_skips_null_and_empty_items(self):
        # 0x402039: test edx,edx ; je -> 0x402059
        self.assertEqual(self.read(0x402039, 2), bytes([0x85, 0xD2]))
        self.assertEqual(self.read(0x40203B, 2), bytes([0x74, 0x1C]))
        # 0x402049: je 0x402059  (strlen == 0)
        self.assertEqual(self.read(0x402049, 2), bytes([0x74, 0x0E]))
        self.assertEqual(0x402049 + 2 + 0x0E, 0x402059)

    def test_calls_strstr_at_0x4050e0(self):
        # 0x40204d: call 0x4050e0  (e8 rel32)
        self.assertEqual(self.read(0x40204D, 1), bytes([0xE8]))
        rel = struct.unpack("<i", self.read(0x40204E, 4))[0]
        self.assertEqual(0x402052 + rel, 0x4050E0)
        # 命中就跳去返回 1
        self.assertEqual(self.read(0x402055, 2), bytes([0x85, 0xC0]))
        self.assertEqual(self.read(0x402057, 2), bytes([0x75, 0x11]))
        self.assertEqual(0x402057 + 2 + 0x11, 0x40206A)


@unittest.skipUnless(_have_tree(), "install tree not present")
class SameStrstrTest(_Base):
    """两个模块静态链接了同一份 `strstr`。"""

    @staticmethod
    def _handler_strstr() -> bytes:
        """按段映射把 VA `0x310082f0` 换成文件偏移。"""
        import pefile

        raw = HANDLER.read_bytes()
        pe = pefile.PE(data=raw)
        ib = pe.OPTIONAL_HEADER.ImageBase
        va = 0x310082F0
        for sec in pe.sections:
            start = sec.VirtualAddress + ib
            if start <= va < start + sec.Misc_VirtualSize:
                off = sec.PointerToRawData + (va - start)
                return raw[off : off + 0x90]
        raise AssertionError("VA 不在任何段内")

    def test_first_0x6e_bytes_are_identical(self):
        a = self._handler_strstr()[:0x6E]
        b = self.exe[0x50E0 : 0x50E0 + 0x6E]
        self.assertEqual(a, b)

    def test_the_split_is_a_relative_jump_to_strchr(self):
        """前 `0x6e` 字节一致，之后是一条 `e9` 跳到各自模块的 `strchr`。"""
        a = self._handler_strstr()[0x6E : 0x74]
        b = self.exe[0x50E0 + 0x6E : 0x50E0 + 0x74]
        self.assertEqual(a[0], 0xE9)
        self.assertEqual(b[0], 0xE9)
        self.assertNotEqual(a, b)
        # 4 字节相对位移不同，是因为 strchr 在两个模块里的地址不同
        self.assertNotEqual(a[1:5], b[1:5])


@unittest.skipUnless(_have_tree(), "install tree not present")
class NoProtocolExclusionTest(_Base):
    """`MPSvcC.exe` 这一路没有协议前缀排除。"""

    def test_no_http_or_ftp_literal_in_the_binary(self):
        self.assertEqual(self.exe.find(b"http://"), -1)
        self.assertEqual(self.exe.find(b"ftp://"), -1)

    def test_handler_does_have_them(self):
        """反证：`Handler.dll` 里就是有的，所以这不是全树都缺。"""
        handler = HANDLER.read_bytes()
        self.assertNotEqual(handler.find(b"http://"), -1)
        self.assertNotEqual(handler.find(b"ftp://"), -1)


@unittest.skipUnless(_have_tree(), "install tree not present")
class TwoMethodsTwoSlotsTest(_Base):
    """`FilterWords` 和 `UpdateWords` 落在不同槽、进不同 worker。"""

    def test_updatewords_enters_0x402020(self):
        # 0x403a80: call 0x402020
        self.assertEqual(self.read(0x403A80, 1), bytes([0xE8]))
        rel = struct.unpack("<i", self.read(0x403A81, 4))[0]
        self.assertEqual(0x403A85 + rel, 0x402020)

    def test_filterwords_enters_0x401e60(self):
        self.assertEqual(self.read(0x403A45, 1), bytes([0xE8]))
        rel = struct.unpack("<i", self.read(0x403A46, 4))[0]
        self.assertEqual(0x403A4A + rel, 0x401E60)

    def test_updatewords_writes_its_result_out(self):
        """`0x403a8a` 把 worker 的返回值写进第 3 个参数的指向。"""
        # mov [esi], eax  after 0x403a85 push
        self.assertEqual(self.read(0x403A8A, 2), bytes([0x89, 0x06]))
        self.assertEqual(self.read(0x403A95, 3), bytes([0xC2, 0x0C, 0x00]))
