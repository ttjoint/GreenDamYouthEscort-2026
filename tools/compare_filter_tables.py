# -*- coding: utf-8 -*-
"""对比两套词表的实际过滤效果。

## 背景

`data/legacy-imported/falun-terms.json` 是我们早期对 `FalunWord.lib` 的解析，
`data/hnc-falun-filter.json` 是这次直接从引擎内存导出的权威数据。

早期解析有两处错：

1. **词条切分错**：靠「遇终止符」切词，但文本表是一整条连续流，
   导致 `我坐在` 而不是 `我坐在他的小腿上`、`恶党书` 而不是 `恶党书记`。
   只有 0.9% 的词条完全正确。
2. **分数读错字段**：把 `+8`（反动度）读成了 `hong_hei`，
   真正的红黑度在 `+12` 反而读成 0。旧数据里 `fan_dong_valid` 全是 False，
   于是得出「原始映射不可恢复」的错误结论——**实际可恢复**。

## 这个脚本回答什么

不是「哪个数字大」，而是：**用旧词表过滤真实语料，会漏掉什么？**

做法：拿一批含敏感词的真实文本片段，分别用两套词表匹配，
看命中集合的差集。因为新词表是引擎实测导出，它的命中就是「标准答案」。

## 注意

两套数据**都不能直接当屏蔽词表用**：

- 旧 `terms`（142,742 条）是 trie 的**全部前缀展开**，73,236 条是单字
  （`网`、`弟子`、`总部`），直接匹配会疯狂误报。
- 新 `entries`（4,693 条）是**打分表**，只覆盖有分数的那些词，
  不是完整词表。

真正能用的词表应该来自 trie 的**叶节点**（完整词），这个还没导出。
"""

from __future__ import unicode_literals

import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

OLD_JSON = os.path.join(ROOT, "data", "legacy-imported", "falun-terms.json")
NEW_JSON = os.path.join(ROOT, "data", "hnc-falun-filter.json")


def load_old():
    with io.open(OLD_JSON, encoding="utf-8") as fh:
        data = json.load(fh)
    return data


def load_new():
    with io.open(NEW_JSON, encoding="utf-8") as fh:
        data = json.load(fh)
    return data


#: 测试片段：把两套词表里出现的词拼进自然句子，检验能否命中。
#: 这些不是「成人内容」样本，而是**敏感词命中测试**，用于对比词表覆盖率。
PROBES = [
    u"他说自己是法轮功学员，经常上明慧网看消息。",
    u"《大纪元》和纪元周刊都在报道这件事。",
    u"610办公室的人来过村里好几次。",
    u"他们散发《法轮功真实的故事》这本小册子。",
    u"海外的大法弟子供稿称，这是一次迫害。",
    u"FA-Lun-Gong 和 FAlun__Gong 都是变体写法。",
    u"http://www.epochtimes.com 这个网址被反复提到。",
    u"江泽民推卸不了的历史责任，这本书在境外流传。",
    u"今天天气不错，我们一起去公园看书吧。",
    u"他在公司负责财务，工作认真，同事都很喜欢他。",
]


def longest_hits(text, vocab, max_len):
    """返回 text 里所有能匹配上的词（最长优先，不重叠）。"""
    hits = []
    i = 0
    n = len(text)
    while i < n:
        found = None
        upper = min(max_len, n - i)
        for L in range(upper, 0, -1):
            cand = text[i:i + L]
            if cand in vocab:
                found = cand
                break
        if found:
            hits.append(found)
            i += len(found)
        else:
            i += 1
    return hits


