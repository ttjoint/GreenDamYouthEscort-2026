# -*- coding: utf-8 -*-
"""用原始工具的两层词库过滤真实语料。

## 已经搞清楚的结构

原始 HNC 词库是**两层**，不是一张平表：

```
第 1 层  trie 词条（142,742 条）    原子词：法轮功 / 邪教 / 明慧网 / 610
         ↓ 命中后继续找更长的组合
第 2 层  打分表（4,693 条）          组合短语：610歹徒 / "法轮功"弟子 / 4.25上访事件
         带 (反动度, 红黑度)
```

实测：**96.2% 的打分短语包含 trie 里的原子词**，两层确实是叠加关系。
`SearchWord`（@0x2550）就是按「先走 trie、命中后看 `+12` 是否指向打分表」来工作的。

## 第 1 层不能直接当词表用

`terms` 里 73,236 条是**单字**（`网`、`看`、`他`、`在`），因为它是 trie 的
**全部节点展开**，包括中间节点。直接拿来匹配，普通句子也会命中一大片。

所以这里分三种模式对比：

- `raw`：两层原样叠加（复现「直接用」的后果）
- `min2`：trie 只取长度 >= 2 的词（去掉单字）
- `phrases`：只用打分表（组合短语层）

## 判据

**漏检**比误报更严重。这里用真实成人语料做负样本、敏感词句做正样本。
"""

from __future__ import unicode_literals

import io
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

OLD_JSON = os.path.join(ROOT, "data", "legacy-imported", "falun-terms.json")
NEW_JSON = os.path.join(ROOT, "data", "hnc-falun-filter.json")

NOVEL_DIR = r"D:\Docs\_w\小说"
ADULT_DIR = r"D:\Docs\_w\涩涩\限级、成人"

STRIP = u"\"'()[]*"


def load_vocabs():
    old = json.load(io.open(OLD_JSON, encoding="utf-8"))
    new = json.load(io.open(NEW_JSON, encoding="utf-8"))

    trie = set(old["terms"])
    phrases = {e["word"]: (e["fandong"], e["honghei"]) for e in new["entries"]}

    return {
        "raw": trie | set(phrases),
        "min2": set(t for t in trie if len(t) >= 2) | set(phrases),
        "phrases": set(phrases),
        "trie2": set(t for t in trie if len(t) >= 2),
    }


def longest_hits(text, vocab, max_len, by_len=None):
    """最长优先、不重叠的匹配。

    `by_len` 是 {长度: 该长度的词集合}，预先按长度分桶。
    没有它的话，每个位置都要试 26 种长度并做 26 次哈希——
    在真实小说上会慢到跑不完。
    """
    if by_len is None:
        by_len = {}
        for w in vocab:
            by_len.setdefault(len(w), set()).add(w)
    lengths = sorted(by_len, reverse=True)
    hits = []
    i, n = 0, len(text)
    while i < n:
        found = None
        for L in lengths:
            if L > n - i:
                continue
            cand = text[i:i + L]
            if cand in by_len[L]:
                found = cand
                break
        if found:
            hits.append(found)
            i += len(found)
        else:
            i += 1
    return hits


def read_text(path):
    for enc in ("utf-8", "gbk", "utf-16"):
        try:
            with io.open(path, encoding=enc, errors="strict") as fh:
                return fh.read()
        except (UnicodeDecodeError, LookupError):
            continue
    with io.open(path, encoding="utf-8", errors="ignore") as fh:
        return fh.read()


def sample_files(dirpath, limit, seed):
    if not os.path.isdir(dirpath):
        return []
    files = [os.path.join(dirpath, f) for f in os.listdir(dirpath)
             if f.lower().endswith(".txt")]
    rnd = random.Random(seed)
    rnd.shuffle(files)
    return files[:limit]


