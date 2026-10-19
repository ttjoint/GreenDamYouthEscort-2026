# -*- coding: utf-8 -*-
"""从原始 HNC 引擎里导出「反动度 / 红黑度」过滤表。

**必须用 32 位 Python 2.7**：
    D:\\Python27\\python.exe -u tools/dump_falun_filter.py

## 背景（这是一次重要的结论修正）

`docs/hnc-engine-probe.md` 早前写道「引擎能调但给不出结果，所以不能当
分析后端」。那句话**只对句类分析成立**，对**过滤**不成立——
过滤走的是另一条路，只依赖 `FalunWord.lib`，而它随包发布。

### 两条互不相干的路

```
句类分析：CSentenceObj::Ini  →  需要 12 个 .lib（包里一个都没有）→ 死路
词条过滤：CreateHncTextGroup →  只需要 FalunWord.lib（包里有）    → 活路
```

`GetWordFanDong` / `GetWordHongHei` 是 **`CFWordLib` 的成员函数**，
不碰 `CSentenceObj`，所以那 12 个缺失文件跟它们没关系。

### 反汇编依据

`?GetWordFanDong@CFWordLib@@QAEHH@Z` @0x2d80：

```asm
8b 44 24 04        mov  eax, [esp+4]          ; eax = idx
3b 81 2c 01 00 00  cmp  eax, [ecx+0x12c]      ; cmp idx, count
7d 14              jge  ret0                  ; idx >= count -> 0
85 c0              test eax, eax
7c 10              jl   ret0                  ; idx < 0      -> 0
8b 89 28 01 00 00  mov  ecx, [ecx+0x128]      ; ecx = 打分表
c1 e0 04           shl  eax, 4                ; idx * 16（每条 16 字节）
8b 44 01 08        mov  eax, [ecx+eax+8]      ; ★ +8 = 反动度
c2 04 00           ret  4
ret0:
33 c0              xor  eax, eax
c2 04 00           ret  4
```

`?GetWordHongHei@...` @0x2d50 与它逐字节相同，只有字段偏移从 `+8`
换成 `+0xc`（即 `+12` = 红黑度）。

**纯内存查表，完全不做文件 I/O。**

### 对象里的四张表（`LoadLib` @0x22d0 填充）

| 字段 | 内容 | 条目数 | 每条字节 |
|---|---|---|---|
| `[G+0x128]` | 打分表（反动度 `+8`、红黑度 `+12`） | `[G+0x12c]` | 16 |
| `[G+0x134]` | **词条文本**（UTF-16 字符序列拼接） | `[G+0x138]` | 2 |
| `[G+0x140]` | 字符节点表 | `[G+0x144]` | 16 |
| `[G+0x148]` | 见 `LoadLib` @0x2470 | `[G+0x13c]` | — |

`G` = `CreateHncTextGroup()` 返回值 `- 0x124`。

**关键**：打分表的 `u32[0]` 不是指针也不是字节偏移，而是**第 `+0x134`
号表的条目下标**（实测取值 0..36941，上限恰为 `[G+0x138]`=36943）。
`u32[1]` 是未初始化堆填充（`0xCDCDCDxx`），无意义。

## 产物

`data/hnc-falun-filter.json`：

```json
{
  "entries": [
    {"index": 17, "word": "法轮功", "fandong": 4, "honghei": 4},
    ...
  ]
}
```

字段含义（**这是推断，非原文点名**，见 `attested` 标注）：

- `fandong`（反动）0..7 —— 越高越"反动"，0 表示不在黑名单。
- `honghei`（红黑）0..4 —— 红/黑名单等级。

两者都**没有**公开文档说明其具体语义，数值只是原样搬运。
"""

from __future__ import print_function

import ctypes
import io
import json
import os
import shutil
import struct
import sys
import tempfile

#: 对象字段偏移（相对主对象 G = CreateHncTextGroup() 返回值 - 0x124）
OFF_SCORE_TABLE = 0x128
OFF_SCORE_COUNT = 0x12C
OFF_TEXT_TABLE = 0x134
OFF_TEXT_COUNT = 0x138
OFF_CHARNODE_TABLE = 0x140
OFF_CHARNODE_COUNT = 0x144

#: 子对象相对主对象的位移
SUB_OBJ_DELTA = 0x124

#: 打分表每条 16 字节的字段偏移
FIELD_FANDONG = 8
FIELD_HONGHEI = 12

#: 词条文本的最大长度（防止越界读到垃圾）
MAX_WORD_LEN = 48

