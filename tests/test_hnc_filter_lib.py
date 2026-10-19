# -*- coding: utf-8 -*-
"""原始 HNC 引擎的过滤库：测试。

这组测试锁住一个**被推翻过的结论**：

> 早前 `docs/hnc-engine-probe.md` 写「引擎能调但给不出结果，不能当后端用」。
> 那是错的 —— 句类分析确实走不通（缺 12 个 .lib），但**过滤完全可用**。

`GetWordFanDong`（0x2d80）/ `GetWordHongHei`（0x2d50）是纯内存查表，
只依赖 `FalunWord.lib`，不经过 `CSentenceObj::Ini`。

运行时验证必须用 32 位 Python 2.7 跑 `tools/dump_falun_filter.py`；
本文件在 64 位下只做静态断言 + 导出数据校验。
"""

from __future__ import annotations

import json
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DLL = ROOT.parent / "LH-installed-tree" / "Windows" / "SentenceObj.dll"
FILTER_JSON = ROOT / "data" / "hnc-falun-filter.json"


def _h(hexstr: str) -> bytes:
    """把十六进制串转 bytes。

    不能用 bytes.fromhex（3.0+ 才有），也不能写 b"\\x.."（中文环境
    下的源码编码容易坏掉，且会引入真实 NUL 字节）。
    """
    return bytes(bytearray.fromhex(hexstr))


@unittest.skipUnless(DLL.exists(), "安装树不在预期位置")
class FilterFunctionsAreMemoryOnlyTest(unittest.TestCase):
    """两个过滤函数的反汇编里不能出现任何 call 文件 I/O。

    这是「过滤能工作」的根本原因：它根本不碰磁盘。
    """

    FANDONG = 0x2D80
    HONGHEI = 0x2D50

    def test_fandong_reads_field_at_offset_8(self):
        data = DLL.read_bytes()
        body = data[self.FANDONG:self.FANDONG + 0x30]
        # mov eax,[esp+4] / cmp eax,[ecx+0x12c] / jge / test / jl
        self.assertEqual(body[:4], _h("8b442404"))
        self.assertEqual(body[4:10], _h("3b812c010000"))
        # 核心：shl eax,4 然后 mov eax,[ecx+eax+8]
        self.assertIn(_h("c1e004"), body)
        self.assertIn(_h("8b440108"), body, "反动度字段应在 +8")
        self.assertTrue(body.endswith(_h("33c0c20400")) or _h("c20400") in body)

    #: 两个函数体的完整机器码（实测 32 字节）。
    #: 唯一差别是取字段的位移：反动度 +8，红黑度 +0xc。
    FANDONG_BODY = "8b442404 3b812c010000 7d14 85c0 7c10 8b8928010000 c1e004 8b440108 c20400"
    HONGHEI_BODY = "8b442404 3b812c010000 7d14 85c0 7c10 8b8928010000 c1e004 8b44010c c20400"

    def test_honghei_is_identical_except_field_offset(self):
        """`GetWordHongHei` 与 `GetWordFanDong` 只差字段偏移 +8 -> +0xc。

        这种「同一函数改一个位移」的模式，正好印证它们是同一张表的
        两个字段——也说明我把两个分数分开处理是对的。
        """
        data = DLL.read_bytes()
        fd = _h(self.FANDONG_BODY.replace(" ", ""))
        hh = _h(self.HONGHEI_BODY.replace(" ", ""))
        self.assertEqual(len(fd), len(hh))
        self.assertNotEqual(fd, hh, "两个函数不应完全相同")

        # 实测字节必须与文件里的完全一致
        self.assertEqual(data[self.FANDONG:self.FANDONG + len(fd)], fd)
        self.assertEqual(data[self.HONGHEI:self.HONGHEI + len(hh)], hh)

        # 差异只允许出现在字段位移那一处
        diff = [i for i in range(len(fd)) if fd[i] != hh[i]]
        self.assertEqual(diff, [28], f"不应有别的差异：{diff}")
        self.assertEqual(fd[28], 0x08, "反动度字段在 +8")
        self.assertEqual(hh[28], 0x0C, "红黑度字段在 +0xc")

    def test_no_file_io_in_either_function(self):
        """函数体里不能有 call（文件 I/O 必有 call）。"""
        data = DLL.read_bytes()
        for name, rva in (("GetWordFanDong", self.FANDONG),
                          ("GetWordHongHei", self.HONGHEI)):
            with self.subTest(name=name):
                body = data[rva:rva + 0x20]
                self.assertNotIn(_h("e8"), body,
                                 f"{name} 里出现 call —— 说明它不只查内存")
                self.assertNotIn(_h("ff15"), body,
                                 f"{name} 里出现 call [mem] —— 说明它不只查内存")

    def test_field_offsets_match_documented_table(self):
        """打分表每条 16 字节，字段在 +8/+12。

        `shl eax,4` 就是这个 16 的由来。
        """
        data = DLL.read_bytes()
        for rva in (self.FANDONG, self.HONGHEI):
            body = data[rva:rva + 0x20]
            self.assertIn(_h("c1e004"), body,
                          "打分表条目大小应为 16（shl eax,4）")
            # shl 之后紧跟取字段，确认「先乘 16 再取值」是同一条链
            pos = body.index(_h("c1e004"))
            self.assertIn(_h("8b4401"), body[pos:pos + 6])