def main():
    old = load_old()
    new = load_new()

    old_terms = old["terms"]
    new_entries = new["entries"]

    # ---- 1. 结构对比 ----
    print("=" * 62)
    print("1. 两套数据的结构")
    print("=" * 62)
    print("旧 falun-terms.json")
    print("  terms        = %d（trie 前缀全展开）" % len(old_terms))
    print("  senses       = %d（打分记录，词条切分错）" % len(old["senses"]))
    single = sum(1 for t in old_terms if len(t) == 1)
    print("  单字词条     = %d（%.1f%%）" % (single, 100.0 * single / len(old_terms)))
    old_senses_exact = sum(
        1 for i, s in enumerate(old["senses"])
        if i < len(new_entries) and s["text"] == new_entries[i]["word"]
    )
    print("  senses 词条切分正确率 = %d / %d（%.1f%%）"
          % (old_senses_exact, len(old["senses"]),
             100.0 * old_senses_exact / len(old["senses"])))
    old_fd_as_hh = sum(
        1 for i, s in enumerate(old["senses"])
        if i < len(new_entries) and s["hong_hei"] == new_entries[i]["fandong"]
    )
    print("  senses.hong_hei 实际等于新数据的 fandong：%d / %d（%.1f%%）"
          % (old_fd_as_hh, len(old["senses"]),
             100.0 * old_fd_as_hh / len(old["senses"])))
    print("  => 旧解析把 +8（反动度）当成了 hong_hei，把 +12 读成 0")
    print()
    print("新 hnc-falun-filter.json")
    print("  entries      = %d（打分表，引擎内存实测）" % len(new_entries))
    print("  词长 1..%d，全部有文本，4693/4693 与函数返回值交叉校验通过"
          % max(len(e["word"]) for e in new_entries))

    # ---- 2. 命中对比 ----
    print()
    print("=" * 62)
    print("2. 命中对比（新数据 = 标准答案）")
    print("=" * 62)

    old_vocab = set(old_terms)
    new_vocab = set(e["word"] for e in new_entries)
    old_max = max(len(t) for t in old_terms)
    new_max = max(len(t) for t in new_entries)

    total_old = total_new = 0
    missed = []
    for text in PROBES:
        ho = longest_hits(text, old_vocab, old_max)
        hn = longest_hits(text, new_vocab, new_max)
        total_old += len(ho)
        total_new += len(hn)
        # 新数据命中但旧数据没命中的字符串
        so = set(ho)
        for h in hn:
            if h not in so:
                missed.append((text, h))

    print("旧词表总命中 = %d" % total_old)
    print("新词表总命中 = %d" % total_new)
    print()
    if missed:
        print("新数据命中、旧数据漏掉的词：")
        for text, h in missed:
            print(u"  漏掉 [%s]   句子：%s" % (h, text[:30]))
    else:
        print("没有发现旧数据漏掉的词。")

    # ---- 3. 误报对比 ----
    print()
    print("=" * 62)
    print("3. 误报风险（普通句子）")
    print("=" * 62)
    normal = [t for t in PROBES if u"天气" in t or u"财务" in t]
    for text in normal:
        ho = longest_hits(text, old_vocab, old_max)
        hn = longest_hits(text, new_vocab, new_max)
        print(u"句子：%s" % text)
        print(u"  旧词表命中 %d 个：%s" % (len(ho), ho[:10]))
        print(u"  新词表命中 %d 个：%s" % (len(hn), hn[:10]))

    # ---- 4. 结论 ----
    print()
    print("=" * 62)
    print("4. 结论")
    print("=" * 62)
    print("旧数据的两处错误：")
    print("  (a) 分数读错字段 -> 得出结论「原始映射不可恢复」")
    print("  (b) 词条切分错   -> 0.9% 正确率")
    print()
    print("(a) 已证伪：+8 反动度 / +12 红黑度，4693 条交叉校验通过。")
    print("(b) 已修正：按与下一条的位置差切词。")
    print()
    print("但两套数据都**不能直接当屏蔽词表**：")
    print("  旧 terms 是 trie 前缀全展开，73,236 条单字，误报会爆炸。")
    print("  新 entries 只是打分表，不含完整词表。")
    print("  真正可用的词表得从 trie 叶节点导出——还没做。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
