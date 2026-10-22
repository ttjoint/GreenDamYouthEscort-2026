"""Semantic chunking as a text-evidence source.

What this adds, and what it deliberately does not
-------------------------------------------------
``chunker`` splits a sentence into semantic blocks (E / A / B / C plus
secondary blocks). This module turns that structure into findings the policy
layer can weigh.

It does **not** feed ``hnc.classify``. That function needs ``role_a``,
``role_b`` and ``participants`` as integers, and the mapping from linguistic
roles to those integers lives in ``SENTEN.LIB``, which was never shipped with
the product. ``chunker.Chunking.to_frame_hint()`` returns ``None`` for exactly
that reason, and a test pins it. Inventing the integers would produce a
plausible-looking sentence classifier with no evidence behind it - the failure
mode this project exists to avoid.

So what is a chunk pattern good for on its own? Two things:

* **Degeneracy.** A sentence the chunker cannot find a predicate in is a
  different kind of sentence from one it can. That is a measurable property, not
  a guess.
* **Structural shape.** ``A+E+B`` versus a bare ``E`` is a real distinction, and
  a filter that treats a one-block fragment differently from a full clause is
  behaving defensibly even without knowing the sentence category.

Findings from here carry ``level`` 0 and ``action=None``. A chunk pattern is not
evidence of harm; it is a feature that other evidence can be weighed against.
That keeps this module honest: it contributes structure, never a verdict.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .chunker import Chunk, Chunking, ChunkRole, SentenceChunker
from .policy import Finding

#: A sentence whose chunker output has no E block: the chunker found no
#: predicate. Not an error - short fragments are normal - but it is a distinct
#: shape worth recording.
NO_PREDICATE = "no_predicate"

#: The chunker found a predicate but produced only one block.
SINGLE_BLOCK = "single_block"

#: A well-formed multi-block clause.
STRUCTURED = "structured"


@dataclass(frozen=True)
class ChunkSummary:
    """Structure of one sentence, with nothing user-visible in it.

    Counts and role names only: a caller can log this safely, which is why it is
    separate from the findings that carry spans.
    """

    length: int
    block_count: int
    main_count: int
    secondary_count: int
    pattern: str
    shape: str
    min_confidence: float

    def to_dict(self) -> dict:
        return {
            "length": self.length,
            "blocks": self.block_count,
            "main": self.main_count,
            "secondary": self.secondary_count,
            "pattern": self.pattern,
            "shape": self.shape,
            "min_confidence": round(self.min_confidence, 3),
        }


def classify_shape(chunking: Chunking) -> str:
    """Which of the three shapes this sentence has."""
    if not chunking.chunks:
        return NO_PREDICATE
    has_predicate = any(c.role is ChunkRole.E for c in chunking.chunks)
    if not has_predicate:
        return NO_PREDICATE
    if len(chunking.chunks) == 1:
        return SINGLE_BLOCK
    return STRUCTURED


def summarise(chunking: Chunking) -> ChunkSummary:
    """Structure summary - safe to log, contains no text."""
    confidences = [c.confidence for c in chunking.chunks] or [0.0]
    return ChunkSummary(
        length=len(chunking.text),
        block_count=len(chunking.chunks),
        main_count=len(chunking.main),
        secondary_count=len(chunking.secondary),
        pattern=chunking.pattern,
        shape=classify_shape(chunking),
        min_confidence=min(confidences),
    )


@dataclass
class ChunkSource:
    """Wraps a :class:`SentenceChunker` as a text-evidence source.

    ``emit_findings`` controls whether chunk structure becomes ``Finding``
    objects. Off by default: most callers want the structure summary (for
    logging or routing) without adding evidence that has no severity meaning.
    """

    chunker: SentenceChunker = field(default_factory=SentenceChunker)
    emit_findings: bool = False
    name: str = "chunker"

    def analyse(self, text: str) -> Chunking:
        """The raw chunking, for callers that want the blocks themselves."""
        return self.chunker.chunk(text)

    def summarise(self, text: str) -> ChunkSummary:
        return summarise(self.analyse(text))

    def findings(self, text: str) -> list[Finding]:
        """One finding per sentence the chunker could not structure.

        Level 0 and no action: this is a structural observation, not a
        judgement. It exists so a caller *can* weigh "this text is mostly
        unstructured fragments" against other evidence, not so it can block on
        it.
        """
        if not self.emit_findings:
            return []

        chunking = self.analyse(text)
        shape = classify_shape(chunking)
        if shape is STRUCTURED:
            return []

        return [Finding(
            span=(0, len(text)),
            surface=text,
            kind="structure",
            sources=(self.name,),
            levels=(0,),
            attested=True,        # the chunker's output is measured, not guessed
            action=None,
            reason=shape,
        )]


def is_structured(text: str,
                  chunker: SentenceChunker | None = None) -> bool:
    """Convenience: did the chunker find a predicate and at least two blocks?"""
    source = ChunkSource(chunker=chunker) if chunker else ChunkSource()
    return classify_shape(source.analyse(text)) is STRUCTURED
