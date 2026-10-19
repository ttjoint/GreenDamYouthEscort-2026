# -*- coding: utf-8 -*-
"""从真实语料用体标记（着/了/过）自动抽取候选动词。

## 为什么

现有 `DEFAULT_VERBS` 只有 208 条，其中单字只有 6 个（吃哭坐睡站笑）。
汉语单字动词（看/说/走/买/拿/做/去/来）几乎全缺，导致真实语料上
**99.9% 的退化是「无谓语候选」**（见 docs/corpus-eval.md）。

手工补动词表不现实，所以用体标记自动抽：

    看 着 / 笑 了 / 说 过

"动词 + 着/了/过" 是汉语体范畴的形态标记，可靠度比「猜」高得多。

## 但直接抽会有大量噪声

实测前 40 名里有 `不过`、`接着`、`向着`、`为了`、`太大了`——
这些都不是「动词+体标记」：

- `不过` / `接着` / `随着`：连词或介词，`过`/`着` 是词的一部分
- `为了`：介词
- `太大了`：形容词 + 程度副词 + 了

所以必须过滤。过滤规则和每条的实测数据都写在下面。
"""

from __future__ import annotations

import collections
import os
import re
import sys

#: 「1-2 字 + 着/了/过」的模式
CANDIDATE_RE = re.compile("([\u4e00-\u9fff]{1,2})([\u7740\u4e86\u8fc7])")

#: 不是动词的候选：连词、介词、副词、代词。
#: 这些词里的 `着/了/过` 是词素而不是体标记。
#:
#: 依据（逐一核对语料高频结果）：
#:   不过/接着/随着/向着/为了/除了/比着/朝了 —— 连词介词
#:   什么/怎么/这么/那么/多么 —— 疑问/指示代词
#:   自己/大家/我们/你们/他们 —— 代词 + 了
#:   已经/曾经 —— 时间副词
NON_VERB = frozenset("""
不过 接着 随着 向着 为了 除了 随着 说到 至于 关于 由于 对于 按照
什么 怎么 这么 那么 多么 什么 哪怕 任你
自己 大家 我们 你们 他们 她们 咱们
已经 曾经 刚才 刚刚 常常 偏偏 偏偏 渐渐
是了 对了 好了 行了 算了 得了 罢了 来了
太了 真了 很了 最了 更了 太着 真着
""".split())

#: 形容词后缀：`太大了` / `太好了` / `真好看`。这类不是谓语动词。
#: 判据：候选去掉体标记后，若以程度副词开头，则丢掉。
DEGREE_PREFIX = frozenset("太真很最更挺蛮怪好")

#: 方位/趋向单字：`到了` 里的 `到` 可以是动词，但 `上了` `下了` `进了`
#: 常是趋向补语。保留 `到/上/下/进/出/回/起` 作为候选是**故意**的：
#: 它们确实能独立作谓语（`他到了` `他上了车`）。
TENDENCY = frozenset("上下进出回起过到")

#: 明显不是动词的高频单字（在语料里常出现在体标记前，但作别的成分）。
NOT_VERB_SINGLE = frozenset("的 地 得 之 其 此 该 各 每 某 本 那 这")

#: 能粘在动词前形成“假候选”的字：前面是它，说明切到了词中间。
#:
#: 实测噪声来源：`的看`（我的看法）、`只不`（只不过）、`是为`（是为了）、
#: `头看`（回头看他）、`受不`（受不了）——全都是把词切断了。
#: 判据：双字候选的首字若属于这些「粘着前字」，则丢掉整个候选。
GLUE_PREFIX = frozenset(
    "的 地 得 只 是 不 头 身 手 腿 脚 心 眼 口 里 下 上 中 前 后 内 外 "
    "象 使 被 把 将 让 给 对 向 从 往 朝 和 与 跟 同 为 以 因 由 "
    "就 都 也 还 又 才 再 更 很 最 太 真 仍 依然 终 竟 倒 却 便"
)


def harvest(paths: list[str], limit_chars: int | None = None
            ) -> collections.Counter:
    """扫语料，返回 `{候选词: 次数}`。"""
    counter: collections.Counter = collections.Counter()
    total = 0
    for path in paths:
        try:
            raw = open(path, "rb").read()
        except OSError:
            continue
        text = _decode(raw)
        total += len(text)
        for m in CANDIDATE_RE.finditer(text):
            counter[m.group(1) + m.group(2)] += 1
        if limit_chars and total >= limit_chars:
            break
    return counter


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "gbk", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def filter_candidates(counter: collections.Counter, min_count: int = 20
                      ) -> dict[str, int]:
    """把「候选 + 体标记」过滤成动词表 `{动词: 次数}`。"""
    out: dict[str, int] = {}
    for word, n in counter.items():
        if n < min_count:
            continue
        if word in NON_VERB:
            continue
        stem = word[:-1]
        if not stem:
            continue
        if stem in NON_VERB:
            continue
        # 程度副词开头 -> 形容词
        if stem[0] in DEGREE_PREFIX:
            continue
        # 双字候选首字是粘着前字 -> 切到词中间了
        if len(stem) >= 2 and stem[0] in GLUE_PREFIX:
            continue
        if len(stem) == 1 and stem in NOT_VERB_SINGLE:
            continue
        # 单字候选只收常见的动作/趋向字，其余太容易误判
        if len(stem) == 1 and stem not in TENDENCY:
            # 保留高频的（语料里 >200 次的单字动词大概率是真的）
            if n < 200:
                continue
        out[stem] = out.get(stem, 0) + n
    return out


def corpus_paths(folder: str, limit: int = 20) -> list[str]:
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    return [os.path.join(folder, n) for n in names
            if n.endswith(".txt")][:limit]


def main(argv: list[str]) -> int:
    default = r"D:\Docs\_w\小说"
    folder = argv[1] if len(argv) > 1 else default
    paths = corpus_paths(folder, limit=20)
    if not paths:
        print("找不到语料：%s" % folder)
        return 2
    counter = harvest(paths)
    verbs = filter_candidates(counter)

    print("语料文件 %d 个" % len(paths))
    print("体标记候选 %d 种" % len(counter))
    print("过滤后动词 %d 条" % len(verbs))
    print()
    print("按频次前 40：")
    for word, n in sorted(verbs.items(), key=lambda kv: -kv[1])[:40]:
        print("  %-6s %d" % (word, n))
    print()
    print("单字动词（前 30）：")
    singles = [(w, n) for w, n in verbs.items() if len(w) == 1]
    for word, n in sorted(singles, key=lambda kv: -kv[1])[:30]:
        print("  %-4s %d" % (word, n))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
