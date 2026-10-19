"""基于 HNC 句类的句子相似度。

## 公式来源

王宇、王芳《基于 HNC 句类的社区问答系统问句检索模型构建》
（计算机应用研究 37(6), 2020），式 (1)(2)(3)(4)(5)(6)。

    sim(Q,C) = α·sim_cat + β·sim_gse + γ·sim_sem                (1)
    sim_gse  = β1·sim_gsem + β2·sim_gsef                        (3)
    sim_sem  = γ1·sim_semm + (1-γ1)·sim_semf                    (6)

论文调优权重：α=0.3 β=0.3 γ=0.4 β1=0.7 β2=0.3 γ1=0.8
分项细则：纯主块同句类=1、同广义作用/效应类=β11(0.5)、其余 0；
辅块同标记同内容=1、同标记异内容=β21(0.7)、标记相交=β22(0.4)。

论文实测（1,140 问句 × 20 候选，人工标注）：AP@1 0.67 / MAP 0.72 /
MRR 0.77，优于向量空间模型（0.21/0.32/0.35）、语言模型与翻译模型。

## 本实现的边界（重要）

论文的 `sim_cat`（问句类型）与 `sim_semm/sim_semf`（词汇语义相似度）
**依赖未分发的 HNC 知识库**：前者要与"问题类别词语集合"比语义相似度，
后者要按文献[8] 的词汇语义相似度算。我们两样都没有。

所以本模块：

- **实现完整**：式 (1)(3)(4)(5)(6) 的结构、权重、阈值全部照抄，可调。
- **替换可替换项**：`sim_cat` 与词汇语义相似度用**显式声明的替代实现**
  （字符/词重叠），并在返回值里用 `degraded` 标出哪些项是替代的。
- **绝不假装**：`degraded=True` 时分数不能与论文数字直接比。

这样做是为了满足 architecture.md 的原则——"一个无法与基线对照的复刻
只是一个观点"。分数是可测量、可 A/B 的，来源也是可核查的。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .chunker import Chunk, ChunkRole, Chunking, SentenceChunker

__all__ = [
    "Weights",
    "SimilarityBreakdown",
    "SentenceSimilarity",
    "char_overlap_similarity",
]


@dataclass(frozen=True)
class Weights:
    """式 (1)(3)(6) 的全部调节参数，默认值取自论文表 2。"""

    alpha: float = 0.3     # sim_cat
    beta: float = 0.3      # sim_gse
    gamma: float = 0.4     # sim_sem
    beta1: float = 0.7     # 主块在 sim_gse 中
    beta2: float = 0.3     # 辅块在 sim_gse 中
    beta11: float = 0.5    # 同属广义作用/效应句
    beta21: float = 0.7    # 辅块同标记异内容
    beta22: float = 0.4    # 辅块标记相交
    gamma1: float = 0.8    # 主块在 sim_sem 中（论文要求 > 0.5）


@dataclass
class SimilarityBreakdown:
    """逐项分值 + 退化标记。"""

    total: float
    sim_cat: float
    sim_gse: float
    sim_sem: float
    sim_gsem: float
    sim_gsef: float
    sim_semm: float
    sim_semf: float
    #: 哪些分项是替代实现（非论文原法）
    degraded: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 替代的词汇相似度
# ---------------------------------------------------------------------------


def char_overlap_similarity(a: str, b: str) -> float:
    """字符二元组 Jaccard。**替代**文献[8] 的 HNC 词汇语义相似度。

    选 Jaccard 而非编辑距离，是因为中文短句里字序变动比字面差异更常见
    （"张三打断李四的腿" vs "李四的腿被张三打断"），Jaccard 对语序不敏感。
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    ga = {a[i : i + 2] for i in range(len(a) - 1)} or {a}
    gb = {b[i : i + 2] for i in range(len(b) - 1)} or {b}
    inter = ga & gb
    union = ga | gb
    return len(inter) / len(union) if union else 0.0


#: 广义作用/效应类。`[概要]`：作用效应链六环节里，作用与效应是源头与
#: 结果，`[王]` 式 (4) 对"同属广义作用或效用句"给 β11 的相似度。
#: 句类码族位（`!3xxxx` 的第 2 位）在缺失字典下只能粗略对应：
#: 族 1/2 归作用类，族 3 归效应类。这是**推断**，标注在 `degraded`。
_ACTION_FAMILIES = {1, 2}
_EFFECT_FAMILIES = {3}


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------


