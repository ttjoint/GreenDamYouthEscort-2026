# -*- coding: utf-8 -*-
"""生成 `data/verb-table.json`：从真实语料抽出的动词表。

## 背景

`chunker.py` 原来的 `DEFAULT_VERBS` 只有 208 条，其中单字 6 个
（吃哭坐睡站笑）。汉语单字动词（看/说/走/买/拿/做/去/来）几乎全缺。

结果：真实语料上 **99.9% 的退化是「无谓语候选」**——分句器找不到
谓语，只能把整句退回成一个无标记块。见 `docs/corpus-eval.md`。

## 做法

用体标记自动抽候选：

    看着 -> 看    笑了 -> 笑    说过 -> 说

体标记（着/了/过）是汉语的形态标记，「动词+着/了/过」的可靠度
远高于「猜」。抽完再按噪声规则过滤，规则和依据见 `harvest_verbs.py`。

## 用法

    python tools/build_verb_table.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

import harvest_verbs as H  # noqa: E402
from green_dam.chunker import DEFAULT_VERBS  # noqa: E402

OUT = ROOT / "data" / "verb-table.json"

#: 抽取时扫的语料目录
CORPORA = (
    r"D:\Docs\_w\小说",
    r"D:\Docs\_w\涩涩\限级、成人",
)

#: 每类语料取前 N 个文件（大文件，取多了慢）
FILES_PER_CORPUS = 20

#: 频次门槛。
#:
#: 实测 30 太低：`认真`(38)、`原因`(71) 这类词会被抽进来当动词，
#: 切分时把「认真」「原因」错当成谓语，触发 14 个回归测试失败。
#: 提到 500 后剩下 296 条高置信词，既补上了单字动词（看/说/走/买/拿），
#: 又不带进低频噪声。
MIN_COUNT = 500

#: 单字候补里明显不是动词的。实测抽出来后逐条核对的。
NOT_VERBS = frozenset("""
不 所 在 再 多 少 够 有 是 象 使 被 把 将 让 给 对 向 从 往 朝
和 与 跟 同 为 以 因 由 就 都 也 还 又 才 更 很 最 太 真 仍 竟
倒 却 便 该 各 每 某 本 那 这 之 其 此 个 些 场 次 回 遍 趟
""".split())


def main() -> int:
    paths: list[str] = []
    for folder in CORPORA:
        paths.extend(H.corpus_paths(folder, limit=FILES_PER_CORPUS))
    if not paths:
        print("找不到语料")
        return 2

    counter = H.harvest(paths)
    picked = H.filter_candidates(counter, min_count=MIN_COUNT)

    verbs: dict[str, int] = {}
    for word, n in picked.items():
        if not 1 <= len(word) <= 2:
            continue
        if len(word) == 1 and word in NOT_VERBS:
            continue
        verbs[word] = n
    # 旧表并入：已经验证过的动词不该因为语料没覆盖就丢掉
    for word in DEFAULT_VERBS:
        verbs.setdefault(word, 0)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({
            "_source": "体标记（着/了/过）自动抽取 + 人工噪声过滤",
            "_attested": False,
            "_note": (
                "不是论文原表。论文未给出完整动词表，这里从真实语料用"
                "「动词+着/了/过」抽取，再用噪声规则过滤。"
                "单字动词的高误切风险由 chunker._find_verbs_safe 的"
                "「仅块边界认可」约束兜底。"
            ),
            "_corpora": list(CORPORA),
            "_files": len(paths),
            "count": len(verbs),
            "singles": sum(1 for w in verbs if len(w) == 1),
            "verbs": dict(sorted(verbs.items(), key=lambda kv: -kv[1])),
        }, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print("写入 %s" % OUT)
    print("动词 %d 条（单字 %d / 双字 %d）"
          % (len(verbs),
             sum(1 for w in verbs if len(w) == 1),
             sum(1 for w in verbs if len(w) == 2)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
