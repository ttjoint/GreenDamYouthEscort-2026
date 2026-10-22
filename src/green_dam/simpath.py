"""Sentence similarity as a text-evidence source, with its limits exposed.

What this is for
----------------
``similarity.SentenceSimilarity`` compares two sentences using the weights from
the HNC literature. A filter can use it to answer "is this text close to
something on a watch list", which is a different question from "does this text
contain a known term".

Why the limits come first
-------------------------
Similarity here is only as trustworthy as the chunking underneath it, and the
chunking has a measurable failure mode: the verb table holds 413 entries drawn
from a corpus, so a sentence whose predicate is not in it produces no E block.
``王五擦干净了桌子`` chunks to a single ``B`` because ``擦`` is not in the table.

When that happens the structural terms (``sim_gsem``, ``sim_gsef``) are computed
from a shape that does not reflect the sentence, and the total score is
meaningless. This module therefore **refuses to score** such a pair rather than
returning a plausible-looking number. That is the same rule the rest of the
project follows: not being able to judge is not the same as judging it clean.

Three other terms are substitute implementations and are marked as such by
``similarity`` itself (``degraded``): the question-type classifier, and the
lexical similarity. They are carried through into findings so a caller can see
how much of the score is measured versus substituted.

No actions
----------
A similarity score is a measurement. Whether a high score should block is
policy, and the vendor's mapping was never recovered. Findings carry
``action=None``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .chunker import Chunking, ChunkRole, SentenceChunker
from .policy import Finding
from .similarity import SentenceSimilarity, SimilarityBreakdown

#: Above this, a pair is worth reporting as "close to a watched sentence".
#:
#: Calibrated against measured scores, not guessed. With the empty-secondary fix
#: in ``similarity.py`` the observed distribution is:
#:
#: ==================  =====
#: pair                score
#: ==================  =====
#: identical           0.530
#: paraphrase (same    0.370
#:   meaning, reworded)
#: passive rewrite     0.176
#: same structure,     0.105
#:   unrelated content
#: unrelated           0.105
#: ==================  =====
#:
#: 0.2 separates "same meaning" from "same shape". It deliberately admits the
#: passive rewrite case (0.176 is just below, so it does *not* admit it - see the
#: note below) rather than being tuned until every hand-picked pair passes.
#:
#: Known limitation: a passive rewrite of the same event scores 0.176 because
#: ``char_overlap_similarity`` compares character bigrams and is sensitive to
#: word order. Raising the score would need real lexical similarity, which is a
#: substitute term in ``similarity.py`` and is marked as such. The threshold is
#: set from what the measurement can actually do, not from what we wish it did.
DEFAULT_THRESHOLD = 0.2

#: Reason tokens, stable so they can appear in logs.
TOO_SHORT = "too_short"
NO_STRUCTURE = "no_structure"
DEGRADED_CHUNKING = "degraded_chunking"


class Unscorable(ValueError):
    """Raised when a pair cannot be scored meaningfully."""


@dataclass(frozen=True)
class Comparison:
    """One scored pair, with the terms that make the score interpretable."""

    total: float
    threshold: float
    #: True when the pair is at or above the threshold.
    similar: bool
    #: Terms that are substitute implementations, straight from similarity.py.
    degraded: tuple[str, ...] = ()
    #: True when the chunking was sound enough to score at all.
    scored: bool = True
    #: Why the pair could not be scored, when ``scored`` is False.
    reason: str = ""
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Counts and flags only - no sentence text."""
        return {
            "total": round(self.total, 4),
            "threshold": self.threshold,
            "similar": self.similar,
            "scored": self.scored,
            "reason": self.reason,
            "degraded_terms": len(self.degraded),
            "metrics": {k: round(v, 4) for k, v in self.metrics.items()},
        }


def _has_predicate(chunking: Chunking) -> bool:
    return any(c.role is ChunkRole.E for c in chunking.chunks)


def is_scorable(left: Chunking, right: Chunking,
                 *, min_length: int = 2) -> tuple[bool, str]:
    """Whether a pair can be scored, and why not when it cannot.

    A pair is unscorable when either side has no predicate block: the structural
    terms would then describe a shape the sentence does not have.
    """
    if len(left.text) < min_length or len(right.text) < min_length:
        return False, TOO_SHORT
    if not left.chunks or not right.chunks:
        return False, NO_STRUCTURE
    if not _has_predicate(left) or not _has_predicate(right):
        return False, DEGRADED_CHUNKING
    return True, ""


@dataclass
class SimilaritySource:
    """Wraps :class:`SentenceSimilarity` with the scorable check applied."""

    similarity: SentenceSimilarity = field(default_factory=SentenceSimilarity)
    chunker: SentenceChunker = field(default_factory=SentenceChunker)
    threshold: float = DEFAULT_THRESHOLD
    name: str = "similarity"

    def compare(self, query: str, candidate: str) -> Comparison:
        """Score a pair, or report why it cannot be scored."""
        left = self.chunker.chunk(query)
        right = self.chunker.chunk(candidate)

        ok, reason = is_scorable(left, right)
        if not ok:
            return Comparison(total=0.0, threshold=self.threshold,
                              similar=False, scored=False, reason=reason)

        breakdown: SimilarityBreakdown = self.similarity.compare_chunkings(
            left, right)
        return Comparison(
            total=breakdown.total,
            threshold=self.threshold,
            similar=breakdown.total >= self.threshold,
            degraded=breakdown.degraded,
            scored=True,
            metrics={
                "cat": breakdown.sim_cat,
                "gse": breakdown.sim_gse,
                "sem": breakdown.sim_sem,
                "gsem": breakdown.sim_gsem,
                "gsef": breakdown.sim_gsef,
                "semm": breakdown.sim_semm,
                "semf": breakdown.sim_semf,
            },
        )

    def best_match(self, text: str,
                   candidates: list[str]) -> tuple[str, Comparison] | None:
        """The most similar candidate, or None when nothing is scorable.

        Returns the candidate *itself* so a caller can decide what to do with
        it; nothing here logs it.
        """
        best: tuple[str, Comparison] | None = None
        for candidate in candidates:
            result = self.compare(text, candidate)
            if not result.scored:
                continue
            if best is None or result.total > best[1].total:
                best = (candidate, result)
        return best

    def findings(self, text: str, candidates: list[str]) -> list[Finding]:
        """One finding when ``text`` is close to any candidate.

        Level 0 and no action: a similarity score is evidence to weigh, not a
        verdict. ``attested`` is False because two of the three terms are
        substitutes, as ``similarity`` itself reports.
        """
        hit = self.best_match(text, candidates)
        if hit is None:
            return []
        _, result = hit
        if not result.similar:
            return []

        return [Finding(
            span=(0, len(text)),
            surface=text,
            kind="similarity",
            sources=(self.name,),
            levels=(0,),
            attested=False,
            action=None,
            reason="similar_to_watched",
        )]


def summarise(result: Comparison) -> dict:
    """Log-safe summary of one comparison."""
    return result.to_dict()
