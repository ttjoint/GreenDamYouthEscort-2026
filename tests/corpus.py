"""带标注的测试语料。

## 这些案例断言的是什么

断言的是**匹配机制**和**已知的数据行为**，不是我猜的语义分级。

写这套语料时踩过一个坑，值得记下来：最初假设 `hong_hei` 是完整敏感度分级，
于是预期「《九评共产党》」必然高等级。实际查数据发现**所有**「九评」词条
`hh` 都是 0，只有 `y省yy县610恐怖组织的犯罪事实` 这类长句才是 5。也就是说
`hh` 的语义比我最初的假设窄，硬塞进期望区间只会自欺。

所以现在的做法是：

- `safe` 案例只断言**不命中**——这是机制问题，可靠。
- `known` 案例断言**具体等级**，等级值直接来自数据表，是回归防护。
- `weak` 案例断言**命中但等级低**，记录 `hh` 的真实分布特征。

数据若变化，这些测试会失败，那正是它们的作用。
"""

from __future__ import annotations

CORPUS: list[dict[str, object]] = [
    # ---- 日常文本：必须不命中 ----
    {
        "id": "safe-weather",
        "class": "safe",
        "text": "今天天气不错，我们下午去公园散步吧。",
        "expect_min": 0,
        "expect_max": 0,
    },
    {
        "id": "safe-homework",
        "class": "safe",
        "text": "孩子的数学作业写完了，家长签字后交回老师。",
        "expect_min": 0,
        "expect_max": 0,
    },
    {
        "id": "safe-shopping",
        "class": "safe",
        "text": "这批货物的库存已经不足，需要尽快补货。",
        "expect_min": 0,
        "expect_max": 0,
    },
    {
        "id": "safe-greeting",
        "class": "safe",
        "text": "老师好，我叫小明，今年十岁，喜欢画画和游泳。",
        "expect_min": 0,
        "expect_max": 0,
    },
    {
        "id": "safe-english",
        "class": "safe",
        "text": "The quick brown fox jumps over the lazy dog.",
        "expect_min": 0,
        "expect_max": 0,
    },

    # ---- 已知等级：值来自数据表，属回归防护 ----
    {
        "id": "known-dajiyuan-url",
        "class": "known",
        "text": "请访问大纪元新闻网查看更多消息。",
        "expect_min": 7,
        "expect_max": 7,
        "note": "大纪元新/新闻网 在表里是 7。",
    },
    {
        "id": "known-dafa-dizi",
        "class": "known",
        "text": "这些人自称大法弟子，经常聚集在一起。",
        "expect_min": 6,
        "expect_max": 6,
        "note": "大法弟子 在表里有 6 级条目。",
    },
    {
        "id": "known-425",
        "class": "known",
        "text": "会议定在4.25上午九点召开。",
        "expect_min": 4,
        "expect_max": 4,
        "note": "4.25 同时存在 1 级和 4 级条目，取高者。",
    },
    {
        "id": "known-evasive",
        "class": "known",
        "text": "有个词叫 a4Lu4 ，还有 A--Lun--Gong 这类写法。",
        "expect_min": 1,
        "expect_max": 1,
        "note": "变体拼写普遍是 1 级。",
    },

    # ---- 弱命中：说明 hh 不是全覆盖的分级 ----
    {
        "id": "weak-jiuping",
        "class": "weak",
        "text": "他在网上转发了《九评共产党》这本书。",
        "expect_min": 0,
        "expect_max": 1,
        "note": "反直觉但真实：所有「九评」词条 hh 都是 0。",
    },
    {
        "id": "weak-610-office",
        "class": "weak",
        "text": "610办公室位于中心城区。",
        "expect_min": 0,
        "expect_max": 1,
        "note": "同理，「610」短词条几乎都是 0，只有个别长句是 5。",
    },
    {
        "id": "weak-government",
        "class": "weak",
        "text": "市政府召开会议，讨论明年道路改造计划。",
        "expect_min": 0,
        "expect_max": 3,
        "note": "「政府」有一条 3 级(上下文为 [江泽)，裸词会被连带。",
    },
]


def load() -> list[dict[str, object]]:
    return list(CORPUS)
