"""诊断：HNC 分块器在真实中文语料上为什么退化。

## 背景

`bench_corpus.py` 已测出：成人语料 10 个文件、11,590 个分句里，
**72% 退化成单个 B 块**——也就是没有任何结构分析产出。
本脚本定位原因，并量化每种原因的占比。

## 为什么这比"判别力"更重要

一个在真实语料上 72% 不工作的模块，谈判别力没有意义。先把失败
模式分类清楚，才知道下一步该修词表、修规则，还是修架构。
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from bench_corpus import iter_corpus, read_text  # noqa: E402
from green_dam.chunker import SentenceChunker  # noqa: E402


@dataclass
class Diag:
    total: int = 0
    single_b: int = 0
    has_verb: int = 0
    no_verb: int = 0
    has_marker: int = 0
    reasons: Counter = field(default_factory=Counter)
    lens: list[int] = field(default_factory=list)

    def report(self, name: str) -> None:
        print(f"\n[{name}] 分句 {self.total:,}，单块退化 {self.single_b:,} "
              f"({self.single_b/max(self.total,1):.1%})")
        print(f"  其中「有谓语候选」 {self.has_verb:,} "
              f"({self.has_verb/max(self.single_b,1):.1%})")
        print(f"  其中「无谓语候选」 {self.no_verb:,} "
              f"({self.no_verb/max(self.single_b,1):.1%})")
        print(f"  其中「含 l 标记符」 {self.has_marker:,}")
        if self.lens:
            s = sorted(self.lens)
            n = len(s)
            print(f"  分句长度 中位 {s[n//2]}  均值 "
                  f"{sum(s)/n:.1f}  P90 {s[int(n*0.9)]}")
        print("  退化样本（前 12）：")
        for text, n in self.reasons.most_common(12):
            print(f"    ×{n:<5d} {text!r}")


def diagnose(chunker: SentenceChunker, root: Path, limit: int | None) -> Diag:
    d = Diag()
    for i, path in enumerate(iter_corpus(root)):
        if limit is not None and i >= limit:
            break
        text = read_text(path)
        if not text:
            continue
        for clause in re.split(r"[，。！？；\n、]+", text):
            clause = clause.strip()
            if not clause:
                continue
            d.total += 1
            d.lens.append(len(clause))
            ch = chunker.chunk(clause)
            if len(ch.chunks) != 1:
                continue
            d.single_b += 1
            if chunker._find_verbs_safe(clause):
                d.has_verb += 1
            else:
                d.no_verb += 1
            if any(chunker._is_boundary(clause, i)
                   for i in range(1, len(clause))):
                d.has_marker += 1
            d.reasons[clause[:28]] += 1
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--novels", default=r"D:\Docs\_w\小说")
    ap.add_argument("--adult", default=r"D:\Docs\_w\涩涩\限级、成人")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--extended", action="store_true",
                    help="使用语料抽取的扩展动词表（428 条）")
    args = ap.parse_args()

    c = SentenceChunker(extended=args.extended)
    dn = diagnose(c, Path(args.novels), args.limit)
    da = diagnose(c, Path(args.adult), args.limit)
    dn.report("普通小说")
    da.report("成人语料")


if __name__ == "__main__":
    main()
