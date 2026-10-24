# -*- coding: utf-8 -*-
"""原始 HNC 引擎（SentenceObj.dll）的可调用性与缺失文件：实测锚点。

对应 `docs/hnc-engine-probe.md`。

## 这些测试为什么存在

上一轮的文档（`REVERSE-ENGINEERING/08-HNC语义引擎剖析.md`）对引擎
能力做了强断言，但**没有一条经过实际运行验证**。本测试把实际测到的
事实钉住，防止后来的推断覆盖掉实测结果。

## 测什么

1. DLL 的位数与导出表（不需要运行 DLL，纯静态读 PE）。
2. 引擎依赖的文件清单（从 DLL 字符串还原）与磁盘实际状态。
3. 输出格式的关键标签是否真的存在于 DLL 里。
4. 源文件路径泄漏（21 个 .cpp）。

## 不测什么

不测试实际调用。原因：需要 32 位 Python 子进程 + 中文路径 + thiscall
thunk，且**必定失败**（缺 FMean.lib）。实跑见
`tools/call_hnc_engine.py`，其结论已被本测试静态覆盖。
"""


import re
import sys
import struct
import unittest
from pathlib import Path


def _h(hexstr):
    """16 进制串转 bytes。

    不用 bytes.fromhex：那是 3.0+ 才有的，32 位 Python 2.7 里没有。
    这个模块得能在两个解释器下都导入，所以自己包一层。
    """
    return bytes(bytearray.fromhex(hexstr))

ROOT = Path(__file__).resolve().parents[1]
#: 安装树在仓库外（父目录），不存在时跳过。
WINDOWS = ROOT.parent / "LH-installed-tree" / "Windows"
DLL = WINDOWS / "SentenceObj.dll"
HNCLIB = WINDOWS / "HNCLIB"


def _pe_sections(data: bytes) -> list[tuple[str, int, int, int, int]]:
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    opt = pe + 24
    oh = struct.unpack_from("<H", data, pe + 20)[0]
    out = []
    for i in range(nsec):
        o = opt + oh + i * 40
        name = data[o:o + 8].rstrip(b"\0").decode("latin-1")
        vs, va, rs, ra = struct.unpack_from("<IIII", data, o + 8)
        out.append((name, va, vs, ra, rs))
    return out


def _exports(data: bytes) -> list[str]:
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    dd = opt + (112 if magic == 0x20B else 96)
    edir_rva = struct.unpack_from("<I", data, dd)[0]
    secs = _pe_sections(data)

    def r2o(rva: int) -> int | None:
        for _n, va, vs, ra, rs in secs:
            if va <= rva < va + max(vs, rs):
                return ra + (rva - va)
        return None

    o = r2o(edir_rva)
    if o is None:
        return []
    nnames = struct.unpack_from("<I", data, o + 24)[0]
    names_rva = struct.unpack_from("<I", data, o + 32)[0]
    on = r2o(names_rva)
    res = []
    for i in range(nnames):
        nr = struct.unpack_from("<I", data, on + i * 4)[0]
        no = r2o(nr)
        end = data.index(b"\0", no)
        res.append(data[no:end].decode("latin-1"))
    return res


@unittest.skipUnless(DLL.exists(), "安装树不在预期位置")
class EngineDllFactsTest(unittest.TestCase):
    """静态读 PE 即可确认的事实。"""

    @classmethod
    def setUpClass(cls):
        cls.data = DLL.read_bytes()

    def test_is_pe32_x86(self):
        """DLL 是 32 位，必须用 32 位 Python 才能 LoadLibrary。

        实测：64 位 Python 加载失败（errno 126），本机
        D:\\Python27\\python.exe（2.7-32）可成功加载到基址 0x10000000。
        """
        pe = struct.unpack_from("<I", self.data, 0x3C)[0]
        machine = struct.unpack_from("<H", self.data, pe + 4)[0]
        magic = struct.unpack_from("<H", self.data, pe + 24)[0]
        self.assertEqual(machine, 0x14C, "IMAGE_FILE_MACHINE_I386")
        self.assertEqual(magic, 0x10B, "PE32（不是 PE32+）")

    def test_export_count_and_key_symbols(self):
        exps = _exports(self.data)
        self.assertEqual(len(exps), 72, "实测 72 个导出")
        for name in (
            "?CreateHncTextGroup@@YAPAVIHncTextGroup@@PAD@Z",
            "?ReleaseHncTextGroup@@YAXPAVIHncTextGroup@@@Z",
            "?LoadLib@CFWordLib@@QAEHPAD@Z",
            "?CutWordMax@CFWordLib@@QAEHPAGHPAUCFWordPosRec@@H@Z",
            "?MakeALC@CFWordLib@@QAEHPAG0_NPAH@Z",
            "?GetWordFanDong@CFWordLib@@QAEHH@Z",
            "?GetWordHongHei@CFWordLib@@QAEHH@Z",
            "?GetSentenceALC@CSentenceObj@@QAEHPAG0PAVCWordSet_Simple@@@Z",
        ):
            with self.subTest(sym=name):
                self.assertIn(name, exps)


