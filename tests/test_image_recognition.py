"""图像识别层与 COM 转发层的行为测试。

这两层都曾被错误地判为"不存在"或"死代码"：
图像识别被当成"可以不做的"，
MPSvcDll 的导出被当成"死导出"。

测试锁住的是**反汇编读出的事实**，不是猜测：
阈值常量、魔法值、返回档位、COM 的 CLSID/IID、分类器文件结构。
"""

import re
import struct
import unittest
from pathlib import Path

import capstone
import pefile

LEGACY = Path(__file__).resolve().parents[2] / "LH-installed-tree"
WIN = LEGACY / "Windows"
SYS32 = WIN / "System32"

MAGIC = 0x7AB7  # 31415

# CHECKEX2 @ 0x10005a02 的三组阈值对（BYTES_LE 双精度，见 0x10004a95 起）
SKIN_THRESHOLDS = {
    1: (0.12, 0.30, 0.50),
    3: (0.06, 0.16, 0.57),
    "other": (0.09, 0.20, 0.54),
}


def _md():
    return capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)


def _rva_to_off(pe, rva):
    for s in pe.sections:
        if s.VirtualAddress <= rva < s.VirtualAddress + s.SizeOfRawData:
            return s.PointerToRawData + (rva - s.VirtualAddress)
    return None


def _disasm(path, rva, count_bytes, image_base=None):
    pe = pefile.PE(str(path), fast_load=True)
    off = _rva_to_off(pe, rva)
    raw = path.read_bytes()
    base = image_base or pe.OPTIONAL_HEADER.ImageBase
    return pe, [
        ins
        for ins in _md().disasm(raw[off : off + count_bytes], base + rva)
    ]


def _double_at(path, va, image_base):
    pe = pefile.PE(str(path), fast_load=True)
    off = _rva_to_off(pe, va - image_base)
    b = path.read_bytes()[off : off + 8]
    return struct.unpack("<d", b)[0]


class _NeedsTree(unittest.TestCase):
    def setUp(self):
        if not WIN.is_dir():
            self.skipTest("legacy install tree not present")


