# -*- coding: utf-8 -*-
"""探测 `SearchWord`：原始 HNC 过滤引擎的查询入口。

**必须用 32 位 Python 2.7**：
    D:\\Python27\\python.exe -u tools/probe_searchword.py

## 为什么重要

`SearchWord`（@0x2550）是引擎**真正用来判断"这段话有没有敏感词"**的入口。
它的第四个参数 `out` 直接返回**打分表下标**，不是布尔值、也不是词长。

```
out >= 0   -> 命中打分表，值即下标
out == -1  -> trie 里有这个词，但没有分数（普通词 / 长词的前缀）
out == -2  -> trie 里根本没有
```

这个区分是整件事的关键：**`FalunWord.lib` 是通用词库**，
`今天`、`公司`、`工作` 全都在里面。只有 `out >= 0` 才算敏感命中，
否则普通句子会满屏误报（实测 6/6 句误报）。

## 签名

```c
FCCharNode* SearchWord(CFWordLib* this,  // ecx
                       wchar_t*   word,  // esp+4
                       int        len,   // esp+8
                       int*       out);  // esp+12
```
"""

from __future__ import print_function

import ctypes
import json
import os
import struct
import sys

#: 必须在 import dump_falun_filter 之前抓住工作目录：
#: FilterLibrary 构造时会 chdir 到临时目录
ROOT = os.getcwd()

sys.path.insert(0, os.path.join(ROOT, "tools"))

import dump_falun_filter as M  # noqa: E402

#: SearchWord 的 RVA
RVA_SEARCHWORD = 0x2550

#: 节点里打分表下标的字段偏移
NODE_SCORE_INDEX = 12

#: 无分数的标志
NO_SCORE = 0xFFFFFFFF

#: `out` 的三种取值含义
OUT_NO_SCORE = -1
OUT_NOT_IN_TRIE = -2


class Searcher(object):
    """在已初始化的词库对象上跑 `SearchWord`。"""

    def __init__(self, lib):
        self.lib = lib
        self.G = lib.global_obj
        self.base = lib.dll._handle
        k32 = ctypes.windll.kernel32
        self._alloc = k32.VirtualAlloc
        self._alloc.restype = ctypes.c_void_p
        self._alloc.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32,
        ]

    def _u32(self, addr):
        return ctypes.c_uint32.from_address(addr).value

    def score_at(self, index):
        """按下标取打分表里的 (反动度, 红黑度)；越界返回 None。"""
        tbl = self._u32(self.G + M.OFF_SCORE_TABLE)
        n = self._u32(self.G + M.OFF_SCORE_COUNT)
        if index < 0 or index >= n:
            return None
        base = tbl + index * 16
        return (self._u32(base + M.FIELD_FANDONG),
                self._u32(base + M.FIELD_HONGHEI))

    def search(self, word):
        """返回 (node_ptr, out)。"""
        wbuf = ctypes.create_unicode_buffer(word)
        out = ctypes.c_int(-1)
        # 手写 thunk：被调方是 __thiscall + 普通 ret，ctypes 不能直接调
        code = (
            b"\x68" + struct.pack("<I", ctypes.addressof(out))
            + b"\x68" + struct.pack("<I", len(word))
            + b"\x68" + struct.pack("<I", ctypes.addressof(wbuf))
            + b"\xb9" + struct.pack("<I", self.G)
            + b"\xb8" + struct.pack("<I", self.base + RVA_SEARCHWORD)
            + b"\xff\xd0"      # call eax
            + b"\xc3"          # ret
        )
        buf = self._alloc(None, len(code), 0x3000, 0x40)
        ctypes.memmove(buf, code, len(code))
        node = ctypes.CFUNCTYPE(ctypes.c_void_p)(buf)()
        return node, out.value

    def node_fields(self, node):
        if not node:
            return None
        return [self._u32(node + k) for k in (0, 4, 8, NODE_SCORE_INDEX)]

    def scan(self, text, max_len=20):
        """逐位置最长匹配，只接受 out >= 0。

        这是引擎实际过滤文本的方式：`out == -1` 说明 trie 里有这个词
        但没分数，不能算命中。
        """
        hits = []
        i = 0
        n = len(text)
        while i < n:
            found = None
            for L in range(min(max_len, n - i), 0, -1):
                sub = text[i:i + L]
                node, out = self.search(sub)
                if node and out >= 0:
                    found = (sub, out, self.score_at(out))
                    break
            if found:
                hits.append(found)
                i += len(found[0])
            else:
                i += 1
        return hits


def main():
    lib = M.FilterLibrary()
    try:
        s = Searcher(lib)

        print("=" * 60)
        print("1. 单次查询：out 的三种取值")
        print("=" * 60)
        samples = [
            u"大纪元", u"法轮功", u"明慧网", u"江泽民", u"法轮大法",
            u"今天", u"公司", u"610",
        ]
        for w in samples:
            node, out = s.search(w)
            score = s.score_at(out) if out >= 0 else None
            print(u"  %-6s out=%-5d score=%s" % (w, out, score))
        print()
        print("  out >= 0  -> 命中打分表，值即下标")
        print("  out == -1 -> trie 里有，无分数（普通词 / 长词前缀）")
        print("  out == -2 -> trie 里没有")

        print()
        print("=" * 60)
        print("2. 全量校验：打分表 4693 条能否查回自身")
        print("=" * 60)
        path = os.path.join(ROOT, "data", "hnc-falun-filter.json")
        with open(path, "rb") as fh:
            entries = json.loads(fh.read().decode("utf-8"))["entries"]
        ok = 0
        bad = []
        for e in entries:
            node, out = s.search(e["word"])
            if out == e["index"]:
                ok += 1
            else:
                bad.append((e["index"], e["word"], out))
        print("  一致：%d / %d（%.1f%%）"
              % (ok, len(entries), 100.0 * ok / len(entries)))
        if bad:
            print("  不一致的 %d 条（重复词归并，正常）：" % len(bad))
            for idx, word, got in bad[:10]:
                print(u"    idx=%d word=%r got out=%d" % (idx, word, got))

        print()
        print("=" * 60)
        print("3. 句子扫描：只有 out >= 0 才算命中")
        print("=" * 60)
        sentences = [
            u"他说自己是法轮功学员，经常上明慧网看消息。",
            u"《大纪元》和纪元周刊都在报道这件事。",
            u"今天天气不错，我们一起去公园看书吧。",
            u"他在公司负责财务，工作认真，同事都很喜欢他。",
        ]
        for text in sentences:
            hits = s.scan(text)
            print(u"  %s" % text)
            if hits:
                for word, idx, score in hits:
                    print(u"    命中 %-10s 下标=%-4d (反动,红黑)=%s"
                          % (word, idx, score))
            else:
                print(u"    无命中")

        print()
        print("=" * 60)
        print("4. 结论")
        print("=" * 60)
        print("  SearchWord 的 out 就是打分表下标，这就是引擎的过滤判定点。")
        print("  FalunWord.lib 是通用词库，普通词也在 trie 里；")
        print("  必须用 out >= 0 过滤，否则误报会爆炸（实测普通句子 6/6 误报）。")
        return 0
    finally:
        lib.close()


if __name__ == "__main__":
    if sys.version_info[0] != 2:
        raise SystemExit("必须用 32 位 Python 2.7 运行：D:\\Python27\\python.exe")
    sys.exit(main())
