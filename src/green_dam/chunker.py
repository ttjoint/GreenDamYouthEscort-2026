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

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
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

    @property
    def math_symbol(self) -> str:
        """数学表示式里的符号（论题 34《语义块表示式及其应用》）。

        原文表格：

            语义块类型          物理表示              数学表示
            特征语义块      X,P,T,Y,R,S,D,jD          E
            广义对象语义块    A,B,C             JK, JKm, JKn
            辅语义块      Ms,In,Wy,Re,Cn,Pr,Rt  fK, fKm, fKmn
            两可语义块        RtB, ReC       fJK, fJKm, fJKmn

        `math_symbol` 只给**类别层**的符号（E/JK/fK/fJK），不带序号。
        序号需要块的出现次序，那是 `Chunking.math_pattern()` 的事。

        两可块（RtB/ReC）目前不产出——它们是“果/参照”与“对象/内容”
        之间的两可情形，需要句类知识才能定，本模块不猜。
        """
        if self is ChunkRole.E:
            return "E"
        if self.is_main:
            return "JK"
        if self is ChunkRole.UNKNOWN:
            return "?"
        return "fK"


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
#: 含单字 l 标记、但**不能**按标记切开的双字词。
#:
#: 论文 11.1 只说 l 类概念用作切分标志，没有提供"何时是词、何时是
#: 标记"的判据（那需要词表）。这里只收实测造成误切的：
#:
#:   原因/因为/由于 → 误切"天气原 | 因"
#:   同学/同事/同时 → 误切"同 | 学"（"同"是主标记符）
#:   现在/正在/存在 → 误切"现 | 在"
#:   目的/的确/有的 → 误切"有 | 的"
_NON_TAG_WORDS = frozenset((
    "原因", "因为", "由于", "因此", "因而", "因果",
    "同学", "同事", "同时", "同意", "同等", "同一", "相同", "共同",
    "现在", "正在", "存在", "实在", "所在", "内在", "外在", "潜在",
    "目的", "的确", "有的", "所得", "所有", "有机", "有利", "有效",
    "不是", "只是", "但是", "还是", "要是", "就是", "总是", "于是",
    "何况", "如何", "为何", "结果", "结合", "适合", "综合", "结构",
    "理解", "理论", "理想", "理由", "管理", "办理", "处理", "治理",
    "通过", "经过", "不过", "超过", "路过", "难过",
    "当地", "当前", "当然", "当做", "担当", "适当", "相当",
    "用户", "用途", "费用", "作用", "采用", "使用", "适用", "应用",
    "方向", "向上", "向往", "转向", "倾向", "指向", "一向",
    "对于", "对岸", "对抗", "针对", "相对", "绝对",
    "离开", "离别", "分离", "距离", "脱离", "隔离",
    "按照", "按时", "按理", "按键",
    "依靠", "依据", "依旧", "依照",
    "照例", "照样", "照相",
    "沿途", "沿线", "沿用",
    "关于", "关系", "关注", "关联", "有关", "无关",
    "至于", "甚至", "直至", "截止", "至此",
    "除了", "清除", "排除", "删除",
    "为了", "成为", "作为", "认为", "以为", "行为", "因为",
    "本着", "本身", "根本", "基本", "资本", "样本",
    "凭着", "凭借",
    "朝着", "明朝", "朝代",
    "沿着", "沿海", "沿用",
))

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
"发现", "认为", "表示", "指出", "希望", "要求", "同意", "反对", "完成", "进行", "实现", "开始", "结束", "出现", "发生", "成为", "打断", "召开", "举行", "参加", "通过", "决定", "提出", "提供", "获得", "取得", "造成", "导致", "引起", "产生", "达到", "超过", "研究", "分析", "处理", "解决", "建立", "发展", "提高", "增加", "减少", "改变", "影响", "支持", "帮助", "保护", "打击", "检查", "空投", "采访", "报道", "宣布", "承认", "否认", "担心", "害怕", "喜欢", "讨厌", "知道", "明白", "理解", "相信", "怀疑", "注意", "照顾", "批评", "表扬", "鼓励", "安慰", "打扰", "欺负", "包揽", "承担", "负责", "参与", "加入", "离开", "到达", "进入", "回来", "出去", "进来", "上去", "下来", "起来", "得到", "失去", "放弃", "拒绝", "接受", "告诉", "询问", "回答", "解释", "说明", "运输", "转移", "接收", "发送", "传递", "交付", "征集", "看书", "读书", "写仚", "写字", "读写", "看报", "听课", "上课", "学习", "工作", "生活", "休息", "睡觉", "起床", "出门", "回家", "吃饭", "喝水", "开会", "说话", "打电", "走路", "跑步", "唱歌", "讲述", "介绍", "讨论", "交流", "沟通", "协商", "汇报", "报告", "安排", "布置", "实施", "执行", "落实", "推行", "开展", "遇刐", "碰到", "遇见", "感觉", "觉得", "意识", "记得", "看到", "听见", "闻到", "想到", "考虑", "思考", "忘记", "选择", "试探", "尝试", "努力", "坚持", "继续", "停止", "取消", "确认", "审核", "批准", "签订", "维修", "维护", "改善", "提升", "降低", "扩大", "缩小", "加强", "削弱", "消失", "增长", "下降", "上升", "下跌", "变化", "变动", "笑", "哭", "坐", "站", "吃", "睡", "遇到", "受到", "遭到", "送到", "找到", "听到", "读到", "买到", "拿到", "收到", "提交", "发出", "弄到", "改到", "学会", "掌握", "熟悉", "了解", "暴露"
))

