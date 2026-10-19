# -*- coding: utf-8 -*-
"""实测：原厂那套「不完整 HNC」在真实语料上到底能拦多少。

## 这是什么

绿坝的 HNC 引擎里，句类分析是死代码（见 `docs/engine-call-graph.md`）。
实际起作用的只有两样东西：

1. `FalunWord.lib` 的 trie（272,920 节点）
2. 打分表（4,693 条，每条带反动度/红黑度）

所以「原厂过滤能力」= 这两样东西的组合。本脚本测它的真实效果。

## 测什么

语料是两类：

- **正样本**：`data/eval/positive/` —— 手工构造的、应当被拦的句子
  （法轮功/邪教相关，来自打分表里的真实词条）
- **负样本**：`D:\\Docs\\_w\\小说` —— 真实小说，应当放行

因为 `FalunWord.lib` 是**法轮功专用词库**，不能用成人小说做正样本
（那是领域外，0 命中是必然的，不说明问题）。所以正样本用构造句。

## 三种过滤策略对照

| 策略 | 做法 | 说明 |
|---|---|---|
| `trie_all` | trie 里查到就算命中 | 天真做法，已验证会误报爆炸 |
| `trie_len2` | trie 里 ≥2 字的词算命中 | 去掉单字中间节点 |
| `scored` | 只有打分表命中才算 | 需要 SearchWord 的 out>=0 |

## 为什么不用引擎实测而用导出数据

引擎是 32 位 DLL，逐位置调用 `SearchWord` 太慢（语料 47M 字）。
`data/hnc-falun-filter.json` 已经从引擎导出并交叉校验过（4,683/4,693
往返一致，见 `tools/probe_searchword.py`），用它做等价的纯 Python 模拟。
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILTER_JSON = ROOT / "data" / "hnc-falun-filter.json"
LEGACY_JSON = ROOT / "data" / "legacy-imported" / "falun-terms.json"

#: 正样本：应当被原厂词库拦下的句子。
#: 全部由打分表里的真实词条构成，不是凭空编的。
POSITIVE = [
    "大纪元时报今天发表了社论",
    "明慧网消息称有新的进展",
    "他是法轮功学员",
    "退党保平安，快去看九评",
    "610办公室下达了通知",
    "真善忍好，法轮大法好",
    "全球公审江泽民",
    "天安门自焚案是栽赃",
    "《转法轮》这本书到处都在传",
    "新唐人电视台播出了节目",
]

#: 负样本：完全无害的日常句子。
NEGATIVE = [
    "今天天气不错，我们一起去公园看书吧",
    "他在公司负责财务工作，同事都很喜欢他",
    "这本书讲述了主人公一生的经历",
    "公司决定下周开始实施新方案",
    "妈妈让孩子早点睡觉，明天还要上学",
    "列车晚点了半个小时，旅客们有些着急",
    "研究表明这种材料在高温下会发生变化",
    "她每天早上六点起床跑步锻炼身体",
    "会议讨论了明年的预算安排",
    "医生建议他多休息，少吃油腻的食物",
]


def load_terms() -> dict[str, int]:
    """从旧解析数据里取 trie 词条 -> 最短长度标记。"""
    data = json.loads(LEGACY_JSON.read_text(encoding="utf-8"))
    return {t: 0 for t in data["terms"]}


def load_scored() -> list[dict]:
    return json.loads(FILTER_JSON.read_text(encoding="utf-8"))["entries"]


class Matcher:
    """三种策略的字面最长匹配。"""

    def __init__(self, terms: dict[str, int], entries: list[dict]):
        self.all_terms = terms
        self.len2_terms = {t for t in terms if len(t) >= 2}
        self.scored = sorted(
            {e["word"] for e in entries}, key=len, reverse=True)
        self.by_len = {
            "trie_all": _bucket(terms),
            "trie_len2": _bucket(self.len2_terms),
            "scored": _bucket(self.scored),
        }

    def scan(self, text: str, mode: str) -> list[str]:
        buckets = self.by_len[mode]
        lens = sorted(buckets, reverse=True)
        hits = []
        i = 0
        n = len(text)
        while i < n:
            found = None
            for L in lens:
                if L > n - i:
                    continue
                sub = text[i:i + L]
                if sub in buckets[L]:
                    found = sub
                    break
            if found:
                hits.append(found)
                i += len(found)
            else:
                i += 1
        return hits


def _bucket(words) -> dict[int, set[str]]:
    out: dict[int, set[str]] = {}
    for w in words:
        out.setdefault(len(w), set()).add(w)
    return out


def evaluate(matcher: Matcher, mode: str,
             positives: list[str], negatives: list[str]) -> dict:
    pos_hit = 0
    pos_detail = []
    for s in positives:
        hits = matcher.scan(s, mode)
        if hits:
            pos_hit += 1
        pos_detail.append((s, hits))
    neg_hit = 0
    neg_detail = []
    for s in negatives:
        hits = matcher.scan(s, mode)
        if hits:
            neg_hit += 1
        neg_detail.append((s, hits))
    return {
        "mode": mode,
        "pos_hit": pos_hit,
        "pos_total": len(positives),
        "neg_hit": neg_hit,
        "neg_total": len(negatives),
        "pos_detail": pos_detail,
        "neg_detail": neg_detail,
    }


def corpus_rate(matcher: Matcher, mode: str, folder: str,
                limit: int = 12) -> tuple[int, int, float]:
    """真实小说上的每千字命中。"""
    p = Path(folder)
    if not p.exists():
        return 0, 0, 0.0
    files = sorted(p.glob("*.txt"))[:limit]
    chars = 0
    hits = 0
    for f in files:
        raw = f.read_bytes()
        for enc in ("utf-8", "gbk"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", "ignore")
        chars += len(text)
        hits += len(matcher.scan(text, mode))
    rate = 1000.0 * hits / chars if chars else 0.0
    return hits, chars, rate


def main() -> int:
    terms = load_terms()
    entries = load_scored()
    matcher = Matcher(terms, entries)

    print("=" * 68)
    print("数据")
    print("=" * 68)
    print("trie 词条      : %d" % len(terms))
    print("trie >=2 字    : %d" % len(matcher.len2_terms))
    print("打分表         : %d" % len(entries))
    print("正样本句子     : %d" % len(POSITIVE))
    print("负样本句子     : %d" % len(NEGATIVE))
    print()

    print("=" * 68)
    print("构造句：三种策略对照")
    print("=" * 68)
    results = {}
    for mode in ("trie_all", "trie_len2", "scored"):
        r = evaluate(matcher, mode, POSITIVE, NEGATIVE)
        results[mode] = r
        print("%-10s  正样本命中 %2d/%2d   负样本误报 %2d/%2d"
              % (mode, r["pos_hit"], r["pos_total"],
                 r["neg_hit"], r["neg_total"]))
    print()
    for mode, r in results.items():
        print("[%s] 正样本详情：" % mode)
        for s, hits in r["pos_detail"]:
            mark = "命中" if hits else "漏掉"
            print("  %s %s  %s" % (mark, s, hits or ""))
        print()

    print("=" * 68)
    print("真实小说：每千字命中（误报强度）")
    print("=" * 68)
    for mode in ("trie_all", "trie_len2", "scored"):
        t0 = time.time()
        hits, chars, rate = corpus_rate(matcher, mode, r"D:\Docs\_w\小说",
                                        limit=3)
        print("%-10s  %d 字，命中 %d 次，%.3f /千字  (%.1fs)"
              % (mode, chars, hits, rate, time.time() - t0))
    print()

    print("=" * 68)
    print("结论")
    print("=" * 68)
    best = "scored"
    r = results[best]
    print("打分表策略：正样本 %d/%d，误报 %d/%d"
          % (r["pos_hit"], r["pos_total"], r["neg_hit"], r["neg_total"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
