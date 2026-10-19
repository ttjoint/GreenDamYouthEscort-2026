"""语言逻辑概念 l 类的层级（`l` 准则）。

## 理论出处

黄曾阳《HNC 理论》论文 11.1《语义块切分的基本准则：lv 准则》。
原文（`data/hnc_archive/text/books_hnc_book2111.htm.txt`）：

> 语言逻辑概念 l 的设置和具体设计，是围绕着语义块的切分和组合
> 这一中心目标而展开的。具体说来就是：l0 和 l2 是主语义块指示符，
> l1 和 l3 是辅语义块指示符，l4 是语义块组合指示符，l8 是辅语义块
> 说明符，l6 和 la 是 E 语义块的说明符，lb 是句间说明符，l9 虽不是
> 语义块的直接指示符，但有间接指示广义对象语义块的作用。

## 为什么需要这个模块

`chunk_tags.py` 的表回答"**这个介词标记哪个语义块**"（主/辅/两可），
是**功能**分类。本节给的 l 类层级是**概念**分类，两者正交：

- `chunk_tags` 的 43 个纯辅介词，在 l 类里全是 `l1`/`l3`。
- `chunk_tags` 里没有的概念——`l0`/`l2`（主块指示）、`l4`（组合）、
  `l6`/`la`（E 块说明）、`l9`（指示代词）——都在这里。

## lv 准则最关键的一句话（修正了本项目的实现）

原文：

> **l 准则仅用于判定辅语义块，对主语义块的判定只有 v 准则可以利用。
> 所以，标准格式语句的主语义块切分通常反而比较困难。**

这句话是准则性的：**不能**用 l 类标记（介词）去切主语义块。早期
版本的 `chunker.py` 用介词定位并吞并后随内容，导致"在教室里"
把整个谓语一起吃成了辅块。本模块把 l 类分层，让调用方能够区分
"这个 l 标记只保证辅块"与"这个 l 标记根本不该用来切块"。

## 诚实边界

原文只给了 l0/l1/l2/l3/l4/l6/l8/l9/la/lb 这十个**一级**节点的分派，
没有给出每个节点下辖的具体词语清单（那在未出版的《HNC 概念符号
体系手册》里）。所以本模块：

- **分层结构**照抄原文，可核验。
- **具体词表**只收原文正文里明确点名的少数几个（如 l41="的"、
  l42=of、l40=关系代词、"根据"=lv8012l、"从"=lj80425、
  "到"=lj80426）。其余靠 `chunk_tags` 的功能分类回填，并标注为推断。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "LClass",
    "LNode",
    "L_TABLE",
    "l_of",
    "cuts_main_block",
    "cuts_aux_block",
]


class LClass(str, Enum):
    """l 类概念的一级节点。值就是 HNC 的符号写法。"""

    #: 主语义块指示符
    L0 = "l0"
    #: 主语义块指示符
    L2 = "l2"
    #: 辅语义块指示符
    L1 = "l1"
    #: 辅语义块指示符
    L3 = "l3"
    #: 语义块组合指示符
    L4 = "l4"
    #: E 语义块说明符（时态）
    L6 = "l6"
    #: 辅语义块说明符
    L8 = "l8"
    #: 指示代词（间接指示广义对象语义块，不作切分标志）
    L9 = "l9"
    #: E 语义块说明符（逻辑态）
    LA = "la"
    #: 句间说明符
    LB = "lb"


#: 哪些 l 类**可以**用来切辅语义块。
#: 原文："l1 和 l3 是辅语义块指示符"、"l8 是 l1 的补充……由 l8
#: 标志的概念一定是辅语义块的切分标志"。
AUX_CUTTING = frozenset({LClass.L1, LClass.L3, LClass.L8})

#: 哪些 l 类可以切主语义块。
#:
#: **空集**。这是 lv 准则的直接结论：原文说"l 准则仅用于判定辅
#: 语义块，对主语义块的判定只有 v 准则可以利用"。l0/l2 虽然叫
#: "主语义块指示符"，指的是它们**指示**主块，而不是说可以用它们
#: 去**切**主块——它们出现在句子偏离标准格式的时候，用于给出标志
#: 信息，属于"转成标准格式时撇开不管"的那一类。
MAIN_CUTTING: frozenset[LClass] = frozenset()


@dataclass(frozen=True)
class LNode:
    """一条 l 类记录。"""

    surface: str
    cls: LClass
    #: 是否由原文正文直接点名（False = 由功能分类回填的推断）
    attested: bool
    note: str = ""


#: 表内数据。attested=True 的条目都能在论文 11.1 正文里找到原话。
L_TABLE: dict[str, LNode] = {
    # --- 原文点名 ---
    "的": LNode("的", LClass.L4, True,
                "原文：'汉语表示偏正结构的字“的”……采用 l41'"),
    "of": LNode("of", LClass.L4, True, "原文：l42"),
    "根据": LNode("根据", LClass.L8, True,
                  "原文：'汉语“根据”义项之一的映射符号是 lv8012l'"),
    "从": LNode("从", LClass.L8, True, "原文：lj80425"),
    "到": LNode("到", LClass.L8, True, "原文：lj80426"),
    "这": LNode("这", LClass.L9, True, "原文：指示代词归入 l9"),
    "那": LNode("那", LClass.L9, True, "原文：指示代词归入 l9"),
    "此": LNode("此", LClass.L9, True, "原文：指示代词归入 l9"),
}

#: 由 `chunk_tags` 的辅块标记符回填的 l1/l3 推断项。
#:
#: 原文没有给出 l1 与 l3 的判别标准（只说"l1 和 l3 是辅语义块
#: 指示符"），所以这里**不硬分** l1/l3，统一记为 l1，并标 attested
#: = False。分成两类需要《HNC 概念符号体系手册》，我们没有。
_INFERRED_AUX = (
    "按照", "依照", "遵照", "根据", "本着", "凭着", "通过", "经过",
    "沿着", "顺着", "朝着", "对着", "为了", "因为", "由于", "关于",
    "对于", "至于", "除了", "直到", "自从", "打从", "趁", "当",
    "离", "在", "用", "按", "依", "照", "沿", "因", "据",
)

for _w in _INFERRED_AUX:
    if _w not in L_TABLE:
        L_TABLE[_w] = LNode(_w, LClass.L1, False,
                            "由 chunk_tags 的辅块标记符回填（l1/l3 未细分）")


def l_of(surface: str) -> LNode | None:
    """查一个词/字属于哪类 l 概念；不是 l 类则返回 None。"""
    return L_TABLE.get(surface)


def cuts_aux_block(surface: str) -> bool:
    """这个词能否作为辅语义块的切分标志。"""
    node = L_TABLE.get(surface)
    return node is not None and node.cls in AUX_CUTTING


def cuts_main_block(surface: str) -> bool:
    """这个词能否作为主语义块的切分标志。

    恒为 False——这是 lv 准则的结论，不是实现偷懒。见 `MAIN_CUTTING`。
    """
    node = L_TABLE.get(surface)
    return node is not None and node.cls in MAIN_CUTTING
