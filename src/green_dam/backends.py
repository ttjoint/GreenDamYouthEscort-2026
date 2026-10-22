"""Two interchangeable text backends behind one interface.

``docs/architecture.md`` §M3 requires this:

    M3 ──► SemanticBackend.analyse(text) -> [Hit]
             ├── HncBackend    — reimplementation of the concept-network approach
             └── JiebaBackend  — statistical segmentation, reference / fallback

"Both are selectable and the pair can be run A/B over the same corpus. This is
deliberate: a reimplementation that cannot be measured against a baseline is
just an opinion."

Two deviations from the document, both deliberate and stated here rather than
buried:

1. **The reference backend is not jieba.** The architecture names jieba, but the
   same document also requires a zero-dependency core, and a contract test
   enforces it. So :class:`SegmentationBackend` is a pure-standard-library
   statistical segmenter - maximum-matching over a term frequency table, which
   is the same family of algorithm jieba's default mode uses. It is named for
   what it is, not for a library it is not. If jieba is ever added as an
   optional extra, it can implement the same protocol without changing callers.

2. **The two backends do not answer the same question.** HNC answers "which
   known terms appear, and how severe are they". Segmentation answers "where are
   the word boundaries". Comparing them on severity would be meaningless. What
   *is* meaningful, and what :func:`compare` measures, is **agreement on spans**:
   when both find something at the same place, and when only one does.

The honest limit: neither backend is the original engine. The lexicon is
recovered; the per-word normal/blocked flag lived in ``FMean.tmp`` which was
never shipped, and the HNC grammar proper was not fully reversed. This module
does not paper over that - :attr:`BackendResult.attested` is False for both, and
``compare`` reports it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Protocol, Sequence, runtime_checkable

from .semantic import SAFE_LEVEL, Hit, LegacySemanticMatcher


@dataclass(frozen=True)
class BackendResult:
    """What a backend found in one text.

    ``hits`` uses the same :class:`~green_dam.semantic.Hit` shape as the rest of
    the project so a caller does not need to know which backend ran.
    """

    backend: str
    hits: tuple[Hit, ...] = ()
    text_length: int = 0
    #: False for every backend we have: none is a bit-faithful reproduction of
    #: the original engine. Kept as a field so a future attested backend can say
    #: so without a code change at every call site.
    attested: bool = False
    #: Word boundaries the backend decided on, when it segments. Empty for
    #: backends that only do term lookup.
    segments: tuple[str, ...] = ()

    @property
    def max_level(self) -> int:
        return max((h.level for h in self.hits), default=SAFE_LEVEL)

    @property
    def spans(self) -> tuple[tuple[int, int], ...]:
        return tuple((h.start, h.start + len(h.term)) for h in self.hits)

    def terms(self) -> tuple[str, ...]:
        return tuple(h.term for h in self.hits)


@runtime_checkable
class SemanticBackend(Protocol):
    """The interface from architecture.md §M3."""

    name: str

    def analyse(self, text: str) -> BackendResult: ...


# -- backend 1: HNC concept network ---------------------------------------

@dataclass
class HncBackend:
    """Wraps :class:`LegacySemanticMatcher` behind the common interface.

    Reimplementation of the *approach* (term network seeded with the recovered
    lexicon), not a bit-faithful copy of the original engine - see the module
    docstring.
    """

    matcher: LegacySemanticMatcher
    name: str = "hnc"
    #: Terms at or below this level are treated as "not graded as harmful" and
    #: dropped from the result. Level 0 is 89% of the table and includes short
    #: ASCII fragments that occur inside ordinary words; emitting them made
    #: normal sentences look suspicious.
    min_level: int = 1

    def analyse(self, text: str) -> BackendResult:
        verdict = self.matcher.analyse(text)
        hits = tuple(
            Hit(h.term, h.start, h.level, h.all_levels)
            for h in verdict.hits
            if max(h.all_levels or (h.level,)) >= self.min_level
        )
        return BackendResult(backend=self.name, hits=hits,
                             text_length=len(text), attested=False)


# -- backend 2: statistical segmentation ----------------------------------

@dataclass
class SegmentationBackend:
    """Reference backend: maximum-matching segmentation over a frequency table.

    This is the A/B baseline. It answers a different question from HNC - where
    the word boundaries are - so the comparison in :func:`compare` is about span
    agreement, not severity.

    Algorithm: forward maximum matching. At each position, take the longest
    candidate that appears in the table; if none does, emit one character and
    advance. That is the classic dictionary-based approach and it needs no
    third-party library.
    """

    lexicon: frozenset[str] = frozenset()
    name: str = "segmentation"
    #: Longest candidate to consider. 8 covers the overwhelming majority of CJK
    #: terms while keeping the inner loop cheap.
    max_term: int = 8

    @classmethod
    def from_terms(cls, terms: Iterable[str], *,
                   max_term: int = 8) -> "SegmentationBackend":
        """Build from any term collection, e.g. the HNC lexicon."""
        return cls(lexicon=frozenset(t for t in terms if t),
                   max_term=max_term)

    @classmethod
    def from_matcher(cls, matcher: LegacySemanticMatcher, *,
                     max_term: int = 8) -> "SegmentationBackend":
        """Build from a matcher's term table, for a like-for-like comparison."""
        return cls.from_terms(matcher._levels.keys(), max_term=max_term)

    def segment(self, text: str) -> tuple[str, ...]:
        """Forward maximum matching. Deterministic and dependency-free."""
        if not text:
            return ()
        out: list[str] = []
        i = 0
        n = len(text)
        while i < n:
            best = 0
            for size in range(min(self.max_term, n - i), 0, -1):
                if text[i:i + size] in self.lexicon:
                    best = size
                    break
            if best == 0:
                out.append(text[i])
                i += 1
            else:
                out.append(text[i:i + best])
                i += best
        return tuple(out)

    def analyse(self, text: str) -> BackendResult:
        """Report the segments that are dictionary terms, as hits at level 0.

        Level 0 is deliberate: this backend has no severity information at all.
        Reporting anything else would invent a judgement it cannot make.
        """
        segments = self.segment(text)
        hits: list[Hit] = []
        cursor = 0
        for segment in segments:
            if len(segment) > 1 and segment in self.lexicon:
                hits.append(Hit(segment, cursor, SAFE_LEVEL, (SAFE_LEVEL,)))
            cursor += len(segment)
        return BackendResult(backend=self.name, hits=tuple(hits),
                             text_length=len(text), attested=False,
                             segments=segments)