#: 一个词条的结束标志
_TEXT_TERMINATORS = (0x0000, 0xFFFD, 0xFFFF)


def _install_tree():
    """返回安装树里 Windows 目录，找不到抛错。"""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    cand = os.path.join(root, "LH-installed-tree", "Windows")
    if os.path.isdir(cand):
        return cand
    raise SystemExit("找不到 LH-installed-tree/Windows，请从项目根运行")


class FilterLibrary(object):
    """一个已初始化的原始 HNC 词库对象，可直接读过滤表。"""

    def __init__(self):
        src = _install_tree()
        self.tmpdir = tempfile.mkdtemp(prefix="hncfilter_")
        # 中文路径会让 LoadLibraryA 失败，所以整棵树拷到纯 ASCII 临时目录
        shutil.copy(os.path.join(src, "SentenceObj.dll"), self.tmpdir)
        hnc = os.path.join(self.tmpdir, "HNCLIB")
        os.makedirs(hnc)
        shutil.copy(
            os.path.join(src, "HNCLIB", "FalunWord.lib"),
            os.path.join(hnc, "FalunWord.lib"),
        )
        self._oldcwd = os.getcwd()
        os.chdir(self.tmpdir)
        self.dll = ctypes.CDLL("SentenceObj.dll")
        create = self.dll["?CreateHncTextGroup@@YAPAVIHncTextGroup@@PAD@Z"]
        create.restype = ctypes.c_void_p
        # ★ 路径必须带尾反斜杠：内部 sprintf(buf, "%s\\FalunWord.lib", path)
        sub = create(ctypes.create_string_buffer(hnc + "\\"))
        if not sub:
            raise RuntimeError("CreateHncTextGroup 失败")
        self.global_obj = sub - SUB_OBJ_DELTA

    # ---- 内存读取 ----
    def _u32(self, addr):
        return ctypes.c_uint32.from_address(addr).value

    def _u16(self, addr):
        return ctypes.c_uint16.from_address(addr).value

    def counts(self):
        return {
            "score": self._u32(self.global_obj + OFF_SCORE_COUNT),
            "text": self._u32(self.global_obj + OFF_TEXT_COUNT),
            "charnode": self._u32(self.global_obj + OFF_CHARNODE_COUNT),
        }

    def word_at(self, index, end=None):
        """取文本表里 [index, end) 的一段。

        `end` 由调用方（下一条记录的起始下标）决定：这张表是 trie 展开，
        每条记录的**长度**就是与下一条的距离，不能靠遇终止符截断。
        """
        text_tbl = self._u32(self.global_obj + OFF_TEXT_TABLE)
        text_n = self._u32(self.global_obj + OFF_TEXT_COUNT)
        if end is None or end <= index:
            end = min(index + MAX_WORD_LEN, text_n)
        chars = []
        for pos in range(index, min(end, text_n)):
            w = self._u16(text_tbl + 2 * pos)
            if w in _TEXT_TERMINATORS:
                break
            chars.append(w)
        return u"".join(unichr(c) for c in chars)

    def entries(self):
        """产出 (index, word, fandong, honghei)。

        词条按 `text_index` 与下一条的**位置差**切分（见 `word_at`）。
        打分表的 `u32[0]` 是文本表里的**字符下标**，不是指针也不是字节偏移。
        """
        tbl = self._u32(self.global_obj + OFF_SCORE_TABLE)
        n = self._u32(self.global_obj + OFF_SCORE_COUNT)
        rows = []
        for i in range(n):
            base = tbl + i * 16
            rows.append((i, self._u32(base),
                         self._u32(base + FIELD_FANDONG),
                         self._u32(base + FIELD_HONGHEI)))
        text_n = self._u32(self.global_obj + OFF_TEXT_COUNT)
        for k, (i, start, fandong, honghei) in enumerate(rows):
            end = rows[k + 1][1] if k + 1 < len(rows) else text_n
            yield i, self.word_at(start, end), fandong, honghei

    def raw_text(self):
        """整张词条文本表，作为一条 unicode 串返回。"""
        text_tbl = self._u32(self.global_obj + OFF_TEXT_TABLE)
        n = self._u32(self.global_obj + OFF_TEXT_COUNT)
        chars = [self._u16(text_tbl + 2 * k) for k in range(n)]
        return u"".join(unichr(c) for c in chars if c not in _TEXT_TERMINATORS), n

    # ---- 直接调导出函数（交叉校验用） ----
    def _make_caller(self, fn_rva, index):
        """手写 thunk 调 `ret 4` 函数。

        不能用 CFUNCTYPE 直接调：被调方 `ret 4` 自己弹栈，
        ctypes 会再弹一次，导致栈失衡。
        """
        # 函数地址必须用实际加载基址 + RVA，不能硬编码 0x10000000
        fn = self.dll._handle + fn_rva
        code = (
            b"\x68" + struct.pack("<I", index)          # push index
            + b"\xb9" + struct.pack("<I", self.global_obj)  # mov ecx, this
            + b"\xb8" + struct.pack("<I", fn)           # mov eax, fn
            + b"\xff\xd0"                                # call eax
            + b"\xc3"                                    # ret
        )
        k32 = ctypes.windll.kernel32
        alloc = k32.VirtualAlloc
        alloc.restype = ctypes.c_void_p
        alloc.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32,
        ]
        buf = alloc(None, len(code), 0x3000, 0x40)  # MEM_COMMIT|RESERVE, RWX
        ctypes.memmove(buf, code, len(code))
        return ctypes.CFUNCTYPE(ctypes.c_int)(buf)

    def call_fandong(self, index):
        return self._make_caller(0x2D80, index)()

    def call_honghei(self, index):
        return self._make_caller(0x2D50, index)()

    def close(self):
        try:
            os.chdir(self._oldcwd)
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)


