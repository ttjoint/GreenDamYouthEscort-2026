"""HNC 句类框架（Sentence Category）复刻。

## 这是什么

`SentenceObj.dll` 的 `0x10030650`–`0x10030AF0` 是 HNC 的**句类判定器**：
给它一个词元结构体，输出一个**句类码**（形如 `!31116`）。

这是绿坝真正"理解句子"的地方，也是它区别于纯关键词过滤的地方：判定依据
不是"文本里有没有某个词"，而是"这个句子在 HNC 句类框架里是什么结构"。

## 参数结构（`0x10030650` 反汇编）

函数只有一个指针参数，指向 13 字节以上的记录：

    offset  type  含义                     代码依据
    ------  ----  -----------------------  -------------------------------
    +0x00   u8    （未在此函数读取）
    +0x01   i8    role_a                   0x1003065A movsx eax,[ecx+1]
    +0x04   i32   participants             0x10030654 cmp edx,3
    +0x08   i32   辅助判据 spare           0x10030663 mov edi,[ecx+8]
    +0x0C   u8    role_b                   0x1003065F mov bl,[ecx+0xC]

入口守卫（`0x10030657`–`0x1003066B`）：

    if participants > 3: return 0
    if role_b > 2      : return 0

**注意 `role_b` 上限是 2**，不是 3。早期版本我按 `bl==3` 写了整整一个分支，
那是读错偏移导致的臆造——`bl>2` 在入口就被挡掉了。

## 三个分支各自用的对象

| 分支 | concept 来源 | role_a 来源 | 角色判定 |
|---|---|---|---|
| `role_b==0` | — | `[+0x01]` | `role_a==2` |
| `role_b==1` | `[+0x0F]`（另一对象） | `[+0x13]` | 跳表 1..4 |
| `role_b==2` | `[+0x10]`（另一对象） | `[+0x13]` | 分层 |

`role_b==1` 与 `role_b==2` 读的是**不同的结构体**（`ecx` 在入口后被换过），
这不是笔误，是原代码如此。

## 句类码编码（结构确定，命名未知）

    ! 3 1 1 1 6
    │ │ │ │ └─ 变体位
    │ │ │ └─── 关系位
    │ │ └───── 关系位
    │ └─────── 角色组
    └───────── 句类族   (1 / 2 / 3)

## 诚实边界

`SENTEN.LIB` / `TYPE.LIB` / `XU.LIB` / `rule.lib` / `JiBenJL.lib` **未随包分发**，
`FalunWord.lib` 是唯一例外。所以：

- **判定逻辑**完整，可逐条对照反汇编核对（本模块的注释就是这么写的）。
- **码 → 自然语言语义**的字典在缺失的 `.lib` 里，无法还原。
  能断言"这是 `!31116` 句类"，不能断言"HNC 管它叫什么"。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

# ---------------------------------------------------------------------------
# 概念类
# ---------------------------------------------------------------------------


class ConceptClass(IntEnum):
    """概念类。只在 0x30 / 0x40 两处比较，其余值返回 0。"""

    ACTION = 0x30
    STATE = 0x40


# ---------------------------------------------------------------------------
# 句类码
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SentenceCategory:
    """一条句类码。`variant` 是紧随 ASCII 码的字节，含义未知，原样保留。"""

    code: str
    variant: int = 0

    @property
    def family(self) -> int:
        return int(self.code[1])

    def __str__(self) -> str:
        return self.code


def _c(code: str, variant: int = 0) -> SentenceCategory:
    return SentenceCategory(code, variant)


# 反汇编自 .data，行尾为 VA。
TABLE: dict[str, SentenceCategory] = {
    "!0": _c("!0"),  # 0x10045A48  (2 字节短码)
    "!112": _c("!112"),  # 0x100460F0
    "!113": _c("!113"),  # 0x10046100
    "!114": _c("!114"),  # 0x10046120
    "!115": _c("!115"),  # 0x10046150
    "!116": _c("!116"),  # 0x10046160
    "!125": _c("!125"),  # 0x10046140
    "!133": _c("!133"),  # 0x10046110
    "!135": _c("!135"),  # 0x10046130
    "!233": _c("!233"),  # 0x100460E8
    "!235": _c("!235"),  # 0x100460F8
    "!320": _c("!320"),  # 0x10045AB4
    "!330": _c("!330"),  # 0x10045A8C
    "!31114": _c("!31114"),  # 0x10046128
    "!31115": _c("!31115"),  # 0x10046158
    "!31116": _c("!31116"),  # 0x10046168
    "!31123": _c("!31123"),  # 0x10046108
    "!31125": _c("!31125"),  # 0x10046148
    "!31133": _c("!31133"),  # 0x10046118
    "!31135": _c("!31135"),  # 0x10046138
    "!32113": _c("!32113"),  # 0x10045A9C
    "!32114": _c("!32114"),  # 0x10045AAC
    "!32133": _c("!32133"),  # 0x10045AA4
    "!33123": _c("!33123"),  # 0x10045A94
}


# ---------------------------------------------------------------------------
# 判定器
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Frame:
    """判定器输入，对应上面参数结构的四个语义字段。"""

    role_a: int
    role_b: int
    participants: int
    concept_class: int = int(ConceptClass.ACTION)
    spare: int = 0
    role2: int = 0


def classify(f: Frame) -> SentenceCategory | None:
    """复刻 `0x10030650`。返回 `None` = 原函数返回 0。

    控制流逐分支照搬反汇编。下面每段都注了对应 VA，便于核对。
    """
    a, b, n, cc, spare, role2 = (
        f.role_a,
        f.role_b,
        f.participants,
        f.concept_class,
        f.spare,
        f.role2,
    )

    # ---- 入口守卫 0x10030657 -------------------------------------------
    if n > 3:
        return None
    if b > 2:
        return None

    # ---- role_b == 0 : 0x10030A76 --------------------------------------
    if b == 0:
        # 0x10030A76: `cmp eax,2` —— 要求 role_a == 2
        if a != 2:
            return None
        if n == 1:
            # 0x10030A84 `cmp edi,eax`（edi=spare, eax=2）：
            #   spare==2 -> !0      (0x10030A88)
            #   spare==1 -> !32133  (0x10030AAB)
            if spare == 2:
                return TABLE["!0"]
            if spare == 1:
                return TABLE["!32133"]
            return None
        if n == 2:
            return TABLE["!233"]  # 0x10030ACC
        return None

    # ---- role_b == 1 : 0x100308DC --------------------------------------
    # 注意：此分支的 concept_class 来自另一个对象，这里按调用方传入值处理。
    if b == 1:
        if cc != ConceptClass.ACTION:
            return None
        if not 1 <= a <= 4:
            return None
        # 跳表 0x10030AF4 按 ecx=a-1 索引（这是实际取出的顺序，早期版本
        # 把前两项写反了，导致 a=1/a=2 的结果整体互换）：
        #   a=1 -> 0x1003092B   a=2 -> 0x100309A3
        #   a=3 -> 0x100309EF   a=4 -> 0x100308FF
        if a == 1:
            # 0x1003092B：n==1 时再按 spare 分
            if n == 1:
                if spare == 2:
                    return TABLE["!0"]  # 0x10030935
                if spare == 1:
                    return TABLE["!32133"]  # 0x1003095C
                return None
            return TABLE["!133"]  # 0x10030983
        if a == 2:
            # 0x100309A3
            if n == 2:
                return TABLE["!113"]  # 0x100309A8
            if n == 3:
                return TABLE["!235"]  # 0x100309CD
            return None
        if a == 3:
            # 0x100309EF
            if n == 1:
                return TABLE["!31114"]  # 0x100309F4
            return TABLE["!114"]  # 0x10030A1F
        # a == 4 : 0x100308FF
        return TABLE["!31123"]

    # ---- role_b == 2 : 0x1003068F --------------------------------------
    # 仔细观察才发现：此分支内部还要再判一次 [+0x14]，即 "第二角色"，
    # 与入口守卫用的 [+0x0C] 不是同一个字段。我最初把 role_b 当成固定条件，
    # 漏掉了这层，导致这一整段的输出全错。
    if b == 2:
        if cc == ConceptClass.ACTION:
            if a == 1:
                # 0x1003076A: bl-2 == 0 -> 再判 n；bl-2 == 1 -> 再判 n
                if role2 == 2:
                    return TABLE["!31125"] if n == 2 else TABLE["!125"]
                if role2 == 3:
                    return TABLE["!31125"] if n == 2 else TABLE["!125"]
                return None
            if a == 2:
                # 0x10030712: 要求 role2 == 3
                if role2 != 3:
                    return None
                return TABLE["!31115"] if n == 2 else TABLE["!115"]
            if a == 3:
                # 0x100306B5: 要求 role2 == 2
                if role2 != 2:
                    return None
                return TABLE["!31116"] if n == 2 else TABLE["!116"]
            return None
        if cc == ConceptClass.STATE:
            # 0x1003081B 之后紧跟 `cmp bl,2`：STATE 分支同样要求 role2 == 2
            if role2 != 2:
                return None
            if a == 1:
                # 0x1003088B: n==1 -> !31133，否则 !133
                return TABLE["!31133"] if n == 1 else TABLE["!133"]
            if a == 3:
                # 0x1003083C: n==1 -> !31114，否则 !114
                return TABLE["!31114"] if n == 1 else TABLE["!114"]
            # a 为其他值：0x10030836 `jne` 直接失败
            return None
        return None

    return None


# ---------------------------------------------------------------------------
# 词性 → 概念层次码
# ---------------------------------------------------------------------------


def concept_level(word_class: int, position: str) -> int:
    """复刻 `0x1000C9A0` 的概念层次码映射。

    基码 `c` 遇上不同位置：

    | position | 偏移 | 例（c=0x30）|
    |---|---|---|
    | `single` | +3 | 0x33 |
    | `first`  | +1 | 0x31 |
    | `last`   | +2 | 0x32 |
    | `middle` | +4 | 0x34 |

    实测基码 0x10 / 0x20 / 0x30 / 0x60 / 0x70 五组共用同一规律。
    """
    offsets = {"single": 3, "first": 1, "last": 2, "middle": 4}
    if position not in offsets:
        raise ValueError(f"unknown position: {position!r}")
    return word_class + offsets[position]


WORD_CLASS_BASES = (0x10, 0x20, 0x30, 0x60, 0x70)
