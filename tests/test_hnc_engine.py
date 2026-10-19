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

from __future__ import annotations

import re
import struct
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