def main(argv):
    out_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "hnc-falun-filter.json",
    )
    if len(argv) > 1:
        out_path = argv[1]

    lib = FilterLibrary()
    try:
        counts = lib.counts()
        print("对象字段：")
        for k, v in sorted(counts.items()):
            print("  %-8s = %d" % (k, v))
        print()

        rows = list(lib.entries())
        print("导出 %d 条过滤记录" % len(rows))

        # 交叉校验：函数返回值必须和内存表逐条一致
        checked = 0
        for i, _word, fandong, honghei in rows:
            got_f = lib.call_fandong(i)
            got_h = lib.call_honghei(i)
            if got_f != fandong or got_h != honghei:
                raise SystemExit(
                    "第 %d 条不一致：表里 (%d,%d)，函数返回 (%d,%d)"
                    % (i, fandong, honghei, got_f, got_h)
                )
            checked += 1
        print("交叉校验：%d 条 GetWordFanDong/GetWordHongHei 与内存表一致" % checked)
        print()

        from collections import Counter
        fd_hist = Counter(r[2] for r in rows)
        hh_hist = Counter(r[3] for r in rows)
        print("反动度分布：%s" % dict(sorted(fd_hist.items())))
        print("红黑度分布：%s" % dict(sorted(hh_hist.items())))
        nonzero = sum(1 for r in rows if r[2] > 0)
        print("反动度 > 0 的条数：%d / %d" % (nonzero, len(rows)))
        print()

        payload = {
            "_source": "SentenceObj.dll + HNCLIB/FalunWord.lib",
            "_attested": False,
            "_note": (
                "fandong/honghei 的数值语义没有公开文档，此处仅原样搬运。"
                "数值来自 GetWordFanDong/GetWordHongHei 的返回值。"
            ),
            "counts": counts,
            "entries": [
                {"index": i, "word": w, "fandong": f, "honghei": h}
                for i, w, f, h in rows
            ],
        }
        # Python 2 的 json.dumps 遇到 dict 里混 unicode 会按 ascii 编码，
        # 所以先用 ensure_ascii=True 拿到纯 ASCII 的转义串，再解码写文件
        blob = json.dumps(payload, ensure_ascii=True, indent=1)
        with io.open(out_path, "w", encoding="utf-8") as fh:
            fh.write(blob.decode("ascii"))
        print("已写入 %s（%d 条）" % (out_path, len(rows)))

        print()
        print("样例（反动度 > 0）：")
        shown = 0
        for i, w, f, h in rows:
            if f > 0 and len(w) >= 2:
                sys.stdout.write(
                    (u"  反动=%d 红黑=%d  [%s]\n" % (f, h, w[:28])).encode("utf-8")
                )
                shown += 1
                if shown >= 15:
                    break
    finally:
        lib.close()

    print()
    print("结论：过滤库有效，共 %d 条；只依赖 FalunWord.lib，不依赖缺失的 12 个 .lib。"
          % len(rows))
    return 0


if __name__ == "__main__":
    if sys.version_info[0] != 2:
        raise SystemExit("必须用 32 位 Python 2.7 运行：D:\\Python27\\python.exe")
    sys.exit(main(sys.argv))