#: 从真实语料抽出的扩展动词表（`tools/build_verb_table.py` 生成）。
#:
#: 上面那份手写表只有 208 条、单字仅 6 个，导致真实语料上
#: **99.9% 的退化是「无谓语候选」**（`docs/corpus-eval.md`）。
#: 汉语单字动词（看/说/走/买/拿/做/去/来）手写不全，改用体标记
#: （着/了/过）从语料自动抽：`看着`->`看`、`笑了`->`笑`。
#:
#: 单字动词的误切风险由 `_find_verbs_safe` 的「仅块边界认可」兜底，
#: 而不是靠「不收录」来回避。
_VERB_TABLE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "verb-table.json"
)


def _load_verb_table() -> frozenset[str]:
    """读 `data/verb-table.json`；缺失时返回空集（不影响原有行为）。"""
    if not _VERB_TABLE_PATH.exists():
        return frozenset()
    try:
        blob = json.loads(_VERB_TABLE_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return frozenset()
    return frozenset(blob.get("verbs") or {})


#: 实际使用的动词表 = 手写表 ∪ 语料抽取表。
EXTENDED_VERBS = DEFAULT_VERBS | _load_verb_table()

#: 晋耀红《基于HNC理论的句类分析系统的设计与实现》§2.1 的“指定字”。
#: 原文：
#:   "本系统设置了 7 个指定字：的、了、和、是、有、不、在，
#:    都是汉语的高频字，而且它们对 E 语义块感知影响很大，
#:    对它们采取指定的方式，可以大大减轻 E 语义块感知的模糊性。"
#:
#: 落地方式：这七个字不参与构词猜测（不当辅块内容、不当块首），
#: 但可用于定位块边界。这直接修掉了本模块的一类错误：
#: “表示反对”里的“反对”被切成独立 B 块、“看书”被吞进辅块。
DESIGNATED_CHARS = frozenset("的了和是有不在")

_CLAUSE_RE = re.compile(r"[^，。！？；,.!?;、\n]+")
_SENT_END_RE = re.compile(r"[，。！？；,.!?;、\n]+")

#: 配对引号。左、右引号各自成对，用于分句前的预处理。
#: 小说里「他说：‘……。’」这种嵌套引用会让分句器切出「」」
#: 或「「啊」这种碎片，它们没有谓语，随后一律退化成无标记块。
#: 实测成人语料 99,639 个退化分句里，前几名全是这类碎片
#: （「」」×5123、"」×2454、「啊×368）。
_QUOTE_PAIRS = (
    ("「", "」"),
    ("『", "』"),
    ("“", "”"),
    ("‘", "’"),
    ("\u300c", "\u300d"),
)

#: 纯语气词/感叹词：没有语义内容，不能作分句。
#: 它们常常因为引号被切开而单独成句（「啊」「嗯」「喂」）。
_INTERJECTIONS = frozenset(
    "啊吧呢吗吧嘛哦噢喔嗯唉哎哎呀喂嘿哈呵嘻哇呀哪么的了着过"
)

#: 句首附着成分：连词、时间/语气副词。
#: 它们被逗号切开后会单独成句，但自身没有谓语，必然退化成无标记块。
#: 实测退化样本前几名全是这类：`但`、`不过`、`这时`、`所以`、`突然`。
#: 处理方式不是丢弃，而是**粘到后一个分句**（见 `_split_clauses`）。
#: 依据：论文说这类成分是 `l` 标记之外的时间/条件修饰，归入后续
#: 主块的辅块（论题 7）。粘上去比丢掉保留的信息多。
_LEADING_ATTACH = frozenset(
    "但 不过 但是 可是 然而 于是 所以 因此 因为 如果 即使 虽然 然后 "
    "后来 这时 这时 现在 突然 忽然 当然 很快 同时 事实上 其实 "
    "而且 并且 而且 况且 否则 不然 虽然 即便 无论 不管 只要 除非 "
    "接着 随即 立刻 马上 随即 终于 果然 显然 似乎 大概 也许 或许".split()
)


#: 时间/情状状语。它们在逗号前很常见，本身没有谓语，单独成句必然退化。
#:
#: 实测来源：`tools/diagnose_chunker.py` 在 10 个小说文件（40.9 万分句）
#: 上跑出的退化碎片排行，纯状语类占 4.2%（10,139 次）：
#:
#:     此刻 605   就在这时 488   顿时 435   与此同时 408   在这一刻 375
#:     这一次 287  此时 256      这一刻 219   最终 211        这一日 169
#:     转眼间 168  在这一瞬间 166  刹那间 140   此时此刻 139    这一幕 129
#:     许久 118    没过多久 117   少顷 97    在这一刹那 90  瞬间 82
#:     片刻后 77  时间不长 78
#:
#: 这些词**全部**在论文的 K(jm)（时间/条件）语义类范围内，属于
#: 辅块内容而不是独立句子。分句层不应该在它们后面断开。
_TIME_ADVERBS = frozenset("""
此刻 此时 此时 这时 这时 这时 就在这时 就在此时 就在这时刻
在这一刻 在这一瞬间 在这一刹那 这一刻 这一瞬间 这一刹那
此时此刻 这个时候 那一瞬间 那一刹那
顿时 立刻 马上 随即 紧接着 随后 继而 跟着 旋即
与此同时 同时 同期
最终 终于 最后 末了 良久 许久 半晌 少顷 片刻 片刻后 片刻之后
没过多久 时间不长 不知过了多久 过了一会儿 过了一会 不多时
这一日 这一天 这一刻 这一次 这一切 这一幕 这一刻 这一世 这一番
一瞬间 一刹那 转瞬间 转眼间 转瞬之间 眨眼间 眨眼之间
刹那间 刹那之间 顷刻间 顷刻之间 蓦然间 蓦然之间 忽然间 猛然间
渐渐地 渐渐的 慢慢地 慢慢的 缓缓地 缓缓的 轻轻地 轻轻的
""".split())

#: 对白提示语：`淡淡的说道` / `喃喃自语道` / `笑道`。
#:
#: 实测占退化碎片 1.6%（3,940 次）：说道 1498、缓缓说道 312、
#: 轻声道 271、说完 262、道 240、笑道 237、沉声道 217、
#: 平淡的说道 201、喝道 145、喃喃自语道 123、恭敬的说道 87。
#:
#: ## 为什么这类要粘到**前**一句而不是后一句
#:
#: “淡淡道：‘走吧。’” 里提示语在引文**之前**，粘到后面正确。
#: 但 “‘走吧。’他说着站起身” 里提示语在引文**之后**——这种由
#: 引号闭合即断的规则已经切开，本表的词不会单独出现。
#: 所以统一按「粘到下一句」处理，覆盖前一种（占绝大多数）。
_SAY_VERBS = frozenset("""
说道 笑道 喝道 问道 答道 叫道 喊道 骂道 叹道 怒道 冷声道 沉声道
轻声道 低声道 大声道 急声道 缓声道 淡淡道 平淡的说道 冷冷的道
恭敬的说道 笑着说道 苦笑道 冷哼道 接口道 接着道 继续说 接着说道
喃喃自语道 自言自语道 低声道 沉声道
道 说完 说着 问道 答道 笑道 喝道 应道 回道 念道
""".split())


def _is_attach_fragment(core: str) -> bool:
    """这段是不是「不该独立成句」的附着成分。

    三类：

    1. 连词/副词（`_LEADING_ATTACH`）
    2. 时间/情状状语（`_TIME_ADVERBS`）
    3. 对白提示语（`_SAY_VERBS`）

    第 2、3 类不是加词表能解决的——它们**有内容但没谓语**，
    论文里属于辅块（K(jm) / 方式），分句层不该在这里断开。
    """
    if core in _LEADING_ATTACH:
        return True
    if core in _TIME_ADVERBS:
        return True
    if core in _SAY_VERBS:
        return True
    # “但/而/于是” + 时间状语（“但此刻”“而这时”）
    for pre in ("但", "而", "可", "却", "又", "就", "便", "才", "也", "还"):
        if core.startswith(pre) and core[1:] in _TIME_ADVERBS:
            return True
    # “缓缓说道” 这类「副词 + 说道」
    for suf in _SAY_VERBS:
        if len(suf) >= 2 and core.endswith(suf) and len(core) <= len(suf) + 2:
            return True
    return False


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

    def math_pattern(self) -> str:
        """数学结构表示式（论题 34 / 晋耀红式 2.1）。

        晋耀红《基于HNC理论的句类分析系统的设计与实现》把简单句写成：

            FJ = Σk（JKi）+ E + Σm（JKi）

        即：E 把句子分成前后两段，每段若干个 JK。辅块 fK 不进入
        这个结构式（它们不是句类函数的一部分），所以这里只排主块。

        例：“张三打断了李四的腿” → `JK + E + JK`
            “他认真地看书”        → `JK + E`
        """
        return " + ".join(c.role.math_symbol for c in self.main)

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
# 分句
# ---------------------------------------------------------------------------


def _split_clauses(text: str) -> list[tuple[int, int]]:
    """把整段拆成分句区间 `[(start, stop)]`。

    比直接用 `_CLAUSE_RE` 多做三件事，都是为了消除碎片句：

    1. **引号内部不切**。`「啊，我不知道。」` 按逗号切会得到 `「啊` 和
       `」然后就走开了`——前者是孤立感叹，后者引号跑到句首。

    2. **引号闭合即切**。`他说：「好。」` 之后直接断句，引文和后续
       叙述各成一句，而不是黏成一长串。

    3. **丢弃碎片**。只剩标点、或只剩一个语气词（啊/嗯/喂）的分句
       没有谓语，留着只会退化成无标记块，拉低有效率。

    返回**原字符串下标**，不做 copy，方便调用方回推位置。
    """
    n = len(text)
    opens = {p[0] for p in _QUOTE_PAIRS}
    closes = {p[1] for p in _QUOTE_PAIRS}
    breaks = "。，！？；：,.!?;:\n"

    spans: list[tuple[int, int]] = []
    start = 0
    depth = 0
    i = 0
    while i < n:
        ch = text[i]
        if ch in opens:
            depth += 1
            i += 1
            continue
        if ch in closes:
            if depth:
                depth -= 1
            i += 1
            if depth == 0:
                # 引文结束，在右引号后断开
                spans.append((start, i))
                start = i
            continue
        if depth == 0 and ch in breaks:
            spans.append((start, i))
            start = i + 1
        i += 1
    spans.append((start, n))

    out: list[tuple[int, int]] = []
    carry = None  # 待粘到下一句的孤立附着成分起点
    for a, b in spans:
        stripped = text[a:b].strip()
        if not stripped:
            continue
        # 只保留有汉字/字母/数字的分句
        if not any(ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
                   for ch in stripped):
            continue
        # 去掉两端引号与标点，再看内容
        core = stripped.strip(
            "\"'\u201c\u201d\u2018\u2019\u300c\u300d\u300e\u300f"
            "\u3002\uff0c\uff01\uff1f\uff1b\uff1a")
        if not core:
            continue
        # 只剩一个语气词 -> 碎片（保留多字的"好的""是吗"）
        if len(core) == 1 and core in _INTERJECTIONS:
            continue

        # 孤立附着成分（连词/副词/状语/提示语）不单独成句，粘到下一句
        if _is_attach_fragment(core):
            if carry is None:
                carry = a
            continue

        if carry is not None:
            # 把前面攒的附着成分和本句接着：保留中间原文
            out.append((carry, b))
            carry = None
        else:
            out.append((a, b))

    # 末尾还攒着附着成分（如“…。但是”）：它没有后续句可粘，
    # 丢弃；它自身没有谓语，留着也只会退化成无标记块。
    return out


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

    def __init__(self, verbs: Iterable[str] | None = None,
                 extended: bool = True) -> None:
        """`extended=True`（默认）使用语料抽取的扩展动词表。

        ## 为什么现在可以默认开

        扩展表（413 条）把单块退化率从 54.3%/66.9% 降到 41.0%/52.5%，
        但早先有两个缺陷挡住它：

        1. `问题`/`代表` 这类**名动兼类词**被当谓语，
           `他正在分析问题` 变成 `E=分析问题`。
           已由 `harvest_verbs.NOUN_AMBIGUOUS` 挡住。
        2. `V+着` 伴随动词抢走 E，`日本代表带着满意的笑容离开会场`
           变成 `E=带着`。
           已由 `_strip_zhe_companion` 挡住。

        两个都修好、回归测试稳定之后，扩展表才成为默认值。
        要给对照实验留口子就用 `extended=False` 退回 208 条手写表。
        """
        if verbs is not None:
            self._verbs = frozenset(verbs)
        elif extended:
            self._verbs = EXTENDED_VERBS
        else:
            self._verbs = DEFAULT_VERBS
        self._max_verb = max((len(v) for v in self._verbs), default=0)

    # -- 公开 ------------------------------------------------------------

    def chunk(self, sentence: str) -> Chunking:
        out = Chunking(text=sentence)
        for start, stop in _split_clauses(sentence):
            out.chunks.extend(self._chunk_clause(sentence[start:stop], start))
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
            # 单字标记符若只是某个词的内部字（“原因”的“因”、“由于”的
            # “于”），它就不是独立 l 标记。这里**跳过整段词**，而不是
            # 只跳一个字——否则辅块右边界会落在词中间。
            # 词内字不是 l 标记。这里**只跳过、不写 taken**——写 taken
            # 会让整段从 `_free_spans` 里消失，词也被丢掉（“同学”曾
            # 因此凭空蒸发，只剩“我们班”）。
            if n == 1:
                if i > 0 and clause[i - 1 : i + 1] in _NON_TAG_WORDS:
                    i += 1
                    continue
                if self._word_containing(clause, i) is not None:
                    i += 1
                    continue
            # 纯主标记符（把/被/将/同/与…）：规范格式，宾语前置，
            # 不产生辅块。这里**不**标记 taken：若把它当切点，
            # “同学”“与会”这类词就会被劈成“同 | 学”。lv 准则要求
            # l 标记只负责辅块，不应干扰主块的字面完整性。
            if tag.roles == TagRole.MAIN:
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
            body = clause[body_start:body_end].strip()
            # 空内容不建块。实测“由于天气原因”：标记符“由于”之后，
            # 扫描器又把“因”当成了另一个标记符，产生一个 text="" 的块。
            if not body:
                i += n
                continue
            chunks.append(
                Chunk(
                    role=sub or ChunkRole.UNKNOWN,
                    text=body,
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
            # 晋耀红 §2.1 的 7 个指定字：“的了和是有不在”。它们单独成段
            # 时不独立成块——“张三打断了李四的腿”曾切出独立的 B 块
            # “了”。归入相邻块更有意义。
            if len(segment) == 1 and segment in DESIGNATED_CHARS:
                continue
            chunks.extend(self._chunk_main(segment, base + offset))

        return chunks

    @staticmethod
    def _pick_e(verbs: list[tuple[int, int]]) -> tuple[int, int]:
        """从多个动词候选里选 E 块（论题 17《论连动句和兼语句》）。

        规则：若头两个动词**紧邻**（之间无宾语），取后一个作 E。
        理由是汉语的连动结构里，前一个动词多作引导/伴随（“表示
        反对”、“开始实施”、“建议采用”），真正的句类中心在后面。

        早期版本无条件取 verbs[0]，于是“他对我的建议表示反对”得到
        E=表示、B=反，完全错了。

        这是**启发式**：只处理紧邻情形。带宾语的兼语句（“让他走”
        不该并成一个 E）不在本函数范围内，因为那需要句类知识。
        """
        if len(verbs) >= 2 and verbs[0][1] == verbs[1][0]:
            return verbs[1]
        return verbs[0]

    @staticmethod
    def _strip_zhe_companion(text: str, verbs: list[tuple[int, int]]
                             ) -> tuple[list[tuple[int, int]], int | None]:
        """把句首的“V+着”伴随动词从 E 候选里剔除。

        `[张]` §3：“v+着+v”结构里，前一个动词整体降为**方式辅块**，
        真正的 E 是后一个动词。

            他拍着手笑          -> BK_MS=拍着，E=笑
            日本代表带着满意的笑容离开会场
                                -> BK_MS=带着，E=离开

        早期版本（`extended=True`）在第二句上得到 `E=带着`，
        因为 `带着` 和 `离开` 中间隔了宾语，`_pick_e` 的「紧邻」
        规则不生效，于是退回 `verbs[0]`。

        ## 判据为什么是「句首」而不是「任意 V+着」

        只在**第一个动词**紧邻 `着` 且它位于句首段（前 6 字）时剔除。
        句中段的 `V+着` 可能是主语的一部分（“他看着的那本书”），
        也可能是真谓语（“他还想着那件事”）。

        ## 为什么要扫前几个而不是只看 `verbs[0]`

        `extended=True` 时 `代表` 也在动词表里，
        “日本代表带着…” 的 `verbs[0]` 是 `代表`(2,4)，
        `带着` 排在第二。只看第一个就漏掉了。

        ## 词表覆盖不到时的兜底

        `他拍着手笑` 里 `拍` 不在 `DEFAULT_VERBS`（单字动词只在块边界
        认可，而 `拍` 后面紧跟 `着`，`_find_verbs_safe` 会认它，但
        `_find_verbs` 根本查不到它）。此时 `verbs` 只有 `[笑]`。

        所以最后按**形状**兜底：句首 6 字内出现「单字 + 着」，
        且后面还有动词，就按伴随辅块处理。

        返回 `(剩余动词, “着”的右边界或 None)`。
        """
        for k, (vs, ve) in enumerate(verbs):
            if vs > 5:
                break
            # 词表里存的是**词干**（`带` 不是 `带着`），体标记是句中的字，
            # 所以判“V 后面紧跟着「着」”，不是判“V 的末字是「着」”。
            if ve >= len(text) or text[ve] != "着":
                continue
            if text[vs] in "随沿为接按照":
                continue
            # 找到了：剔除它和它之前的候选（`代表` 也一起剔，
            # 因为它只是把主语切成了两半）
            zhe_end = ve + 1
            rest = [v for v in verbs[k + 1 :] if v[0] >= zhe_end]
            if not rest:
                return verbs, None
            return rest, zhe_end

        # 兜底：形状检测。句首的「X着」即使 X 不在词表里，也是伴随辅块。
        p = text.find("着")
        if 1 <= p <= 5 and text[p - 1] not in "随沿为接按照" and p - 1 >= 1:
            zhe_end = p + 1
            rest = [v for v in verbs if v[0] >= zhe_end]
            if rest:
                return rest, zhe_end
        return verbs, None

    def _aux_end(self, s: str, start: int, taken: bytearray) -> int:
        """辅块右边界：下一个标记符 / 下一个谓语 / 分句尾。

        ## 这里踩过的坑（lv 准则）

        论文 11.1：“l 准则仅用于判定辅语义块”。早期版本拿到介词后
        直接延到分句尾，于是“在教室里**认真地看书**”整句被吃成
        一个 Cn，谓语没了。辅块的右边界不能由 l 标记自己定，
        必须由下一个 v 事件定——这就是 v 准则。

        ## 为什么要便宜地猜谓语

        只靠 `_verbs` 词表判边界会漏（“看书”不在表里），所以这里
        再用一层形态信号做补充：体标记（着/了/过）、
        以及“动词不及物尾”（单个汉字后跟名词时不像谓语）。
        两者都会在 `confidence` 上付出代价，不影响块类型。
        """
        i = start
        while i < len(s):
            if taken[i]:
                return i
            if i > start and self._is_boundary(s, i):
                return i
            pstart = self._predicate_start(s, i, start)
            if pstart is not None:
                return pstart
            i += 1
        return len(s)

    @staticmethod
    def _word_containing(s: str, i: int) -> int | None:
        """s[i] 若是某个双字词的**首字**，返回词长 2；否则 None。

        单字标记符（因/于/为/当/用/向…）在汉语里几乎都能作构词成分：
        “原因”的“因”、“因为”的“为”、“当下”的“当”。不排除它们，
        就会切出“天气原 | 因”。

        只看“本字是否是词首”——词尾情形（“原因”的“因”在 i=5）由
        `_aux_end` 处理：它在 i=4 处就看不出标记，于是把“因”一并
        留在辅块里。
        """
        if i + 1 < len(s) and s[i : i + 2] in _NON_TAG_WORDS:
            return 2
        return None

    def _predicate_start(self, s: str, i: int, floor: int) -> int | None:
        """若 s[i] 落在谓语范围内，返回**谓语起点**；否则 None。

        论文 11.1：“l 准则仅用于判定辅语义块，对主语义块的判定只有
        v 准则可以利用。”辅块右边界必须靠 v 信号定位。

        两种高精度形态信号：

        1. **体标记后接**：s[i+1] 是 了/着/过。此时 s[i] 只是
           谓语的**最后一个字**，真正起点要向左找（“遇到了”）。
           早期版本直接返回 i，结果切成“图书馆遇 | 到了老朋友”。
        2. **状语尾“地”**：s[i-1] == “地”，则 i 就是谓语起点。

        刻意**不**做单字动词猜测——“笑容”“开会”就是这么被切碎的。
        """
        # 信号 1：X + 体标记
        if i + 1 < len(s) and s[i + 1] in "着了过":
            # 先试词表最长匹配（“遇到”“打断”…）
            for n in range(min(self._max_verb, i - floor + 1), 1, -1):
                if s[i - n + 1 : i + 1] in self._verbs:
                    return i - n + 1
            return i  # 词表没收录，就从这个字算起
        # 信号 2：“…地”之后
        if i > 0 and s[i - 1] == "地":
            return i
        return None

    def _looks_like_predicate(self, s: str, i: int) -> bool:
        """s[i:] 处是否像一个谓语的**开头**（不看词表）。

        论文 11.1：“l 准则仅用于判定辅语义块，对主语义块的判定只有
        v 准则可以利用。”所以辅块的右边界必须靠 v 信号定位。

        只用两种高精度形态信号：

        1. `V+了/着/过`：体标记。注意**必须整体回退**——命中
           “遇”时真正的谓语是“遇到了”，如果把 i 定在“遇”，
           就会切出“图书馆遇 | 到了老朋友”。所以这里返回的是
           **谓语起点**，调用方往左做最长词表回溯。
        2. 前面是“地”（状语尾）：“认真地看书”中的“看书”一定
           是谓语部分。

        刻意**不**做单字动词猜测——“笑容”“开会”就是这么被切碎的。
        """
        # 信号 1：左侧紧邻体标记 → i 已经在体标记后面，不是谓语起点
        if i > 0 and s[i - 1] in "着了过":
            return False
        # 信号 2：X + 体标记。X 是动词，词表里找得到就往前扩
        if i + 1 < len(s) and s[i + 1] in "着了过发":
            return True
        # 信号 3：“…地”之后的第一个字就是谓语开头
        if i > 0 and s[i - 1] == "地":
            return True
        return False

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
        if not hit:
            return False
        # 词内字不算边界。两种情形：
        #   词尾：“天气原|因”的“因”、“现|在”的“在”
        #   词首：“我|们班同学”的“同”——“同”本身是主标记符
        if hit[1] == 1:
            if i > 0 and s[i - 1 : i + 1] in _NON_TAG_WORDS:
                return False
            if s[i : i + 2] in _NON_TAG_WORDS:
                return False
        return self._usable_marker(hit[0], hit[1])

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

        # `[张]` §3：“v+着+v”。句首的伴随动词整体降为方式辅块。
        #
        # 早期版本用 `_zhe_prefix_end()` 单独做一套形状检测，只处理
        # “他拍着手笑”这种「V+着」紧贴 E 的情形。但
        # “日本代表带着满意的笑容离开会场”中间隔了宾语，
        # 那条路走不通，`_pick_e` 就退回 `verbs[0]`，得到 E=带着。
        #
        # 现在改成先整体剔除句首伴随动词，再走正常流程。
        verbs, zhe_end = self._strip_zhe_companion(text, verbs)

        if not verbs:
            return [Chunk(ChunkRole.B, text, base, base + len(text),
                          confidence=0.4)]

        e_pos, e_end = self._pick_e(verbs)
        out: list[Chunk] = []

        # 连动结构里 E 块的左边界：前方紧邻的同组动词也属于 E¶
        # “表示反对”：E 不应只是“反对”，前面的“表示”也是谓语成分。
        # 早期版本把它划进了 A，“他对我的建议表示”成为一个荒谬的 A 块。
        e_start = e_pos
        for vs, ve in verbs:
            if ve == e_start:
                e_start = vs
            elif vs >= e_pos:
                break

        before = text[:e_start].strip()
        if before:
            # 句首伴随动词已被 `_strip_zhe_companion` 识别出来：
            # “日本|代表|带着” 里主语只到 `zhe_end - 2`（“带着”占 2 字），
            # 前面是 A，`带着` 是方式辅块。
            if zhe_end is not None:
                # `[张]` §3 的“v+着+v”：主语 + 方式辅块 + E。
                # 主语长度按“x着”占 2 字回推（启发式）。
                subj_end = max(0, zhe_end - 2)
                if subj_end > 0:
                    out.append(Chunk(ChunkRole.A, text[:subj_end].strip(),
                                     base, base + subj_end, confidence=0.5))
                # 辅块一直延到 E 之前：`拍着` 的宾语（`手`）、
                # `带着` 的宾语（`满意的笑容`）都属于方式辅块内部，
                # 不是独立的 B。早期版本把 `手` 切成 B，
                # 导致 math_pattern 变成 "JK + JK + E"。
                out.append(Chunk(ChunkRole.BK_MS,
                                 text[subj_end:e_start].strip(),
                                 base + subj_end, base + e_start,
                                 confidence=0.6))
            # 前置成分是否已是"v+着"（单动词情形也降为方式辅块）
            elif before.endswith("着") and len(before) > 1:
                out.append(Chunk(ChunkRole.BK_MS, before, base,
                                 base + e_start, confidence=0.5))
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
                                     base + cut, base + e_start,
                                     confidence=0.5))
                else:
                    out.append(Chunk(ChunkRole.BK_CN, before, base,
                                     base + e_start, confidence=0.4))
            else:
                out.append(Chunk(ChunkRole.A, before, base,
                                 base + e_start, confidence=0.6))

        # 体标记属于 E 自身的形态，不是独立语义块。
        # 早期版本把紧跟的“了”切成了一个独立的 B 块（“打断”+“了李四的腿”）。
        while e_end < len(text) and text[e_end] in "着了过":
            e_end += 1

        out.append(Chunk(ChunkRole.E, text[e_start:e_end],
                         base + e_start, base + e_end, confidence=0.8))
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