# -- A/B comparison --------------------------------------------------------

@dataclass(frozen=True)
class Comparison:
    """How two backends agreed on one text.

    ``agreed`` are spans both found; ``only_left`` and ``only_right`` are the
    disagreements. Nothing here is called an error: neither backend is
    attested, so a disagreement is a question to investigate, not a bug in one
    of them.
    """

    text_length: int
    left: str
    right: str
    agreed: tuple[tuple[int, int], ...] = ()
    only_left: tuple[tuple[int, int], ...] = ()
    only_right: tuple[tuple[int, int], ...] = ()

    @property
    def total(self) -> int:
        return len(self.agreed) + len(self.only_left) + len(self.only_right)

    @property
    def agreement(self) -> float:
        """Jaccard-style agreement over span sets. 1.0 means identical."""
        if self.total == 0:
            return 1.0
        return len(self.agreed) / self.total


def compare(left: BackendResult, right: BackendResult) -> Comparison:
    """Compare two backend results on the same text by span overlap.

    Spans are compared as exact intervals. A partial overlap counts as a
    disagreement, because the two backends are claiming different things about
    where a term is - merging them would hide exactly the information the
    comparison exists to produce.
    """
    a = set(left.spans)
    b = set(right.spans)
    return Comparison(
        text_length=max(left.text_length, right.text_length),
        left=left.backend,
        right=right.backend,
        agreed=tuple(sorted(a & b)),
        only_left=tuple(sorted(a - b)),
        only_right=tuple(sorted(b - a)),
    )


@dataclass
class AbReport:
    """Aggregate of many comparisons, for corpus-level A/B runs."""

    comparisons: list[Comparison] = field(default_factory=list)

    def add(self, comparison: Comparison) -> None:
        self.comparisons.append(comparison)

    @property
    def texts(self) -> int:
        return len(self.comparisons)

    @property
    def mean_agreement(self) -> float:
        if not self.comparisons:
            return 1.0
        return sum(c.agreement for c in self.comparisons) / len(self.comparisons)

    def summary(self) -> dict:
        """Counts only - never the matched terms themselves."""
        return {
            "texts": self.texts,
            "mean_agreement": round(self.mean_agreement, 4),
            "identical": sum(1 for c in self.comparisons if c.agreement == 1.0),
            "disagreed": sum(1 for c in self.comparisons
                             if c.only_left or c.only_right),
            "spans_agreed": sum(len(c.agreed) for c in self.comparisons),
            "spans_only_left": sum(len(c.only_left) for c in self.comparisons),
            "spans_only_right": sum(len(c.only_right) for c in self.comparisons),
        }


def run_ab(left: SemanticBackend, right: SemanticBackend,
           corpus: Sequence[str]) -> AbReport:
    """Run two backends over a corpus and aggregate their agreement."""
    report = AbReport()
    for text in corpus:
        report.add(compare(left.analyse(text), right.analyse(text)))
    return report


def load_ab_pair() -> tuple[HncBackend, SegmentationBackend]:
    """Build the default HNC + segmentation pair from the shipped lexicon."""
    from .semantic import load_default

    matcher = load_default()
    return HncBackend(matcher=matcher), SegmentationBackend.from_matcher(matcher)
