"""用真实语料测 HNC 分块层与屏蔽系统的性能。

## 评测要回答什么

不是"HNC 分块器有多准"（没有金标准标注，测不了），而是三个可测的问题：

1. **性能**：分块器在真实中文小说上的吞吐是多少？能不能上线。
2. **分辨率**：`LegacySemanticMatcher`（字面词表）在成人语料上命中率如何？
   它能不能区分"小说"和"色情小说"两类语料。
3. **HNC 结构信号是否带来增量**：分块后，哪些**结构特征**在两类语料上
   有显著差异？这些特征能否作为正则/词表之外的补充判据。

## 为什么不直接算准确率

`semantic.py` 的 `FalunWord.lib` 是**法轮功**词表，和色情内容无关。
拿它测色情检测会是 0 命中——那是词库问题，不是模型问题。所以本脚本
分两层：

- **层 A**：法轮功词表在两类语料上的行为（验证它确实是领域外的）。
- **层 B**：不用任何词表，纯看 HNC 结构特征能否区分两类语料。

层 B 才是对 HNC 模块的公平测试。

## 诚实边界

- 语料没有人工标注。"成人语料 = 应拦截"是**目录标签**，不是内容标签：
  里面混着正统小说（如《中国古代十大禁书》含《金瓶梅》）。
- 层 B 给出的是**特征分布差异**，不是分类器准确率。要变成可用判据，
  还需要阈值调优和独立验证集，本脚本不做。
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam.chunker import SentenceChunker  # noqa: E402
from green_dam.semantic import LegacySemanticMatcher  # noqa: E402

#: 单文件最多读这么多字符。长篇小说动辄几 MB，全读会把评测时间
#: 堆在少数文件上，失去代表性。
MAX_CHARS_PER_FILE = 60_000

_ENCODINGS = ("utf-8", "gb18030", "utf-16", "big5")


def read_text(path: Path, limit: int = MAX_CHARS_PER_FILE) -> str | None:
    """尽力读出一段文本。编码错就换下一个，全失败返回 None。

    ## 实测到的编码情况

    中文小说语料里混着大量 GBK(GB18030) 文件，少数文件是
    **文件内混编码**或含罕见字，严格解码会在中途报错：

        story825.txt: gb18030 在字节 23532 处失败

    所以这里用 `errors="replace"` 宽容解码，然后看**替换率**判断
    是否可信。阈值 2%：正常文本替换率应为 0，超 2% 说明编码猜错。
    """
    try:
        raw = path.read_bytes()[: limit * 4]
    except OSError:
        return None
    best: str | None = None
    best_rate = 1.0
    for enc in _ENCODINGS:
        try:
            text = raw.decode(enc, errors="replace")
        except LookupError:
            continue
        if not text:
            continue
        rate = text.count("\ufffd") / len(text)
        if rate < best_rate:
            best, best_rate = text, rate
        if rate == 0:
            break
    if best is None or best_rate > 0.02:
        return None
    return best[:limit]


def iter_corpus(root: Path):
    """按文件名序遍历 .txt。"""
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() == ".txt":
            yield path


# ---------------------------------------------------------------------------
# 层 A：字面词表
# ---------------------------------------------------------------------------


@dataclass
class CorpusStat:
    """层 A 统计。

    ## `hits` 与 `blocked` 不是一回事（实测踩到的坑）

    `matcher.analyse()` 会把**任何**能匹配到的词条记进 `hits`，
    包括等级 0 的普通词。普通小说语料里命中最多的居然是：

        执政 218 / 时间 189 / 巨大 105 / 社会 74

    这些都来自 `FalunWord.lib` 的上下文窗口（该词条在**某些上下文**
    等级为 0，在另一些上下文等级升高），它们本身不是敏感词。
    所以层 A 的有效指标是 `blocked_files`（max_level > 0 的文件数）
    和 `blocked`，不是 `hits` 的长度。用 `hits` 会得出“普通小说比
    色情小说更敏感”的荒谬结论。
    """

    name: str
    files: int = 0
    chars: int = 0
    hit_files: int = 0
    hits: int = 0
    blocked_files: int = 0
    blocked_chars: int = 0
    level_hist: dict[int, int] = field(default_factory=dict)


def layer_a(matcher: LegacySemanticMatcher, root: Path, name: str,
            limit_files: int | None) -> CorpusStat:
    stat = CorpusStat(name)
    for i, path in enumerate(iter_corpus(root)):
        if limit_files is not None and i >= limit_files:
            break
        text = read_text(path)
        if not text:
            continue
        stat.files += 1
        stat.chars += len(text)
        v = matcher.analyse(text)
        stat.hits += len(v.hits)
        if v.hits:
            stat.hit_files += 1
        strong = v.max_level
        if strong > 0:
            stat.blocked_files += 1
            stat.blocked_chars += len(text)
            stat.level_hist[strong] = stat.level_hist.get(strong, 0) + 1
    return stat


# ---------------------------------------------------------------------------
# 层 B：HNC 结构特征
# ---------------------------------------------------------------------------

#: 结构特征。刻意只用**形状**，不用任何词表——
#: 否则就不是在测 HNC 层，而是在测又一张词表。
FEATURES = (
    "aux_ratio",      # 辅块数 / 块总数
    "e_ratio",        # E 块数 / 块总数
    "jk_per_e",       # 广义对象块 / E 块（句子复杂度）
    "ms_ratio",       # 方式辅块占辅块比
    "pr_ratio",       # 原因辅块占辅块比
    "cn_ratio",       # 条件辅块占辅块比
    "unknown_ratio",  # 无法判定块占块总数比
    "blk_len",        # 平均块长（字）
    "chk_per_sent",   # 每句块数
)


@dataclass
class StructStat:
    name: str
    files: int = 0
    chars: int = 0
    chunks: int = 0
    sentences: int = 0
    counter: dict[str, int] = field(default_factory=dict)
    blk_lens: list[int] = field(default_factory=list)

    def bump(self, key: str, n: int = 1) -> None:
        self.counter[key] = self.counter.get(key, 0) + n

    def features(self) -> dict[str, float]:
        total = max(self.chunks, 1)
        aux = sum(v for k, v in self.counter.items()
                  if k.startswith("aux:"))
        return {
            "aux_ratio": aux / total,
            "e_ratio": self.counter.get("E", 0) / total,
            "jk_per_e": (sum(v for k, v in self.counter.items()
                             if k in ("A", "B", "C"))
                         / max(self.counter.get("E", 0), 1)),
            "ms_ratio": self.counter.get("aux:Ms", 0) / max(aux, 1),
            "pr_ratio": self.counter.get("aux:Pr", 0) / max(aux, 1),
            "cn_ratio": self.counter.get("aux:Cn", 0) / max(aux, 1),
            "unknown_ratio": self.counter.get("?", 0) / total,
            "blk_len": (sum(self.blk_lens) / max(len(self.blk_lens), 1)),
            "chk_per_sent": self.chunks / max(self.sentences, 1),
        }


def layer_b(chunker: SentenceChunker, root: Path, name: str,
            limit_files: int | None) -> StructStat:
    stat = StructStat(name)
    for i, path in enumerate(iter_corpus(root)):
        if limit_files is not None and i >= limit_files:
            break
        text = read_text(path)
        if not text:
            continue
        stat.files += 1
        stat.chars += len(text)
        chunking = chunker.chunk(text)
        stat.sentences += text.count("。") + text.count("！") + text.count("？")
        for ch in chunking.chunks:
            stat.chunks += 1
            if ch.role.is_main:
                stat.bump(ch.role.value)
            else:
                stat.bump(f"aux:{ch.role.value}")
            stat.blk_lens.append(len(ch.text))
    return stat


# ---------------------------------------------------------------------------
# 吞吐
# ---------------------------------------------------------------------------


def throughput(chunker: SentenceChunker, root: Path, files: int = 20) -> None:
    texts = []
    for i, path in enumerate(iter_corpus(root)):
        if i >= files:
            break
        t = read_text(path, limit=30_000)
        if t:
            texts.append(t)
    total = sum(len(t) for t in texts)
    t0 = time.perf_counter()
    for t in texts:
        chunker.chunk(t)
    dt = time.perf_counter() - t0
    print(f"  吞吐：{len(texts)} 文件 / {total:,} 字 / {dt:.2f}s "
          f"= {total/dt:,.0f} 字/秒")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--novels", default=r"D:\Docs\_w\小说")
    ap.add_argument("--adult", default=r"D:\Docs\_w\涩涩\限级、成人")
    ap.add_argument("--limit", type=int, default=None,
                    help="每类最多取多少个文件")
    ap.add_argument("--seed", type=int, default=20260101)
    ap.add_argument("--json", default=None, help="结果写到这个文件")
    args = ap.parse_args()

    random.seed(args.seed)
    novels, adult = Path(args.novels), Path(args.adult)
    if not novels.is_dir() or not adult.is_dir():
        raise SystemExit(f"语料目录不存在：{novels} / {adult}")

    chunker = SentenceChunker()
    tpath = ROOT / "data/legacy-imported/falun-terms.json"
    matcher = LegacySemanticMatcher.from_json(tpath)

    print("=" * 68)
    print("吞吐")
    print("=" * 68)
    throughput(chunker, novels)

    print()
    print("=" * 68)
    print(f"层 A：法轮功字面词表（{len(matcher)} 条）")
    print("=" * 68)
    a_novel = layer_a(matcher, novels, "novel", args.limit)
    a_adult = layer_a(matcher, adult, "adult", args.limit)
    for s in (a_novel, a_adult):
        print(f"  {s.name:6s} 文件 {s.files:5d}  {s.chars:>11,d} 字")
        print(f"         任一词条命中  {s.hit_files:4d} 文件 "
              f"({s.hit_files/max(s.files,1):6.2%})，共 {s.hits} 次"
              f"  ← 含 0 级普通词，不作为判据")
        print(f"         等级>0 命中   {s.blocked_files:4d} 文件 "
              f"({s.blocked_files/max(s.files,1):6.2%})"
              f"  等级分布 {dict(sorted(s.level_hist.items()))}")

    print()
    print("=" * 68)
    print("层 B：HNC 结构特征（不使用任何词表）")
    print("=" * 68)
    b_novel = layer_b(chunker, novels, "novel", args.limit)
    b_adult = layer_b(chunker, adult, "adult", args.limit)
    fn, fa = b_novel.features(), b_adult.features()
    print(f"  {'特征':16s} {'小说':>10s} {'成人':>10s} {'相对差':>10s}")
    for k in FEATURES:
        v1, v2 = fn[k], fa[k]
        rel = (v2 - v1) / v1 if v1 else float("inf")
        print(f"  {k:16s} {v1:10.4f} {v2:10.4f} {rel:+10.1%}")
    for s in (b_novel, b_adult):
        print(f"  [{s.name}] 文件 {s.files} 字 {s.chars:,} "
              f"块 {s.chunks:,}")

    if args.json:
        out = {
            "layer_a": {
                "novel": {"files": a_novel.files, "chars": a_novel.chars,
                          "hit_files": a_novel.hit_files,
                          "hits": a_novel.hits,
                          "blocked_files": a_novel.blocked_files,
                          "level_hist": a_novel.level_hist},
                "adult": {"files": a_adult.files, "chars": a_adult.chars,
                          "hit_files": a_adult.hit_files,
                          "hits": a_adult.hits,
                          "blocked_files": a_adult.blocked_files,
                          "level_hist": a_adult.level_hist},
            },
            "layer_b": {"novel": fn, "adult": fa},
        }
        Path(args.json).write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已写入 {args.json}")


if __name__ == "__main__":
    main()