class ImageRecognitionTest(_NeedsTree):
    def test_cimage_exists_in_windows_not_system32(self):
        """CImage.dll 的目录位置曾经被我写错，锁住。"""
        self.assertTrue((WIN / "CImage.dll").is_file())
        self.assertFalse((SYS32 / "CImage.dll").is_file())

    def test_opencv_deps_are_in_system32(self):
        for name in ("FImage.dll", "xcore.dll", "Xcv.dll", "Xtool.dll"):
            with self.subTest(dll=name):
                self.assertTrue((SYS32 / name).is_file(), name)

    def test_cimage_links_opencv_and_face(self):
        pe = pefile.PE(str(WIN / "CImage.dll"))
        imported = set()
        for entry in pe.DIRECTORY_ENTRY_IMPORT:
            for im in entry.imports:
                if im.name:
                    imported.add(im.name.decode())
        for fn in ("cvLoadImage", "cvCreateImage", "cvFindContours", "cvCanny"):
            with self.subTest(fn=fn):
                self.assertIn(fn, imported)
        # 人脸检测不在 CImage 里，在 FImage 里
        self.assertNotIn("cvHaarDetectObjects", imported)

    def test_fimage_loads_haar_cascade(self):
        raw = (SYS32 / "FImage.dll").read_bytes()
        self.assertIn(b"cvHaarDetectObjects", raw)
        self.assertIn(b"XFimage.xml", raw)
        for n in (b"FaceDectect", b"FaceDectect2", b"FaceDectect3", b"FaceDectect4"):
            with self.subTest(export=n):
                self.assertIn(n, raw)

    def test_cascade_file_ships_and_is_complete(self):
        """分类器数据是齐的——这一条推翻了"依赖缺失"的判断。"""
        xml = SYS32 / "XFimage.xml"
        self.assertTrue(xml.is_file())
        text = xml.read_text(encoding="latin-1")
        self.assertIn("haarcascade_frontalface_alt2", text)
        self.assertIn("opencv-haar-classifier", text)
        self.assertEqual(len(re.findall(r"<!-- stage \d+ -->", text)), 20)
        self.assertEqual(len(re.findall(r"<!-- tree \d+ -->", text)), 1047)
        self.assertRegex(text, r"<size>\s*20 20\s*</size>")
        # 858,866 字节
        self.assertGreater(xml.stat().st_size, 850_000)

    def test_capscreen_uses_bitblt_srccopy(self):
        """SRCCOPY 在内部例程 0x100048e0 里，CAPSCREEN 只是调用它。"""
        pe, ins = _disasm(WIN / "CImage.dll", 0x48E0, 0xA0)
        text = ["%s %s" % (i.mnemonic, i.op_str) for i in ins]
        self.assertIn("push 0xcc0020", text, "SRCCOPY")
        self.assertTrue(
            any(t.startswith("call dword ptr [0x1000c024]") for t in text),
            "应为 BitBlt 的 IAT 槽",
        )
        # CAPSCREEN 必须调用这个内部例程
        _, cap = _disasm(WIN / "CImage.dll", 0x4970, 0x30)
        self.assertTrue(
            any(i.mnemonic == "call" and i.op_str == "0x100048e0" for i in cap),
            "CAPSCREEN 应调用 0x100048e0",
        )
        self.assertTrue(pe)

    def test_checkex2_validates_magic_and_returns_graded_codes(self):
        base = 0x10000000
        ins = _disasm(WIN / "CImage.dll", 0x59F0, 0x120)[1]
        text = ["%s %s" % (i.mnemonic, i.op_str) for i in ins]
        self.assertTrue(
            any("0x7ab7" in t for t in text), "CHECKEX2 应校验魔数 31415"
        )
        # 返回值档位 100 / 50 必须都在
        movs = [i.op_str for i in ins if i.mnemonic == "mov" and i.op_str.startswith("eax, 0x")]
        self.assertIn("eax, 0x64", movs)  # 100
        self.assertIn("eax, 0x32", movs)  # 50

    def test_skin_thresholds_resolve_to_expected_doubles(self):
        """三档阈值是分两条指令写进栈的双精度低/高 dword。

        按 `mov dword ptr [esp+X], imm32` 的栈槽配对，而不是按文件偏移猜。
        低 dword 在 [esp+0x1c]/[esp+0x24]/[esp+0x14]，
        高 dword 在 [esp+0x20]/[esp+0x28]/[esp+0x18]。
        """
        md = _md()
        md.detail = True
        pe = pefile.PE(str(WIN / "CImage.dll"), fast_load=True)
        off = _rva_to_off(pe, 0x4A95)
        raw = (WIN / "CImage.dll").read_bytes()
        slots = {}
        for ins in md.disasm(raw[off : off + 0xA0], 0x10004A95):
            if ins.mnemonic != "mov" or len(ins.operands) != 2:
                continue
            dst, src = ins.operands
            if dst.type != capstone.x86.X86_OP_MEM:
                continue
            if src.type != capstone.x86.X86_OP_IMM:
                continue
            slots.setdefault(dst.mem.disp, []).append(src.imm)

        def combine(disp_lo, disp_hi, idx):
            lo = slots[disp_lo][idx] & 0xFFFFFFFF
            hi = slots[disp_hi][idx] & 0xFFFFFFFF
            return struct.unpack("<d", struct.pack("<II", lo, hi))[0]

        # 每个模式 3 个双精度：低阈/上阈/另一阈
        layout = {
            1: (0x1C, 0x20, 0x24, 0x28, 0x14, 0x18),
            3: (0x1C, 0x20, 0x24, 0x28, 0x14, 0x18),
            "other": (0x1C, 0x20, 0x24, 0x28, 0x14, 0x18),
        }
        order = [1, 3, "other"]
        for mode, (alo, ahi, blo, bhi, clo, chi) in layout.items():
            idx = order.index(mode)
            got = (
                combine(alo, ahi, idx),
                combine(blo, bhi, idx),
                combine(clo, chi, idx),
            )
            exp = SKIN_THRESHOLDS[mode]
            with self.subTest(mode=mode):
                for g, e in zip(got, exp):
                    self.assertAlmostEqual(g, e, places=6)

    def test_all_checkex_variants_share_the_magic(self):
        pe = pefile.PE(str(WIN / "CImage.dll"))
        raw = (WIN / "CImage.dll").read_bytes()
        checked = 0
        for e in pe.DIRECTORY_ENTRY_EXPORT.symbols:
            if not e.name:
                continue
            name = e.name.decode()
            if not name.startswith("CHECKEX"):
                continue
            off = _rva_to_off(pe, e.address)
            body = raw[off : off + 0x60]
            if struct.pack("<H", MAGIC) not in body:
                continue
            checked += 1
        # CHECKEX / CHECKEX2 是参数校验壳，其余 *x 变体带魔数
        self.assertGreaterEqual(checked, 6)

    def test_weight_table_is_248_bytes_of_doubles(self):
        """push 0xfc 是"用满 248 字节"的写法；/8 = 31 个双精度，位置 0x10010020。"""
        self.assertEqual(0xF8 // 8, 31)
        pe = pefile.PE(str(WIN / "CImage.dll"))
        off = _rva_to_off(pe, 0x10020)
        raw = (WIN / "CImage.dll").read_bytes()
        vals = struct.unpack("<31d", raw[off : off + 0xF8])
        finite = [v for v in vals if abs(v) < 4]
        self.assertGreater(len(finite), 25)
        # 至少有一个负权重（判别式要有正负）
        self.assertTrue(any(v < 0 for v in vals))


class ComForwardingTest(_NeedsTree):
    """MPSvcDll 的导出是 COM 转发，不是死代码。"""

    def test_filterwords_calls_cocreateinstance_then_vtable(self):
        ins = _disasm(WIN / "MPSvcDll.dll", 0x1440, 0x62)[1]
        text = ["%s %s" % (i.mnemonic, i.op_str) for i in ins]
        joined = "\n".join(text)
        self.assertIn("0x100050b0", joined)  # CoInitialize slot
        self.assertIn("0x100050b4", joined)  # CoCreateInstance slot
        self.assertIn("0x100050b8", joined)  # CoUninitialize slot
        self.assertIn("edx + 0x34", joined)  # FilterWords 的 vtable 偏移
        self.assertIn("ecx + 8", joined)  # Release

    def test_updatewords_uses_a_different_vtable_slot(self):
        ins = _disasm(WIN / "MPSvcDll.dll", 0x13E0, 0x40)[1]
        joined = "\n".join("%s %s" % (i.mnemonic, i.op_str) for i in ins)
        self.assertIn("edx + 0x30", joined)
        self.assertNotIn("edx + 0x34", joined)

    def test_iat_slots_are_ole32_com_apis(self):
        pe = pefile.PE(str(WIN / "MPSvcDll.dll"))
        want = {
            0x100050B0: "CoInitialize",
            0x100050B4: "CoCreateInstance",
            0x100050B8: "CoUninitialize",
        }
        got = {}
        for entry in pe.DIRECTORY_ENTRY_IMPORT:
            if entry.dll.decode().lower() != "ole32.dll":
                continue
            for im in entry.imports:
                if im.address in want and im.name:
                    got[im.address] = im.name.decode()
        self.assertEqual(got, want)

    def test_clsid_and_iid_differ_only_in_last_four_bytes(self):
        pe = pefile.PE(str(WIN / "MPSvcDll.dll"))
        raw = (WIN / "MPSvcDll.dll").read_bytes()

        def read(va):
            off = _rva_to_off(pe, va - 0x10000000)
            return raw[off : off + 16]

        clsid = read(0x100050D0)
        iid = read(0x100050C0)
        self.assertEqual(len(clsid), 16)
        self.assertEqual(clsid[:12], iid[:12])
        self.assertNotEqual(clsid[12:], iid[12:])
        # 文档里引用的两个 GUID 字符串必须能对上字节
        self.assertEqual(clsid.hex(), "035a8356aa3bf049ba6e8eefd6d62991")
        self.assertEqual(iid.hex(), "035a8356aa3bf049ba6e8eefd6d62960")

    def test_clsid_bytes_appear_in_all_three_mpsvc_modules(self):
        needle = bytes.fromhex("035a8356aa3bf049ba6e8eefd6d62991")
        for name in ("MPSvcC.exe", "MPSvcDll.dll", "MPSvcPS.dll"):
            with self.subTest(module=name):
                self.assertIn(needle, (WIN / name).read_bytes())

    def test_mpsvcps_is_an_rpc_proxy(self):
        raw = (WIN / "MPSvcPS.dll").read_bytes()
        for marker in (b"NdrProxyInitialize", b"NdrStubInitialize", b"MPSVCPROXY.dll"):
            with self.subTest(marker=marker):
                self.assertIn(marker, raw)


class NoStaticImportIsNotProofTest(unittest.TestCase):
    """方法论回归：全树大量模块动态解析，静态导入表不能证明无调用者。"""

    def test_many_modules_use_dynamic_resolution(self):
        if not WIN.is_dir():
            self.skipTest("legacy install tree not present")
        count = 0
        for path in list(WIN.glob("*.exe")) + list(WIN.glob("*.dll")) + list(
            SYS32.glob("*.dll")
        ):
            try:
                pe = pefile.PE(str(path), fast_load=True)
            except Exception:
                continue
            pe.parse_data_directories(
                directories=[
                    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]
                ]
            )
            names = set()
            for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
                for im in entry.imports:
                    if im.name:
                        names.add(im.name.decode())
            if "GetProcAddress" in names:
                count += 1
        self.assertGreaterEqual(count, 15, "动态解析是常态，不能用导入表反证")


if __name__ == "__main__":
    unittest.main()