@unittest.skipUnless(DLL.exists(), "安装树不在预期位置")
class RequiredFilesTest(unittest.TestCase):
    """引擎初始化需要 5 个文件，分发版只有 1 个。"""

    @classmethod
    def setUpClass(cls):
        cls.data = DLL.read_bytes()

    def test_engine_references_five_hnclib_files(self):
        # 字符串簇原样出现在 DLL 里：%s\\FalunWord.lib 等
        for name in (rb"%s\FalunWord.lib", rb"%s\FMean.lib",
                     rb"%s\FMean.tmp", rb"%s\FWL.tmp", rb"%s\FWL.txt"):
            with self.subTest(f=name):
                self.assertIn(name, self.data)

    @unittest.skipUnless(HNCLIB.exists(), "HNCLIB 目录不存在")
    def test_only_falunword_is_shipped(self):
        """这是本项目最关键的实测事实。

        CreateHncTextGroup 因缺 FMean.lib 等返回 NULL、打印
        'Ini Error!'。所以\"能不能调用原始 HNC 库\"的答案是：
        能加载，不能初始化。
        """
        present = {p.name for p in HNCLIB.iterdir() if p.is_file()}
        self.assertIn("FalunWord.lib", present)
        for missing in ("FMean.lib", "FMean.tmp", "FWL.tmp", "FWL.txt"):
            with self.subTest(f=missing):
                self.assertNotIn(missing, present,
                                 f"{missing} 居然存在了？请更新 "
                                 f"docs/hnc-engine-probe.md 并重跑引擎")


@unittest.skipUnless(DLL.exists(), "安装树不在预期位置")
class OutputFormatTest(unittest.TestCase):
    """引擎生成的 XML 标签体系——文献里没写、实测才拿到。"""

    @classmethod
    def setUpClass(cls):
        cls.text = DLL.read_bytes().decode("gbk", errors="ignore")

    def test_grading_dimensions(self):
        """7 维打分：对立统一性 / 褒贬 / 下级E的褒贬 / 相对立场。"""
        for dim in ("对立统一性", "褒贬", "下级E的褒贬", "相对立场"):
            with self.subTest(dim=dim):
                self.assertIn(dim, self.text)

    def test_direct_vs_indirect_hit(self):
        """★ 最关键的发现：引擎区分字面命中与语义推断命中。

        这是 HNC 结构分析相对纯词表的**唯一实证价值点**。
        """
        self.assertIn("对象语义信息(直接命中)", self.text)
        self.assertIn("对象语义信息(间接命中)", self.text)

    def test_block_expansion_and_sentential_transformation(self):
        """块扩 / 句蜕 / 5 类要素句蜕。"""
        for tag in ("块扩", "句蜕", "E要素句蜕", "JK1要素句蜕",
                    "JK2要素句蜕", "JK3要素句蜕", "包装句蜕",
                    "复合子句", "主句", "块素"):
            with self.subTest(tag=tag):
                self.assertIn(tag, self.text)

    def test_two_level_exclusion(self):
        """团块绝对排除 / 相对排除 —— 两级而非黑白名单。"""
        self.assertIn("团块绝对排除", self.text)
        self.assertIn("团块相对排除", self.text)