class SentenceSimilarity:
    """式 (1) 的完整实现。"""

    def __init__(
        self,
        chunker: SentenceChunker | None = None,
        weights: Weights | None = None,
    ) -> None:
        self._chunker = chunker or SentenceChunker()
        self.w = weights or Weights()

    # -- 公开 ------------------------------------------------------------

    def compare(self, q: str, c: str) -> SimilarityBreakdown:
        qc, cc = self._chunker.chunk(q), self._chunker.chunk(c)
        return self.compare_chunkings(qc, cc)

    def compare_chunkings(self, q: Chunking, c: Chunking) -> SimilarityBreakdown:
        degraded: list[str] = []
        notes: list[str] = []

        sim_cat = self._sim_cat(q, c, degraded)
        sim_gsem, sim_gsef = self._sim_gse(q, c)
        sim_semm, sim_semf, sem_degraded = self._sim_sem(q, c)
        degraded.extend(sem_degraded)

        sim_gse = self.w.beta1 * sim_gsem + self.w.beta2 * sim_gsef
        sim_sem = self.w.gamma1 * sim_semm + (1 - self.w.gamma1) * sim_semf
        total = (
            self.w.alpha * sim_cat
            + self.w.beta * sim_gse
            + self.w.gamma * sim_sem
        )

        if not q.chunks or not c.chunks:
            notes.append("至少一方无可用语义块，分数不可比")
        return SimilarityBreakdown(
            total=total,
            sim_cat=sim_cat,
            sim_gse=sim_gse,
            sim_sem=sim_sem,
            sim_gsem=sim_gsem,
            sim_gsef=sim_gsef,
            sim_semm=sim_semm,
            sim_semf=sim_semf,
            degraded=tuple(degraded),
            notes=notes,
        )

    # -- 分项 ------------------------------------------------------------

    def _sim_cat(self, q: Chunking, c: Chunking,
                 degraded: list[str]) -> float:
        """问句类型相似度（式 2）。

        论文用贝叶斯分类器把问句分到 7 类（定义/事实/过程/原因/观点/
        是非/描述），我们无训练数据。**替代**：以一个句子的疑问焦点词
        归一到上述 7 类。同类=1，同大类=α1(0.5)，异类=0。
        """
        degraded.append("sim_cat(以疑问词规则替代贝叶斯分类)")
        fq, fc = _focus_category(q.text), _focus_category(c.text)
        if fq is None or fc is None:
            return 0.0
        if fq == fc:
            return 1.0
        if _CATEGORY_GROUP[fq] == _CATEGORY_GROUP[fc]:
            return 0.5
        return 0.0

    def _sim_gse(self, q: Chunking, c: Chunking) -> tuple[float, float]:
        """式 (4)(5)：主块按块签名比，辅块按标记+内容比。"""
        # 式 (4)：主语义块相似度
        qm, cm = q.pattern_of_main(), c.pattern_of_main()
        if qm == cm:
            sim_gsem = 1.0
        elif _same_broad_family(qm, cm):
            sim_gsem = self.w.beta11
        else:
            sim_gsem = 0.0

        # 式 (5)：辅语义块相似度
        qs = {(ch.marker or "", ch.text) for ch in q.secondary}
        cs = {(ch.marker or "", ch.text) for ch in c.secondary}
        if not qs and not cs:
            sim_gsef = 1.0
        else:
            qm_set = {m for m, _ in qs}
            cm_set = {m for m, _ in cs}
            if qm_set == cm_set and {t for _, t in qs} == {t for _, t in cs}:
                sim_gsef = 1.0
            elif qm_set == cm_set:
                sim_gsef = self.w.beta21
            elif qm_set & cm_set:
                sim_gsef = self.w.beta22
            else:
                sim_gsef = 0.0
        return sim_gsem, sim_gsef

    def _sim_sem(self, q: Chunking, c: Chunking
                 ) -> tuple[float, float, list[str]]:
        """式 (6)：主块与辅块的内容相似度（逐块对齐后取均值）。"""
        degraded = ["sim_semm/sim_semf(以字符 n-gram Jaccard 替代 HNC 词汇语义相似度)"]
        main_q, main_c = q.main, c.main
        sec_q, sec_c = q.secondary, c.secondary

        sim_semm = _aligned_mean(main_q, main_c, self._chunk_sim)
        sim_semf = _aligned_mean(sec_q, sec_c, self._chunk_sim)
        return sim_semm, sim_semf, degraded

    @staticmethod
    def _chunk_sim(a: Chunk, b: Chunk) -> float:
        # 角色不同则语义块不同（`[概要]`：语义块是句类的函数）
        if a.role != b.role:
            return 0.0
        return char_overlap_similarity(a.text, b.text)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

#: 问句类型的焦点词 → 七类（文献[21] 的分类，`[王]` §2.1）。
_FOCUS_RULES: tuple[tuple[str, str], ...] = (
    ("什么是", "定义"), ("是什么", "定义"), ("何谓", "定义"),
    ("为什么", "原因"), ("为何", "原因"), ("原因", "原因"),
    ("怎么", "过程"), ("如何", "过程"), ("怎样", "过程"),
    ("是不是", "是非"), ("是否", "是非"), ("吗", "是非"),
    ("认为", "观点"), ("觉得", "观点"), ("看法", "观点"),
    ("谁", "事实"), ("何时", "事实"), ("哪里", "事实"),
    ("多少", "事实"), ("几个", "事实"),
)

#: 大类归属：定义/事实/是非 → 陈述型；过程/原因 → 解释型；观点/描述 → 评价型
_CATEGORY_GROUP = {
    "定义": "陈述", "事实": "陈述", "是非": "陈述",
    "过程": "解释", "原因": "解释",
    "观点": "评价", "描述": "评价",
}


def _focus_category(text: str) -> str | None:
    """焦点词优先：先比长度，再比出现位置。

    早期版本按表序匹配，导致“天空为什么是蓝色”先命中“是什么”而不是
    更长的“为什么”，两句话被归到了不同类别。后来又发现“为什么”与
    “是什么”同为 3 字，只比长度仍会歧义，故加上位置做第二判据。
    """
    best: tuple[int, int, str] | None = None  # (len, pos, cat)
    for word, cat in _FOCUS_RULES:
        pos = text.find(word)
        if pos < 0:
            continue
        key = (len(word), -pos, cat)
        if best is None or key[:2] > best[:2]:
            best = key
    return best[2] if best else None


def _same_broad_family(a: str, b: str) -> bool:
    """式 (4) 的"同属广义作用或效用句"。用块签名首字母近似。"""
    if not a or not b:
        return False
    return a[0] == b[0]


def _aligned_mean(a: list[Chunk], b: list[Chunk], fn) -> float:
    """按序号对齐后求均值。块数不同时按较长者计，缺失块记 0。"""
    if not a and not b:
        return 1.0
    n = max(len(a), len(b))
    total = 0.0
    for i in range(n):
        if i < len(a) and i < len(b):
            total += fn(a[i], b[i])
    return total / n
