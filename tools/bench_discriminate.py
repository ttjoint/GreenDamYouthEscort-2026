"""单文件级判别力分析：结构特征能否区分两类语料。

## 为什么要这一步

`bench_corpus.py` 给的是**语料整体均值**。整体均值有差异不代表可用：

    jk_per_e  小说 6.46 / 成人 7.78   （差 +20.4%）

但如果两个分布几乎完全重叠，那么任何阈值都会把大量普通小说误判。
反过来，若某个特征的单文件分布是双峰的，它就能直接当判据。

## 输出什么

对每个结构特征算单文件分布，并给出：

- **AUC**：随机取一个小说文件和一个成人文件，小说得分低于成人的概率。
  0.5 = 无判别力，1.0 = 完美分离。
- **最佳阈值下的准确率**：扫全部候选切点，取准确率最高的那个。
  这是**上限估计**——在同一批数据上选阈值再评估，会乐观偏差。
  所以同时给出留一法（LOOCV）交叉验证的准确率，那才是可信数字。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from bench_corpus import FEATURES, iter_corpus, read_text  # noqa: E402
from green_dam.chunker import SentenceChunker  # noqa: E402


def per_file_features(chunker: SentenceChunker, root: Path,
                      limit: int | None) -> list[list[float]]:
    """每文件一行，列顺序同 FEATURES。"""
    rows: list[list[float]] = []
    for i, path in enumerate(iter_corpus(root)):
        if limit is not None and i >= limit:
            break
        text = read_text(path)
        if not text:
            continue
        chunking = chunker.chunk(text)
        total = max(len(chunking.chunks), 1)
        cnt: dict[str, int] = {}
        blk = 0
        for ch in chunking.chunks:
            key = ch.role.value if ch.role.is_main else f"aux:{ch.role.value}"
            cnt[key] = cnt.get(key, 0) + 1
            blk += len(ch.text)
        sents = max(text.count("。") + text.count("！")
                    + text.count("？"), 1)
        aux = sum(v for k, v in cnt.items() if k.startswith("aux:"))
        jk = sum(v for k, v in cnt.items() if k in ("A", "B", "C"))
        e = cnt.get("E", 0)
        rows.append([
            aux / total,
            e / total,
            jk / max(e, 1),
            cnt.get("aux:Ms", 0) / max(aux, 1),
            cnt.get("aux:Pr", 0) / max(aux, 1),
            cnt.get("aux:Cn", 0) / max(aux, 1),
            cnt.get("?", 0) / total,
            blk / total,
            len(chunking.chunks) / sents,
        ])
    return rows


def auc(pos: list[float], neg: list[float]) -> float:
    """P(pos > neg) + 0.5·P(pos == neg)，按秩计算。"""
    if not pos or not neg:
        return 0.5
    wins = 0.0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1
            elif p == n:
                wins += 0.5
    return wins / (len(pos) * len(neg))


def best_threshold(adult: list[float], novel: list[float]) -> tuple[float, float]:
    """扫所有候选切点，返回 (阈值, 准确率)。方向：大于阈值 → 成人。

    这个数字是**乐观上限**：阈值在同一批数据上选出又评估。
    """
    cands = sorted(set(adult + novel))
    best_t, best_acc = 0.0, -1.0
    for t in cands:
        tp = sum(1 for v in adult if v > t)
        tn = sum(1 for v in novel if v <= t)
        acc = (tp + tn) / (len(adult) + len(novel))
        if acc > best_acc:
            best_t, best_acc = t, acc
    return best_t, best_acc


def loocv(adult: list[float], novel: list[float]) -> float:
    """留一法：每次留出一个样本，用其余样本选阈值，再判它。"""
    pts = [(v, 1) for v in adult] + [(v, 0) for v in novel]
    correct = 0
    for i, (v, label) in enumerate(pts):
        rest = pts[:i] + pts[i + 1:]
        a = [x for x, lab in rest if lab == 1]
        n = [x for x, lab in rest if lab == 0]
        if not a or not n:
            continue
        t, _ = best_threshold(a, n)
        pred = 1 if v > t else 0
        correct += pred == label
    return correct / len(pts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--novels", default=r"D:\Docs\_w\小说")
    ap.add_argument("--adult", default=r"D:\Docs\_w\涩涩\限级、成人")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    chunker = SentenceChunker()
    print("提取单文件特征…")
    novel_rows = per_file_features(chunker, Path(args.novels), args.limit)
    adult_rows = per_file_features(chunker, Path(args.adult), args.limit)
    print(f"  小说 {len(novel_rows)} 文件，成人 {len(adult_rows)} 文件")

    print()
    print("=" * 74)
    print(f"{'特征':16s} {'AUC':>7s} {'最优阈值':>10s} "
          f"{'上限准确率':>10s} {'留一法':>8s}")
    print("=" * 74)
    out = {}
    for j, name in enumerate(FEATURES):
        a = [r[j] for r in adult_rows]
        n = [r[j] for r in novel_rows]
        a_pos = auc(a, n)
        t, acc = best_threshold(a, n)
        cv = loocv(a, n)
        out[name] = {"auc": a_pos, "threshold": t,
                     "acc_upper": acc, "acc_loocv": cv}
        flip = "（方向反转）" if a_pos < 0.5 else ""
        print(f"{name:16s} {a_pos:7.3f} {t:10.4f} "
              f"{acc:10.2%} {cv:8.2%} {flip}")

    print()
    print("说明：")
    print("  AUC 0.5 = 无判别力，1.0 = 完美分离；<0.5 表示方向与预期相反。")
    print("  上限准确率是乐观估计（同批数据选阈值再评估）。")
    print("  留一法才是可信数字。两者差距大 = 特征不稳定。")
    print("  基准：全判「成人」的准确率 = "
          f"{len(adult_rows)/max(len(adult_rows)+len(novel_rows),1):.2%}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  已写入 {args.json}")


if __name__ == "__main__":
    main()