def main():
    limit = 40
    for arg in sys.argv[1:]:
        if arg.startswith("--limit="):
            limit = int(arg.split("=", 1)[1])

    vocabs = load_vocabs()
    max_len = max(len(w) for v in vocabs.values() for w in v)
    print("词表规模：")
    for k in ("raw", "min2", "trie2", "phrases"):
        print("  %-8s %d 条，最长 %d 字"
              % (k, len(vocabs[k]), max(len(w) for w in vocabs[k])))
    print()

    # ---- 正样本：敏感词句 ----
    positive = [
        u"他说自己是法轮功学员，经常上明慧网看消息。",
        u"《大纪元》和纪元周刊都在报道这件事。",
        u"610办公室的人来过村里好几次。",
        u"他们散发《法轮功真实的故事》这本小册子。",
        u"海外的大法弟子供稿称，这是一次迫害。",
        u"FA-Lun-Gong 和 FAlun__Gong 都是变体写法。",
        u"http://www.epochtimes.com 这个网址被反复提到。",
        u"江泽民推卸不了的历史责任，这本书在境外流传。",
    ]
    # 负样本：完全无害的中文句子
    negative = [
        u"今天天气不错，我们一起去公园看书吧。",
        u"他在公司负责财务，工作认真，同事都很喜欢他。",
        u"这道菜要用小火慢炖两个小时才好吃。",
        u"明天上午九点在三楼会议室开会。",
        u"孩子放学回来说老师表扬了他。",
        u"火车晚点了四十分钟，我在站台上等。",
    ]

    print("=" * 64)
    print("A. 正样本：应该命中（漏检 = 危险）")
    print("=" * 64)
    for mode in ("raw", "min2", "phrases"):
        v = vocabs[mode]
        total = 0
        missed_sent = 0
        for text in positive:
            h = longest_hits(text, v, max_len)
            total += len(h)
            if not h:
                missed_sent += 1
        print("  %-8s 命中 %3d 个，完全漏检 %d / %d 句"
              % (mode, total, missed_sent, len(positive)))

    print()
    print("=" * 64)
    print("B. 负样本：不应该命中（误报 = 扰民）")
    print("=" * 64)
    for mode in ("raw", "min2", "phrases"):
        v = vocabs[mode]
        total = 0
        bad = 0
        for text in negative:
            h = longest_hits(text, v, max_len)
            total += len(h)
            if h:
                bad += 1
        print("  %-8s 命中 %3d 个，误报 %d / %d 句"
              % (mode, total, bad, len(negative)))

    print()
    print("=" * 64)
    print("C. 真实语料：每千字命中数（误报压力）")
    print("=" * 64)
    for label, dirpath in (("普通小说", NOVEL_DIR), ("成人内容", ADULT_DIR)):
        files = sample_files(dirpath, limit, seed=42)
        if not files:
            print("  %s：目录不存在，跳过（%s）" % (label, dirpath))
            continue
        for mode in ("raw", "min2", "phrases"):
            v = vocabs[mode]
            hit_chars = 0
            total_chars = 0
            hit_count = 0
            for path in files:
                try:
                    text = read_text(path)
                except Exception:
                    continue
                total_chars += len(text)
                h = longest_hits(text, v, max_len)
                hit_count += len(h)
                hit_chars += sum(len(x) for x in h)
            if total_chars:
                per_k = 1000.0 * hit_count / total_chars
                cov = 100.0 * hit_chars / total_chars
                print("  %-8s %-8s %d 文件 / %d 字：命中 %d 次，"
                      "%.2f 次/千字，覆盖 %.3f%% 字符"
                      % (label, mode, len(files), total_chars,
                         hit_count, per_k, cov))

    print()
    print("=" * 64)
    print("D. 结论")
    print("=" * 64)
    print("  两层结构已确认：trie 原子词 + 打分表组合短语。")
    print("  `raw` 模式（原样叠加）误报会爆炸——单字节点全在里面。")
    print("  正确用法是先用 trie 找原子词，再看能否升级为打分短语。")
    print("  单用打分表（phrases）会漏掉所有裸原子词。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