@unittest.skipUnless(FILTER_JSON.exists(), "还没导出过滤库")
class ExportedFilterLibraryTest(unittest.TestCase):
    """校验 `data/hnc-falun-filter.json` 的完整性。

    这些数字都是 `tools/dump_falun_filter.py` 实测出来的锚点，
    改动数据时应当同步更新。
    """

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(FILTER_JSON.read_text(encoding="utf-8"))
        cls.entries = cls.data["entries"]

    def test_entry_count(self):
        self.assertEqual(len(self.entries), 4693)

    def test_object_field_counts(self):
        counts = self.data["counts"]
        self.assertEqual(counts["score"], 4693)
        self.assertEqual(counts["text"], 36943)
        self.assertEqual(counts["charnode"], 272920)

    def test_every_entry_has_a_word(self):
        """4693 条**全部**有词条文本，没有空条。

        如果这里出现空词，说明词条切分（按位置差）退化了。
        """
        empty = [e for e in self.entries if not e["word"]]
        self.assertEqual(empty, [], f"{len(empty)} 条词条为空")

    def test_fandong_histogram(self):
        hist = {}
        for e in self.entries:
            hist[e["fandong"]] = hist.get(e["fandong"], 0) + 1
        self.assertEqual(
            hist,
            {0: 4177, 1: 347, 2: 4, 3: 45, 4: 41, 5: 21, 6: 53, 7: 5},
        )
        self.assertEqual(sum(hist.values()), 4693)

    def test_honghei_histogram(self):
        hist = {}
        for e in self.entries:
            hist[e["honghei"]] = hist.get(e["honghei"], 0) + 1
        self.assertEqual(hist, {0: 1, 1: 146, 2: 256, 3: 617, 4: 3673})

    def test_scores_are_bounded(self):
        for e in self.entries:
            self.assertGreaterEqual(e["fandong"], 0)
            self.assertLessEqual(e["fandong"], 7)
            self.assertGreaterEqual(e["honghei"], 0)
            self.assertLessEqual(e["honghei"], 4)

    def test_most_entries_are_active(self):
        """绝大多数条目真的带分，不是占位。

        这是「过滤库有效」最直接的量化证据。
        """
        nonzero_hh = sum(1 for e in self.entries if e["honghei"] > 0)
        self.assertEqual(nonzero_hh, 4692, "红黑度 > 0 的条数")
        nonzero_fd = sum(1 for e in self.entries if e["fandong"] > 0)
        self.assertEqual(nonzero_fd, 516, "反动度 > 0 的条数")

    def test_content_matches_known_targets(self):
        """词条内容确实是针对法轮功相关的，不是通用脏话表。

        这不是价值判断，是确认我们导出的就是那套东西。
        """
        words = "\n".join(e["word"] for e in self.entries)
        self.assertIn("法轮功", words)
        self.assertIn("大纪元", words)
        self.assertIn("明慧", words)
        self.assertIn("610", words)

        # 最高分（反动度 7）里应有明确的境外站点/媒体名
        top = [e["word"] for e in self.entries if e["fandong"] == 7]
        self.assertEqual(len(top), 5)
        joined = "|".join(top)
        self.assertTrue(
            "epochtimes" in joined or "大纪元" in joined or "明慧" in joined,
            f"反动度 7 的条目内容异常：{top}",
        )

    def test_words_have_sane_lengths(self):
        """词长 1..26。超过 30 字说明切分串条了。"""
        lens = [len(e["word"]) for e in self.entries]
        self.assertEqual(min(lens), 1)
        self.assertEqual(max(lens), 26)

    def test_attested_flag_is_false(self):
        """数值语义没有公开文档，必须标注未证实。

        这是本项目的诚实边界惯例：搬运的东西要标清楚。
        """
        self.assertIs(self.data.get("_attested"), False)
        self.assertIn("_note", self.data)

    def test_indices_are_dense_and_ordered(self):
        """score 表的下标必须是 0..4692 连续，不允许跳号。"""
        idx = [e["index"] for e in self.entries]
        self.assertEqual(idx, list(range(4693)))


class DumpToolTest(unittest.TestCase):
    """导出脚本存在的意义是「可复现」，这里锁住它的关键约束。"""

    TOOL = ROOT / "tools" / "dump_falun_filter.py"

    def test_tool_exists_and_documents_python27(self):
        text = self.TOOL.read_text(encoding="utf-8")
        self.assertIn("Python 2.7", text)
        self.assertIn("32 位", text)

    def test_tool_knows_the_trailing_backslash_trap(self):
        text = self.TOOL.read_text(encoding="utf-8")
        # CreateHncTextGroup 的路径必须带尾反斜杠，否则返回 NULL
        self.assertIn('hnc + "\\\\"', text)

    def test_tool_uses_actual_load_base(self):
        """函数地址必须用 dll._handle + RVA，不能硬编码 0x10000000。"""
        text = self.TOOL.read_text(encoding="utf-8")
        self.assertIn("self.dll._handle + fn_rva", text)
        # 文档里可以提 0x10000000（作为「不能硬编码」的反例），
        # 但代码里不能真的用它算地址
        code_lines = [ln for ln in text.splitlines()
                      if not ln.lstrip().startswith("#")
                      and not ln.lstrip().startswith("#:")
                      and "硬编码" not in ln and "不能" not in ln]
        self.assertFalse([ln for ln in code_lines if "0x10000000" in ln],
                         "代码里不能硬编码基址 0x10000000")

    def test_tool_uses_main_object_not_sub_object(self):
        """this 必须传主对象（子对象 - 0x124）。"""
        text = self.TOOL.read_text(encoding="utf-8")
        self.assertIn("SUB_OBJ_DELTA", text)
        self.assertIn("sub - SUB_OBJ_DELTA", text)

    def test_tool_documents_word_split_by_position_difference(self):
        """词条长度 = 与下一条的位置差，不能靠终止符。"""
        text = self.TOOL.read_text(encoding="utf-8")
        self.assertIn("位置差", text)


if __name__ == "__main__":
    unittest.main()
