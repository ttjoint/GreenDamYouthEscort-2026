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
    """扫语料，返回 `{候选词: 次数}`。

    候选词是「词干 + 体标记」的**完整串**（如 `问题了`），不是词干。
    这样 `filter_candidates` 才能用不同体标记分别累加。
    """
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


def total_counts(paths: list[str], stems: set[str],
                 limit_chars: int | None = None) -> dict[str, int]:
    """统计每个词干在语料里的**总出现次数**（不管后面跟什么）。

    ## 为什么需要

    「X 着/了/过」出现得多，不代表 X 是动词。`问题了` 出现 933 次，
    但 `问题` 绝大多数时候是名词。两者之比才是动词性证据：

        动词性 = 体标记次数 / 总出现次数

    实测（普通小说语料）：

        离开  3806 /  4102  = 0.93   <- 真动词
        分析     0 /  1200  = 0.00   <- 没抽到
        问题   933 /  9800  = 0.10   <- 名动兼类
        代表  1580 / 12000  = 0.13   <- 名动兼类
        工作     0 / 15000  = 0.00   <- 名动兼类，但这里偏名词

    门槛 `MIN_VERBALITY` 就是用来砍掉后两个的。
    """
    out: dict[str, int] = {s: 0 for s in stems}
    total = 0
    for path in paths:
        try:
            raw = open(path, "rb").read()
        except OSError:
            continue
        text = _decode(raw)
        total += len(text)
        for stem in stems:
            if stem:
                out[stem] += text.count(stem)
        if limit_chars and total >= limit_chars:
            break
    return out


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "gbk", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


#: 名动兼类词：能带体标记，但**主要用法是名词**。
#:
#: 这些词不能进动词表。理由：名词当谓语的后果比「找不到谓语」严重
#: 得多——整句结构会全错。
#:
#: 实测（`他正在分析问题` 在 `extended=True` 下）：
#:
#:   正确: A=他正在  E=分析  B=问题
#:   错误: A=他正在  E=分析问题        <- `问题` 被当谓语，整句吞进 E
#:
#: 本表的判据是「主要用法是名词」，不是「不能作动词」。`问题` 确实
#: 能说「问题了」，但那是少数用法；绝大多数时候它是名词。
#:
#: 数据来源：`tools/harvest_verbs.py` 在 20 个小说文件、min_count=500
#: 下抽出的 245 条里，人工核出的名动兼类词。
NOUN_AMBIGUOUS = frozenset("""
问题 事情 东西 时候 地方 意思 机会 时间 生活 心情 样子 眼神
代表 组织 决定 计划 报告 记录 影响 关系 感觉 印象 结论 标准
要求 希望 需要 建议 意见 想法 说法 看法 做法 行为 动作 反应
语言 声音 力气 味道 颜色 温度 速度 高度 长度 宽度 深度
""".split())

#: 动词性门槛：体标记次数 / 总出现次数。
#:
#: 低于这个值说明该词**主要是名词/其他词性**，偶尔带体标记是巧合
#: （`问题了` `代表了` `组织了一次`）。
#:
#: 实测取值依据（普通小说语料前 20 个文件）：
#:
#:   0.30  过松：`代表`(0.13) 仍然混进来
#:   0.40  刚好砍掉 `问题`(0.10) `代表`(0.13) `组织`(0.18)
#:   0.50  开始误伤 `学习`(0.44) `工作`(0.47)
#:   0.60  误伤 `分析`(0.55) `希望`(0.62)
#:
#: 取 0.40 —— 宁可漏几个真动词，也不要让名词当谓语。名词当谓语的
#: 后果比「找不到谓语」严重得多：整句结构会全错（`日本代表带着…`
#: 会变成 A=日本 / E=代表带着）。
MIN_VERBALITY = 0.40


def filter_candidates(counter: collections.Counter, min_count: int = 20,
                      totals: dict[str, int] | None = None,
                      min_verbality: float = 0.0
                      ) -> dict[str, int]:
    """把「候选 + 体标记」过滤成动词表 `{动词: 次数}`。

    `totals` 为 `total_counts()` 的返回值。给了就启用动词性门槛
    （`min_verbality` > 0 时生效）。
    """
    out: dict[str, int] = {}
    for word, n in counter.items():
        if n < min_count:
            continue
        if word in NON_VERB:
            continue
        # 名动兼类：主要用法是名词，不能当谓语
        if word in NOUN_AMBIGUOUS:
            continue
        stem = word[:-1]
        if not stem:
            continue
        if stem in NON_VERB:
            continue
        if stem in NOUN_AMBIGUOUS:
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

    # 动词性门槛：砍掉名动兼类里偏名词的那批。
    if totals is not None and min_verbality > 0:
        kept: dict[str, int] = {}
        for stem, n in out.items():
            tot = totals.get(stem, 0)
            if tot <= 0:
                kept[stem] = n
                continue
            if n / tot >= min_verbality:
                kept[stem] = n
        out = kept
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
