"""HNC 语义判定的复刻。

## 数据实际是什么

`FalunWord.lib` 解开后是**一份 36,943 字符的连续敏感语料流**（按拼音/字母
序排列），外加一张 4,693 条的 sense 表。每条 sense 是语料流上的一个
`(偏移, 长度, 等级)` 三元组，等级 `hong_hei` 取 0..7。

    sense[i] = (off, len, level)  ->  语料流[off:off+len]

同一字符串可以带**多个不同等级**，因为它在语料流里出现于不同位置、
不同上下文。实测 3,364 个唯一片段中有 43 个是多等级的：

    '大法弟子'   -> [6, 0, 0, 0, ...]     6 次高等级，其余为 0
    '《法轮功'   -> [1, 0, 1, 5, 5, ...]  随上下文浮动
    '4.25'       -> [1, 4]

这就是原引擎"上下文敏感"的物理载体：**等级不是词的固有属性，而是
该词在某段上下文中的属性**。

## 复刻策略

1. 用 sense 片段建最长匹配扫描器（这是词条层）。
2. 每个片段携带它的**全部等级**，取最高者作为保守判定。
3. 同时把整段语料流当作可匹配文本，因为语料流本身含有大量长词条
   （`yy省yy县610恐怖组织的犯罪事实` 这类），链式 Trie 未完整覆盖。

## 诚实边界

原引擎逐词"正常/拦截"的标志来自 `FMean.tmp`，该文件从未随包分发
（见 docs/falunword-format.md）。这里重建的是**基于可用等级的判定梯度**，
不是位级还原。等级语义由语料统计反推，各级主题一致：
7=大纪元/新闻网，6=学员供稿，5=《九评》/610 恐怖组织，4=4.25 上访/邪教组织，
3=610 办公室/洗脑班，2=大法弟子，1=变体拼写/非法聚集。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

SAFE_LEVEL = 0


@dataclass(frozen=True)
class Hit:
    """一次词库命中。"""

    term: str
    start: int
    level: int
    # 同一词条在语料流中可能对应多个等级；这里保留全集供上层判断。
    all_levels: tuple[int, ...] = ()


@dataclass
class Verdict:
    """对一段文本的判定结果。"""

    hits: list[Hit] = field(default_factory=list)
    text_length: int = 0

    @property
    def max_level(self) -> int:
        return max((h.level for h in self.hits), default=SAFE_LEVEL)

    @property
    def level_counts(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for h in self.hits:
            counts[h.level] = counts.get(h.level, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def blocked(self) -> bool:
        """词库层是否命中敏感语义。

        拦截阈值属于策略层（M8）；本层只回答"有没有、有多强"。
        """
        return self.max_level > SAFE_LEVEL


class LegacySemanticMatcher:
    """基于导入数据的最大匹配扫描器。

    朴素最长匹配：在每个起始位置尝试所有候选长度，取最长命中。片段最长
    约 40 字符，Python 下足够快，且与数据表一一对应，便于逐条核对。
    """

    def __init__(self, levels: dict[str, tuple[int, ...]]) -> None:
        self._levels = dict(levels)
        self._max_len = max((len(t) for t in self._levels), default=0)

    @classmethod
    def from_json(cls, path: str | Path) -> "LegacySemanticMatcher":
        """从导入数据构建匹配器。

        等级的归属很容易搞错，这里记清楚：

        - `senses` 是语料流上 `(偏移, 长度, 等级)` 的**位置表**。
        - `term_senses` 是链（即真正拿去匹配的词条）到 sense 的**引用**。

        等级要跟**链词条**走，而不是跟 `sense["text"]`（即池切片）走。
        两者常常不同：`《九评》` 作为池切片全是 0 级，而链引用它的那一条
        可以是 0..7 任意值。早期版本按池切片取等级，导致高等级词条全部
        退化为 0，这是一个真实发生过的错误。
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        by_index = {s["index"]: s["hong_hei"] for s in data["senses"]}
        levels: dict[str, list[int]] = {}
        for entry in data["term_senses"]:
            level = by_index.get(entry["sense_id"])
            if level is None:
                continue
            levels.setdefault(entry["word"], []).append(level)
        # 池切片也作为候选补上，但只在其本身非空时；它们的等级为 0 是
        # 正常的，不代表词条无害。
        for sense in data["senses"]:
            text = sense["text"]
            if text:
                levels.setdefault(text, []).append(sense["hong_hei"])
        return cls({k: tuple(sorted(set(v))) for k, v in levels.items()})

    def __len__(self) -> int:
        return len(self._levels)

    def levels_of(self, term: str) -> tuple[int, ...] | None:
        return self._levels.get(term)

    def analyse(self, text: str) -> Verdict:
        verdict = Verdict(text_length=len(text))
        i = 0
        n = len(text)
        while i < n:
            matched = 0
            step = min(self._max_len, n - i)
            while step >= 2:
                candidate = text[i : i + step]
                all_levels = self._levels.get(candidate)
                if all_levels is not None:
                    verdict.hits.append(
                        Hit(candidate, i, max(all_levels), all_levels)
                    )
                    matched = step
                    break
                step -= 1
            i += matched if matched else 1
        return verdict


def load_default() -> LegacySemanticMatcher:
    root = Path(__file__).resolve().parents[2]
    return LegacySemanticMatcher.from_json(
        root / "data" / "legacy-imported" / "falun-terms.json"
    )
