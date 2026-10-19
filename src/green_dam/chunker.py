"""语义块分块器：把句子切成语义块序列。

## 在链路里的位置

    chunk_tags（介词表） ──► chunker（本模块） ──► hnc.classify（句类判定）
    E 语义块感知            语义块边界 + 角色        句类码

`chunk_tags.py` 只回答"这个介词标记哪类块"，`hnc.py` 只回答"给定角色
结构输出什么句类码"，中间缺的就是"**怎么从一段中文切出块、并给每个
块定角色**"。本模块补这一段。

## 规则来源

- 主/辅/两可角色：CCL-99-003 的三大表（`chunk_tags.TAG_TABLE`）。
- 主块 E=特征、A=作用者、B=对象、C=内容：`[概要]` 二(二)。
- "v+着+v" 首动词降为辅块：`[张]` §3（"拍着手笑""拉长了脸瞧"）。
- 无标记时间辅块：`[张]` §2 第四形式。
- 纯主标记符（把/被/将…）引入的是**规范格式**：宾语前置，
  不产生新块，只改变后随成分的块角色 —— `[张]` §4「主语义块标记符」。

## 设计取舍（必须写清楚）

这是**启发式切分**，不是完整句类分析。三条理由：

1. 完整句类分析依赖 `[概要]` 的九模块和 `rule.lib`，后者未分发。
2. `[张]` 只保证"81% 的介词可直接定块类"，剩下 19% 要句类知识。
3. 本项目真正要的是 M3 的**可测量输出**，不是语言学完备性。

输出带 `confidence`：标记符直接给出的块是高置信，靠位置推断的 E 块与
无标记辅块是低置信。

## 与 hnc.classify 的接口缺口（诚实边界）

`hnc.classify` 吃的是二进制里的 13 字节结构体（`role_a`/`role_b`/
`participants`/`spare`/`role2`），那是 ASC 引擎**内部**的量化编码，其
整数值 → 语言学角色的映射在缺失的 `SENTEN.LIB` 里。所以本模块**不伪造**
这些整数，只输出语言学角色，并提供 `to_frame_hint()` 给出可在被外部
校准后使用的**候选**，默认明确返回"无法映射"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from .chunk_tags import TagRole, maximal_tag_at, roles_of

__all__ = [
    "ChunkRole",
    "Chunk",
    "Chunking",
    "SUBCATEGORY_OF_TAG",
    "SentenceChunker",
]


#: 时间词。用于识别无标记条件辅块（论题 7 第三条：“其中主要是
#: 时间条件辅块”）。这不是 HNC 的 K(jm) 概念判定，只是字面启发式。
_TIME_WORDS = (
    "现在", "目前", "今天", "明天", "昨天", "后天", "去年", "今年",
    "明年", "之后", "之前", "以前", "以后", "当时", "此时", "届时",
    "同时", "一旦", "将来", "过去", "早上", "晚上", "中午",
)


#: 时间单位。“一星期后 / 两小时前”里的“星期”“小时”是双字单位，
#: 早期只 rstrip 单字（“天年月日周”），这类就判不出来。
_TIME_UNITS = (
    "秒钟", "分钟", "小时", "时辰", "星期", "礼拜", "季度",
    "天", "年", "月", "日", "周", "秒", "分", "点",
)


def _strip_time_unit(s: str) -> str:
    for u in _TIME_UNITS:
        if s.endswith(u):
            return s[: -len(u)]
    return s


#: 中文数字（含位值），用于“三天后 / 二零零五年之前”这类相对时间
_CN_DIGITS = "零一二三四五六七八九十百千万两"


def _is_numberish(s: str) -> bool:
    """“三”“15”“二零零五”都算数字。

    早期版本直接 `s.isdigit()`，而汉语里写的是中文数字，
    “三天后”的“三”不是 ASCII 数字，判断永远失败。
    """
    return bool(s) and all(ch.isdigit() or ch in _CN_DIGITS for ch in s)


class ChunkRole(str, Enum):
    """语义块角色。

    主块取 `[概要]` 的 E/A/B/C 记法；辅块取 `[张]` §2 的英文缩写
    （Ms 方式 / In 工具 / Wy 途径 / Cn 条件 / Re 参照 / Pr 因 / Rt 果）。
    """

    E = "E"
    A = "A"
    B = "B"
    C = "C"
    BK_MS = "Ms"
    BK_IN = "In"
    BK_WY = "Wy"
    BK_CN = "Cn"
    BK_RE = "Re"
    BK_PR = "Pr"
    BK_RT = "Rt"
    UNKNOWN = "?"

    @property
    def is_main(self) -> bool:
        return self in (ChunkRole.E, ChunkRole.A, ChunkRole.B, ChunkRole.C)


# ---------------------------------------------------------------------------
# 介词 → 辅块子类（**推断**，非论文原表）
#
# `[张]` 只给了主/辅/两可三分，未把 43 个纯辅介词细分为 7 类。下面的
# 细分依据是介词固有逻辑义（论文正文举例："按照"表参照、"为了"表目的），
# 因此置信度低于角色判定本身。
# ---------------------------------------------------------------------------

SUBCATEGORY_OF_TAG: dict[str, ChunkRole] = {
    # 方式
    "按": ChunkRole.BK_MS, "按照": ChunkRole.BK_MS, "依": ChunkRole.BK_MS,
    "依照": ChunkRole.BK_MS, "照": ChunkRole.BK_MS, "照着": ChunkRole.BK_MS,
    "遵照": ChunkRole.BK_MS, "本着": ChunkRole.BK_MS, "凭": ChunkRole.BK_MS,
    "凭借": ChunkRole.BK_MS,
    # 工具（材料/原料/动力按 `[张]` 归工具）
    "用": ChunkRole.BK_IN, "借": ChunkRole.BK_IN,
    # 途径
    "通过": ChunkRole.BK_WY, "经过": ChunkRole.BK_WY, "经": ChunkRole.BK_WY,
    "沿": ChunkRole.BK_WY, "沿着": ChunkRole.BK_WY, "顺着": ChunkRole.BK_WY,
    "离": ChunkRole.BK_WY,
    # 条件（含时间/环境/社会/物质/前提 —— `[张]` §2）
    "在": ChunkRole.BK_CN, "当": ChunkRole.BK_CN, "当着": ChunkRole.BK_CN,
    "趁": ChunkRole.BK_CN, "趁着": ChunkRole.BK_CN, "临": ChunkRole.BK_CN,
    "每当": ChunkRole.BK_CN, "待": ChunkRole.BK_CN, "至": ChunkRole.BK_CN,
    "自": ChunkRole.BK_CN, "自从": ChunkRole.BK_CN, "及至": ChunkRole.BK_CN,
    "随着": ChunkRole.BK_CN, "只限": ChunkRole.BK_CN,
    # 参照
    "根据": ChunkRole.BK_RE, "据": ChunkRole.BK_RE, "依据": ChunkRole.BK_RE,
    "基于": ChunkRole.BK_RE, "鉴于": ChunkRole.BK_RE,
    "像……一样": ChunkRole.BK_RE, "正如……那样": ChunkRole.BK_RE,
    "拿……来说": ChunkRole.BK_RE, "就……而言": ChunkRole.BK_RE,
    # 因
    "因": ChunkRole.BK_PR, "因为": ChunkRole.BK_PR,
    "由于": ChunkRole.BK_PR, "为着": ChunkRole.BK_PR,
    # 果 / 目的
    "为了": ChunkRole.BK_RT,
}

#: 搭配型标记符的块尾（`[张]` §2 第二形式："拿……来说"是真尾）。
TAG_TAILS: dict[str, str] = {
    "拿": "来说", "就": "而言", "像": "一样", "正如": "那样",
}

# ---------------------------------------------------------------------------
# 词典
# ---------------------------------------------------------------------------

#: 不作辅块标记的介词。它们要么是单字且与构词成分高度混淆，
#: 要么是 `[张]` 表中的兼类项，但在启发式下单字误匹配的代价
#: （把“自己”“由于”切碎）远高于收益。只保留双字及以上标记符
#: 作为辅块起点，辅以 `SINGLE_CHAR_SAFE` 白名单。
SINGLE_CHAR_SAFE = frozenset(("把", "被", "将"))

#: 单字介词里可安全作为辅块标记的（含义明确、不易构词混淆）。
#: `[张]` 表中其余单字项（自/于/打/给/向/朝/以/从/经/据/依/照/
#: 凭/临/待/至/离/沿/因/用/当/按）均不入此表：实测它们在本项目的
#: 目标文本（短句、含人名地名）中误切率过高。
SINGLE_CHAR_SECONDARY = frozenset(
    ("在", "用", "因", "据", "按", "依", "照", "沿", "离")
)

#: 孤立时不成块的单字介词（`_chunk_clause` 第 3 步过滤用）。
#: 它们被识别为标记符但不作为辅块起点，剩余孤字也不应成为一个 B 块。
_ORPHAN_MARKERS = frozenset(
    "于自打向朝以从经给至于为把被将连跟同和与就让叫替管往对"
)
TIME_WORDS = frozenset((
    "昨天", "今天", "明天", "前天", "后天", "现在", "刚才", "以后",
    "之前", "之后", "去年", "今年", "明年", "上午", "下午", "晚上",
    "早上", "中午", "最近", "当时", "将来", "过去", "目前", "如今",
    "次日", "昨晚", "本周", "上月", "下半年",
))

#: 谓语候选。刻意只收**双字及以上**词：单字动词（"去/来/打/看"）在中文里
#: 与构词成分高度混淆（"空投"含"投"、"会议"不含动词），收进来会把名词
#: 内部切开。宁可漏判 E（退化为低置信 B 块），也不误判。
DEFAULT_VERBS = frozenset((
    "发现", "认为", "表示", "指出", "希望", "要求", "同意", "反对",
    "完成", "进行", "实现", "开始", "结束", "出现", "发生", "成为",
    "打断", "召开", "举行", "参加", "通过", "决定", "提出", "提供",
    "获得", "取得", "造成", "导致", "引起", "产生", "达到", "超过",
    "研究", "分析", "处理", "解决", "建立", "发展", "提高", "增加",
    "减少", "改变", "影响", "支持", "帮助", "保护", "打击", "检查",
    "空投", "采访", "报道", "宣布", "承认", "否认", "担心", "害怕",
    "喜欢", "讨厌", "知道", "明白", "理解", "相信", "怀疑", "注意",
    "照顾", "帮助", "批评", "表扬", "鼓励", "安慰", "打扰", "欺负",
    "包揽", "承担", "负责", "参与", "加入", "离开", "到达", "进入",
    "回来", "出去", "进来", "上去", "下来", "起来", "得到", "失去",
    "放弃", "拒绝", "接受", "告诉", "询问", "回答", "解释", "说明",
    "空投", "运输", "转移", "接收", "发送", "传递", "交付", "征集",
    # 单字谓语白名单：极度保守。只收 `[张]` §3 例子直接需要、且几乎
    # 不会出现在名词内部的动词。实测“笑/走/开/拉”会误切“笑容”“走
    # 廊”“开会”“拉长”，故只留下面这几个：
    "笑", "哭", "坐", "站", "吃", "睡",
))

_CLAUSE_RE = re.compile(r"[^，。！？；,.!?;、\n]+")
_SENT_END_RE = re.compile(r"[，。！？；,.!?;、\n]+")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Chunk:
    """一个语义块。

    `text` 是块**内容**（不含标记符）；`marker` 保留标记符本身，因为
    `[张]` 强调标记符携带独立逻辑意义，是块类型的主要依据。
    """

    role: ChunkRole
    text: str
    start: int
    end: int          # end 含标记符，便于回取原文切片
    marker: str | None = None
    confidence: float = 1.0

    @property
    def from_marker(self) -> bool:
        return self.marker is not None

    @property
    def marker_roles(self) -> TagRole:
        return roles_of(self.marker) if self.marker else TagRole.NONE


@dataclass
class Chunking:
    """一次分块结果。"""

    text: str
    chunks: list[Chunk] = field(default_factory=list)

    @property
    def main(self) -> list[Chunk]:
        return [c for c in self.chunks if c.role.is_main]

    @property
    def secondary(self) -> list[Chunk]:
        return [c for c in self.chunks if not c.role.is_main]

    @property
    def pattern(self) -> str:
        """块序列签名，如 `A+E+B`。`[王]` 的语法相似度(式 4)需要它。"""
        return "+".join(c.role.value for c in self.chunks)

    def pattern_of_main(self) -> str:
        """只取主语义块的签名。式 (4) 的 `sim_gsem` 只比主块。"""
        return "+".join(c.role.value for c in self.main)

    def to_frame_hint(self) -> None:
        """留给 `hnc.classify` 的适配点。

        不返回 `Frame`：`role_a`/`role_b`/`spare`/`role2` 的整数值编码
        在缺失的 `SENTEN.LIB` 里，任何取值都是编造。等哪天拿到该文件或
        更多反汇编证据，在这里实现映射，并让测试逐条对齐二进制。

        当前恒返回 `None`，这是**刻意**的：让调用方看见"没有映射"，
        而不是拿到一个看起来能跑的猜值。
        """
        return None


# ---------------------------------------------------------------------------
# 分块器
# ---------------------------------------------------------------------------


class SentenceChunker:
    """按标记符切分语义块。

    每个分句内按 `[张]` 的"三部曲"近似处理：

    1. **语义块感知**：扫描标记符。辅/两可标记符起一个辅块，其边界
       到下一个标记符、下一个谓语、或分句尾。
    2. **E 块定位**：未被标记符覆盖的部分里找谓语。前一首动词是
       "v+着" 时按 `[张]` §3 降为辅块，第二个动词才作 E。
    3. **A/B 归属**：E 之前除辅块外的剩余成分为 A，E 之后为 B。
    """

    def __init__(self, verbs: Iterable[str] | None = None) -> None:
        self._verbs: frozenset[str] = frozenset(verbs or DEFAULT_VERBS)
        self._max_verb = max((len(v) for v in self._verbs), default=0)

    # -- 公开 ------------------------------------------------------------

    def chunk(self, sentence: str) -> Chunking:
        out = Chunking(text=sentence)
        for m in _CLAUSE_RE.finditer(sentence):
            out.chunks.extend(self._chunk_clause(m.group(), m.start()))
        out.chunks.sort(key=lambda c: c.start)
        return out

    # -- 分句 ------------------------------------------------------------

    def _chunk_clause(self, clause: str, base: int) -> list[Chunk]:
        chunks: list[Chunk] = []
        # 被切走的区间，用占位符标记，避免重复切分
        taken = bytearray(len(clause))

        # --- 0. 整个分句就是一个无标记条件辅块 -----------------------
        # “如果下雨，比赛就取消”里的“如果下雨”被逗号独立后无谓语，
        # 旧逻辑退化成了无动词 B 块。论题 7 第三条说无标记辅块就是
        # 条件辅块，故这里先判一次，命中就不再往主块流程走。
        if self._looks_conditional(clause):
            cut = self._conditional_prefix_end(clause)
            # 整个分句无条件成分（且无谓语）→ 整句就是条件辅块
            if not self._find_verbs_safe(clause):
                return [Chunk(ChunkRole.BK_CN, clause, base,
                              base + len(clause), confidence=0.4)]
            # 条件前缀后紧跟谓语（“三天后我们将开始施工”）→ 前后拆开
            if 0 < cut < len(clause):
                rest = self._chunk_main(clause[cut:], base + cut)
                return ([Chunk(ChunkRole.BK_CN, clause[:cut], base,
                               base + cut, confidence=0.4)] + rest)

        # --- 1. 标记符驱动的辅块 -------------------------------------
        i = 0
        while i < len(clause):
            # 多字动词优先于单字标记符（“离开”vs“离”）。
            if i > 0:
                hi = min(self._max_verb, len(clause) - i)
                if any(clause[i : i + n] in self._verbs
                       for n in range(hi, 1, -1)):
                    i += 1
                    continue
            hit = maximal_tag_at(clause, i)
            if not hit:
                i += 1
                continue
            tag, n = hit
            if taken[i]:
                i += n
                continue
            # 纯主标记符（把/被/将…）：规范格式，宾语前置，不产生辅块。
            # 只跳过标记符本身，让后随成分继续走主块判定。
            if tag.roles == TagRole.MAIN:
                taken[i : i + n] = b"\x01" * n
                i += n
                continue
            if not (tag.roles & (TagRole.SECONDARY | TagRole.DUAL)):
                i += n
                continue
            if not self._usable_marker(tag, n):
                i += n
                continue

            body_start, body_end = i + n, self._aux_end(clause, i + n, taken)
            sub = SUBCATEGORY_OF_TAG.get(tag.surface)
            chunks.append(
                Chunk(
                    role=sub or ChunkRole.UNKNOWN,
                    text=clause[body_start:body_end].strip(),
                    start=base + i,
                    end=base + body_end,
                    marker=tag.surface,
                    # 角色由标记符直接给出 → 高置信；子类为推断 → 次一档
                    confidence=1.0 if sub else 0.7,
                )
            )
            taken[i:body_end] = b"\x01" * (body_end - i)
            i = body_end

        # --- 2. 无标记时间辅块（仅在未被占用的区间里找） --------------
        free = self._free_spans(clause, taken)
        for span_start, span_end in free:
            segment = clause[span_start:span_end]
            for w in TIME_WORDS:
                pos = segment.find(w)
                if pos < 0:
                    continue
                abs_pos = span_start + pos
                if any(taken[abs_pos : abs_pos + len(w)]):
                    continue
                chunks.append(
                    Chunk(
                        role=ChunkRole.BK_CN,
                        text=w,
                        start=base + abs_pos,
                        end=base + abs_pos + len(w),
                        confidence=0.5,
                    )
                )
                taken[abs_pos : abs_pos + len(w)] = b"\x01" * len(w)

        # --- 3. 剩余文本切主块 ---------------------------------------
        for span_start, span_end in self._free_spans(clause, taken):
            segment = clause[span_start:span_end].strip()
            if not segment:
                continue
            offset = clause.find(segment, span_start)
            # 孤立单字（多为未入白名单的单字介词，如“于”）不单独成块，
            # 它本身不携带可判定的语义信息，挂在相邻块上只会稀释信号。
            if len(segment) == 1 and segment in _ORPHAN_MARKERS:
                continue
            chunks.extend(self._chunk_main(segment, base + offset))

        return chunks

    def _aux_end(self, s: str, start: int, taken: bytearray) -> int:
        """辅块右边界：下一个标记符 / 下一个谓语 / 分句尾。"""
        i = start
        while i < len(s):
            if taken[i]:
                return i
            if i > start and self._is_boundary(s, i):
                return i
            i += 1
        return len(s)

    def _is_boundary(self, s: str, i: int) -> bool:
        """s[i:] 处是否是一个新块的开头。

        优先级：多字动词 > 多字标记符 > 单字标记符。把双字动词排在单字
        标记符前面是必须的——“离开/开会”里的“离/开”不能被当成辅助标记
        （实测错误：“离开会场”→辅块“开会场”）。
        """
        hi = min(self._max_verb, len(s) - i)
        for n in range(hi, 1, -1):
            if s[i : i + n] in self._verbs:
                return True
        hit = maximal_tag_at(s, i)
        return bool(hit and self._usable_marker(hit[0], hit[1]))

    @staticmethod
    def _usable_marker(tag, span: int) -> bool:
        """该标记符是否可作为块边界。单字辅类标记符默认不切。"""
        if tag.roles == TagRole.MAIN:
            return False  # 纯主标记符不切块，只改后随成分角色
        if not (tag.roles & (TagRole.SECONDARY | TagRole.DUAL)):
            return False
        if span == 1:
            return tag.surface in SINGLE_CHAR_SECONDARY
        return True

    @staticmethod
    def _free_spans(s: str, taken: bytearray) -> list[tuple[int, int]]:
        spans, start = [], None
        for i, t in enumerate(taken):
            if not t and start is None:
                start = i
            elif t and start is not None:
                spans.append((start, i))
                start = None
        if start is not None:
            spans.append((start, len(s)))
        return spans

    # -- 主块 ------------------------------------------------------------

    def _chunk_main(self, text: str, base: int) -> list[Chunk]:
        verbs = self._find_verbs_safe(text)

        # `[张]` §3：“v+着+v”。首动词不必在词汇表内——汉语“v+着”
        # 形式本身（任何一个字 + “着”在句首位置）就已经是充分信号，
        # 而首动词的具体语义反而无关紧要（它整体降为方式辅块）。
        # 所以这里先做“字+着”形状检测，再退回词语表检测。
        zhe_end = self._zhe_prefix_end(text)
        if zhe_end is not None and len(verbs) >= 1:
            e_pos, e_end = verbs[0]
            if e_pos >= zhe_end:
                # `[张]` §3 的例子“他拍着手笑”：主语属于 A（作用者），
                # “拍着”才是辅块，二者必须分开。主语长度取“着”前
                # 第一个动词之前的字符数；启发式下按 1～2 字估计。
                subj_end = max(0, zhe_end - 2)  # “x着”占 2 字
                out: list[Chunk] = []
                if subj_end > 0:
                    out.append(Chunk(ChunkRole.A, text[:subj_end].strip(),
                                     base, base + subj_end, confidence=0.5))
                out.append(Chunk(ChunkRole.BK_MS,
                                text[subj_end:zhe_end].strip(),
                                base + subj_end, base + zhe_end,
                                confidence=0.5))
                out.append(Chunk(ChunkRole.E, text[e_pos:e_end],
                                base + e_pos, base + e_end, confidence=0.8))
                out.extend(self._tail_chunk(text, e_end, base))
                return out

        if not verbs:
            return [Chunk(ChunkRole.B, text, base, base + len(text),
                          confidence=0.4)]

        e_pos, e_end = verbs[0]
        out: list[Chunk] = []

        before = text[:e_pos].strip()
        if before:
            # 前置成分是否已是"v+着"（单动词情形也降为方式辅块）
            if before.endswith("着") and len(before) > 1:
                out.append(Chunk(ChunkRole.BK_MS, before, base,
                                 base + e_pos, confidence=0.5))
            elif self._looks_conditional(before):
                # 论题 7 答 3 第三条（`book307.htm` 原文）：
                #   "不带标记的辅块必然是以 K(jm) 为内容的条件辅块。
                #    其中主要是时间条件辅块。"
                # 所以 E 前无标记、且形似条件/时间的成分必须判 Cn，
                # 不能一律归为 A。置信度压低，因为这只靠形状推断。
                cut = self._conditional_prefix_end(before)
                if 0 < cut < len(before):
                    # “三天后 + 我们”这类粘连：只把时间前缀当辅块，
                    # 其余仍当 A。早期版本把整段吞进 Cn，主语就消失了。
                    out.append(Chunk(ChunkRole.BK_CN, before[:cut], base,
                                     base + cut, confidence=0.4))
                    out.append(Chunk(ChunkRole.A, before[cut:],
                                     base + cut, base + e_pos,
                                     confidence=0.5))
                else:
                    out.append(Chunk(ChunkRole.BK_CN, before, base,
                                     base + e_pos, confidence=0.4))
            else:
                out.append(Chunk(ChunkRole.A, before, base,
                                 base + e_pos, confidence=0.6))

        out.append(Chunk(ChunkRole.E, text[e_pos:e_end], base + e_pos,
                         base + e_end, confidence=0.8))
        out.extend(self._tail_chunk(text, e_end, base))
        return out

    def _tail_chunk(self, text: str, start: int, base: int) -> list[Chunk]:
        tail = text[start:].strip()
        if not tail:
            return []
        pos = text.rfind(tail)
        # 论题 7 第一条（`book307.htm`）：“辅块……绝不会出现在 E 块之后。”
        # 所以 E 之后的一律是主语义块 B，不可能在这里切出辅块。
        return [Chunk(ChunkRole.B, tail, base + pos,
                      base + pos + len(tail), confidence=0.6)]

    def _looks_conditional(self, text: str) -> bool:
        """启发式：这段前置成分是否像一个无标记的条件辅块。

        依据论题 7 第三条：“不带标记的辅块必然是以 K(jm) 为内容的
        条件辅块，其中主要是时间条件辅块。” 所以只认两类形状：
        时间词，以及“……时/……后/……前/……的话”这类条件尾。

        注意两件事：
        1. 判定用 **`startswith` 而非 `endswith`**。前置成分往往是
           “时间词 + 主语”粘在一起（“三天后我们”），所以时间标记在
           开头。早期版本用 endswith，直接漏判。
        2. 这是从“无标记辅块＝K(jm)”倒推的**形状启发式**，不是原文
           判据。原文的判据是概念类别 K(jm)，需要 HNC 词知识库。
        """
        if len(text) < 2:
            return False
        if text.startswith(_TIME_WORDS):
            return True
        # “N 天后 / N 年后 / N 月后”这类相对时间
        for suf in ("后", "前", "之后", "之前"):
            i = text.find(suf)
            if 0 < i <= 5 and _is_numberish(_strip_time_unit(text[:i])):
                return True
        return text.startswith(("当", "一旦", "如果", "要是", "除非"))

    @staticmethod
    def _conditional_prefix_end(text: str) -> int:
        """条件/时间前缀的结束位置；用于把“三天后我们”拆成两段。

        返回 0 表示整段都是条件成分（不应再拆）。
        """
        for w in _TIME_WORDS:
            if text.startswith(w) and len(text) > len(w):
                return len(w)
        # “N 天后 / N 年后”这类
        for suf in ("之后", "之前", "后", "前"):
            i = text.find(suf)
            if 0 < i <= 5 and _is_numberish(_strip_time_unit(text[:i])):
                return i + len(suf)
        for w in ("如果", "要是", "除非", "一旦", "当"):
            if text.startswith(w) and len(text) > len(w):
                return len(w)
        return 0

    def _zhe_prefix_end(self, text: str) -> int | None:
        """主语之后的“v+着”起始段的结束位置（不含主语）。

        `[张]` §3 的例子是“他拍着手笑”：主语“他” + 辅块“拍着” + E“笑”。
        本函数返回“v+着”的右边界；找不到返回 None。

        仅在“着”位于前 6 个字内时认（避免把句中的“着”当信号）。
        """
        p = text.find("着")
        if p < 1 or p > 5:
            return None
        # “着”前必须是单个汉字（v+着），不能是“随着/沿着”这类介词
        if text[p - 1] in "随沿为接按照":
            return None
        return p + 1

    def _find_verbs(self, text: str) -> list[tuple[int, int]]:
        """返回 [(start, end)]，按出现位置升序、互不重叠、最长优先。"""
        found: list[tuple[int, int]] = []
        i = 0
        while i < len(text):
            hit = None
            for n in range(min(self._max_verb, len(text) - i), 0, -1):
                if text[i : i + n] in self._verbs:
                    hit = (i, i + n)
                    break
            if hit:
                found.append(hit)
                i = hit[1]
            else:
                i += 1
        return found

    def _find_verbs_safe(self, text: str) -> list[tuple[int, int]]:
        """单字动词只在块边界上认可。

        单字动词的主要风险是把名词内部切开（“笑容”→笑+容）。
        汉语里单字谓语很少后面紧接双字名词而不带宾语标记，所以只在
        **句末或后随“着/了/过”或单个词**时认可；否则舍弃该单字候选。
        """
        out = []
        for start, end in self._find_verbs(text):
            if end - start == 1:
                nxt = text[end : end + 1]
                if nxt and nxt not in "着了过，。！？；、":
                    continue
            out.append((start, end))
        return out