@unittest.skipUnless(DLL.exists(), "安装树不在预期位置")
class SourceLeakTest(unittest.TestCase):
    """编译期源文件路径泄漏 21 个 .cpp，名字对应 HNC 模块。"""

    def test_twenty_one_sources(self):
        data = DLL.read_bytes()
        cpps = {m.group().decode("latin-1")
                for m in re.finditer(rb"[\x20-\x7e]{6,}\.cpp", data)}
        self.assertEqual(len(cpps), 21, f"实测 21 个，得到 {sorted(cpps)}")
        # 这几个直接印证我们复刻的方向
        self.assertIn(r".\src\user\GetlvList.cpp", cpps,
                      "lv 准则（论文 11.1）确实独立成模块")
        self.assertIn(r".\src\user\KDiaoDu-fkJK.cpp", cpps,
                      "句类调度与 fK/JK 同模块（论题 34）")
        self.assertIn(r".\src\user\ActiveRule.cpp", cpps,
                      "规则运行时激活（论文 11.2 运用规则的规则）")


@unittest.skipUnless(DLL.exists(), "安装树不在预期位置")
class MakeAlcReturnCodesTest(unittest.TestCase):
    """`?MakeALC@CFWordLib@@QAEHPAG0_NPAH@Z`（0x28d0）的返回码。

    这一组断言是把反汇编结论钉死，防止以后又把 `ret=7` 误读成"引擎拒绝处理"。

    返回点（文件偏移 = RVA - 0x1000 + 0x1000 = RVA，因为 .text VA==RawPtr）：
        @0x2c64  return 1    整句处理完成
        @0x2c21  return 7    单个 token 处理完
        @0x2c05  return 2
        @0x2c37  return 4
        @0x2d45 / 0x2d4d  return -1

    诚实边界：实测 MakeALC 只走一个 token 就返回 7，从没返回过 1。
    整句循环骨架不在这里，在 CSentenceObj 里。
    """

    # (mov eax,code 的文件偏移, code)
    RETURNS = ((0x2C5B, 1), (0x2C18, 7), (0x2BFC, 2), (0x2C2E, 4))

    def test_return_sites_encode_expected_codes(self):
        data = DLL.read_bytes()
        for off, code in self.RETURNS:
            self.assertEqual(data[off], 0xB8, f"@0x{off:x} 不是 mov eax,imm32")
            got = int.from_bytes(data[off + 1:off + 5], "little")
            self.assertEqual(got, code, f"@0x{off:x} 返回 {got}，期望 {code}")
            # 后面紧跟 pop ebp; add esp,0x14; ret 0x10
            self.assertEqual(data[off + 5:off + 12],
                             _h("5d83c414c21000"),
                             f"@0x{off:x} 不是 __thiscall 的 ret 0x10 结尾")

    def test_token_code_is_seven_not_error(self):
        """7 是"处理了一个 token"，旁边那句 inc [ebp+0x10047c] 是证据。"""
        data = DLL.read_bytes()
        # @0x2c08: mov eax,[ebp+0x10047c] / inc eax / mov [ebp+0x10047c],eax / mov eax,7
        # 0x2bec: mov eax,[ebp+0x10047c] / pop edi;pop esi / inc eax
        #         / mov [ebp+0x10047c],eax / pop ebx / mov eax,7
        window = data[0x2BEC:0x2C18]
        self.assertEqual(window[:6], _h("8b857c041000"),
                         "应为 mov eax,[ebp+0x10047c]")
        self.assertEqual(window[6:10], _h("5f5e4089"),
                         "应为 pop edi; pop esi; inc eax; mov ...")
        self.assertEqual(window[10:17], _h("857c0410005bb8"),
                         "应为 [ebp+0x10047c] 做目的操作数 + pop ebx + mov eax")
        self.assertEqual(data[0x2C18], 0xB8)
        self.assertEqual(int.from_bytes(data[0x2C19:0x2C1D], "little"), 7)


@unittest.skipUnless(DLL.exists(), "安装树不在预期位置")
class GetAlcNeedsGlobalTest(unittest.TestCase):
    """`?GetALC@@YAHPAG0_N@Z`（0x4e70）开头就检查全局 wordlib 指针。"""

    def test_null_global_short_circuits_to_seven(self):
        data = DLL.read_bytes()
        # 0x4e70: 51                push ecx
        self.assertEqual(data[0x4E70], 0x51)
        # 0x4e71: a1 e8 31 0b 10   mov eax,[0x100B31E8]
        self.assertEqual(data[0x4E71:0x4E76], _h("a1e8310b10"))
        # 0x4e76: 85 c0            test eax,eax
        self.assertEqual(data[0x4E76:0x4E78], _h("85c0"))
        # 0x4e78: 0f 84 88 00 00 00  je 0x4f06
        self.assertEqual(data[0x4E78:0x4E7E], _h("0f8488000000"))
        # 0x4f06: b8 07 00 00 00   mov eax,7   (pop ecx 在 0x4f0a)
        self.assertEqual(data[0x4F06], 0xB8)
        self.assertEqual(int.from_bytes(data[0x4F07:0x4F0B], "little"), 7)


@unittest.skipUnless(DLL.exists(), "安装树不在预期位置")
class IniCheckpointChainTest(unittest.TestCase):
    """`CSentenceObj::Ini` 是一条串行检查链，每个检查点有独立错误串。

    这一组测试把「引擎需要 12 个数据文件、包里只有 1 个」钉死。
    这是本项目"不能拿原始引擎当参照答案"的直接证据。

    反汇编依据：`?Ini@CSentenceObj@@QAEHPAD@Z` @0x68d0 依次调用
    sub_11700（内部再串 0xff90 / 0x1f5d0 / 0x1d100 / ...）。
    """

    # 错误串 -> 它要求的文件（None 表示未反汇编到具体文件名）
    CHECKPOINTS = {
        b"WordLib Ini Err!": b"Word.lib",
        b"SentLib Ini Err!": None,
        b"EsseSentCat Ini Err!": None,
        b"ActiveRule Ini Err!": b"ActiveMode.lib",
        b"DefInit Ini Err!": b"DefIndex.lib",
        b"UnifyPercList Ini Err!": b"UnifyPerc.lib",
        b"UserDefine_hNotV Ini Err!": None,
        b"EgPerc Ini Err!": b"EgPerc.lib",
        b"MoreVFrame Ini Err!": None,
        b"Asc Disp Ini Err!": None,
        b"SentStructure Ini Err!": None,
    }

    def test_all_error_strings_present(self):
        """11 个检查点的错误串都在 DLL 里。少一个说明我数错了。"""
        data = DLL.read_bytes()
        for label in self.CHECKPOINTS:
            with self.subTest(label=label):
                self.assertIn(label, data, f"没找到 {label!r}")

    def test_word_lib_is_not_falunword(self):
        """`Ini` 要的是 `Word.lib`，不是 `FalunWord.lib`。

        这俩属于不同层：建组用 FalunWord.lib，分析用 Word.lib。
        混淆过一次，这里钉死。
        """
        data = DLL.read_bytes()
        self.assertIn(b"Word.lib", data)
        self.assertIn(b"FalunWord.lib", data)
        # 0x458e4 是 "%s%s"——拼路径不带分隔符，所以 Ini 的入参
        # 必须自带尾反斜杠（和 CreateHncTextGroup 的 %s\FalunWord.lib 不同）
        self.assertEqual(data[0x458E4:0x458E8], b"%s%s")

    def test_required_data_files_are_missing(self):
        """12 个必需数据文件，磁盘上一个都不在。

        如果哪天有人补上了这些文件，这个测试会失败——那是好事，
        说明可以重新尝试跑原始引擎了。
        """
        base = ROOT.parent.parent / "LH-installed-tree" / "Windows" / "HNCLIB"
        if not base.is_dir():
            self.skipTest("安装树里没有 HNCLIB 目录")
        present = {p.name.lower() for p in base.iterdir()}
        required = [
            "word.lib", "sentlib.lib", "activemode.lib", "defindex.lib",
            "unifyperc.lib", "egperc.lib", "jibenjl.lib", "rule.lib",
            "attribe.lib", "eltest.lib", "defl0index.lib", "defl0data.lib",
        ]
        found = [f for f in required if f in present]
        self.assertEqual(
            found, [],
            f"居然出现了 {found}——可以重新尝试跑原始引擎了，"
            "记得更新 docs/hnc-engine-probe.md §0",
        )
        self.assertIn("falunword.lib", present, "连 FalunWord.lib 都没了？")


@unittest.skipUnless(DLL.exists(), "安装树不在预期位置")
class GetSentenceALCLengthCapTest(unittest.TestCase):
    """`GetSentenceALC` 有 35 字符硬上限。

    这对本项目是个旁证：原始引擎按「句」设计，喂整段会被截断。
    我们复刻时坚持先分句是对的。
    """

    def test_cap_is_0x23(self):
        data = DLL.read_bytes()
        # 0x6dd2: 83 fe 23    cmp esi, 0x23
        self.assertEqual(data[0x6DD2:0x6DD5], _h("83fe23"))
        # 紧跟其后应该是：pop edi; pop esi; mov eax,7; pop ebx; ...; ret 0x0c
        tail = data[0x6DD5:0x6DE7]
        self.assertEqual(
            tail, _h("894c240c7c0c5f5eb8070000005b59c20c00"),
            "超长分支：mov [esp+0xc],ecx; jl; pop edi; pop esi; mov eax,7; ...; ret 0xc",
        )
        # 上限就写在指令里：35 个字符
        self.assertEqual(struct.unpack("<B", data[0x6DD4:0x6DD5])[0], 35)

    def test_makeobjlist_also_clamps(self):
        """`MakeObjList` 里还有 509 的钳制，两道限制互相独立。"""
        data = DLL.read_bytes()
        # 0x69d1: 81 ff fd 01 00 00   cmp edi, 0x1fd
        self.assertEqual(data[0x69D1:0x69D7], _h("81fffd010000"))
        self.assertEqual(
            struct.unpack("<I", data[0x69D3:0x69D7])[0], 509,
            "MakeObjList 的长度钳制应为 509",
        )


class IniCheckpointConstantTest(unittest.TestCase):
    """工具里的检查点清单要和文档一致，防止文档/代码漂移。"""

    def test_tool_declares_all_checkpoints(self):
        import sys as _sys
        if str(ROOT / "tools") not in _sys.path:
            _sys.path.insert(0, str(ROOT / "tools"))
        try:
            import call_hnc_engine as mod
        except ImportError:
            self.skipTest("tools/call_hnc_engine.py 不可导入")
        labels = {label for label, _f, _s in mod.REQUIRED_DATA_FILES}
        for expected in ("WordLib Ini Err!", "SentLib Ini Err!",
                         "ActiveRule Ini Err!", "EgPerc Ini Err!"):
            self.assertIn(expected, labels)
        # 只有 FalunWord.lib 在包里
        shipped = [f for _l, f, ok in mod.REQUIRED_DATA_FILES if ok]
        self.assertEqual(len(shipped), 1, f"只有 1 个文件随包发布，得到 {shipped}")

    def test_length_cap_matches(self):
        import sys as _sys
        if str(ROOT / "tools") not in _sys.path:
            _sys.path.insert(0, str(ROOT / "tools"))
        try:
            import call_hnc_engine as mod
        except ImportError:
            self.skipTest("tools/call_hnc_engine.py 不可导入")
        self.assertEqual(mod.GETSENTENCEALC_MAX_CHARS, 35)


class EngineRuntimeTest(unittest.TestCase):
    r"""真跑一遍引擎——但只在 32 位 Python 下。

    **这个类在本仓库默认的 64 位解释器下永远 skip**，因为
    test_hnc_engine.py 这个文件整体用了 3.x 语法（函数注解），
    2.7 连导入都做不到。

    运行时验证请直接跑脚本：

        D:\Python27\python.exe tools\call_hnc_engine.py

    （上面这个 docstring 的 r 前缀是必需的：这段路径含反斜杠，
    非 raw 字符串会触发 `SyntaxWarning: invalid escape sequence`。）

    那里会打印建组结果、计数接口、MakeALC 返回码，并断言
    「路径带尾反斜杠才成功」这件事。
    """

    def test_runtime_is_covered_by_tool_script(self):
        """把"运行时验证在哪"这件事本身钉住。

        没有这一步，后来的人会以为 test_hnc_engine.py 覆盖了运行时行为，
        实际上它只做静态 PE 读取。
        """
        tool = ROOT / "tools" / "call_hnc_engine.py"
        self.assertTrue(tool.exists(), "运行时验证脚本不见了")
        body = tool.read_text(encoding="utf-8")
        # 这三件事是脚本必须证明的
        self.assertIn("trailing_backslash", body,
                      "脚本必须演示「路径要带尾反斜杠」")
        self.assertIn('path += "\\\\"', body,
                      "加尾反斜杠那行不见了")
        self.assertIn("trailing_backslash", body)
        self.assertIn("global_obj", body,
                      "脚本必须演示主对象/子对象差 0x124")
        self.assertIn("self.base + 0x28D0", body,
                      "必须用实际基址，不能硬编码 0x100028D0")


if __name__ == "__main__":
    unittest.main()
