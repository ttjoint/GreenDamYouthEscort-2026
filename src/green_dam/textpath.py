"""The text evidence pipeline: sources in, findings out.

Why this is its own module
--------------------------
Gathering text evidence has nothing to do with network requests or image
screening, but all three used to live inside ``Filter``. This module owns the
text half:

    sources  →  findings  →  verdict

Three sources can contribute findings:

=============  ==============================  ====================
source         produces                        level attribution
=============  ==============================  ====================
wordlists      :class:`policy.Finding`         the entry's own level
HNC backend    :class:`policy.Finding`         the chain entry's grade
any backend    :class:`policy.Finding`         whatever the backend says
=============  ==============================  ====================

The last two overlap on purpose. :class:`~green_dam.backends.HncBackend` is the
same matcher behind the ``SemanticBackend`` protocol; accepting both shapes means
a caller can pass either without this module caring.

The level-0 rule, stated once
-----------------------------
A hit whose highest level is 0 is **not** evidence. Level 0 means "not graded as
harmful", and 89% of the HNC table is level 0, including short ASCII fragments
that occur inside ordinary words. Emitting them made normal sentences look
suspicious - the exact false-positive trap the original product fell into.

Every source in this module applies that rule. It is not repeated at each call
site; it is applied in :func:`findings_from_backend` and in the wordlist path's
own filter, and the tests assert it for each.

No actions here
---------------
This module produces evidence and a verdict. It never decides that something
should be blocked: whether a level means block or warn is policy, and the
vendor's mapping was never recovered. See ``docs/policy-layer.md`` §2.1.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .policy import ACTION_STRENGTH, Finding, KIND_SCORED, PolicyEngine, Verdict

#: Hits at or below this level are "not graded as harmful" and are dropped.
SAFE_LEVEL = 0


@runtime_checkable
class BackendLike(Protocol):
    """Anything with ``analyse(text) -> result-with-hits`` and a ``name``."""

    name: str

    def analyse(self, text: str): ...


@dataclass
class TextSources:
    """Everything that can contribute text evidence, all optional.

    ``wordlists`` is the general multi-source lexicon; ``backend`` is a
    :class:`~green_dam.backends.SemanticBackend`; ``matcher`` is a bare
    :class:`~green_dam.semantic.LegacySemanticMatcher`, accepted because that is
    what older call sites pass.
    """

    wordlists: object | None = None
    backend: object | None = None
    matcher: object | None = None
    #: Optional :class:`green_dam.chunkpath.ChunkSource`. Off by default: chunk
    #: structure is a feature, not evidence of harm, so most callers want the
    #: summary without adding findings.
    chunker: object | None = None

    @property
    def configured(self) -> bool:
        """True when at least one source exists.

        Used to tell "no source configured" from "configured, found nothing".
        Those must not collapse into one reason: the first means nothing was
        looked at.
        """
        return any(x is not None
                   for x in (self.wordlists, self.backend, self.matcher,
                             self.chunker))

    def active_backend(self) -> object | None:
        """The backend-shaped source, preferring an explicit ``backend``."""
        return self.backend if self.backend is not None else self.matcher


def findings_from_backend(source, text: str) -> list[Finding]:
    """Findings from a SemanticBackend or a bare HNC matcher.

    Level-0 hits are dropped here, once, for both shapes.
    """
    if not hasattr(source, "analyse"):
        # A bare matcher: reuse the bridge in semantic.py, which already applies
        # the level-0 rule.
        from .semantic import analyse_findings

        return analyse_findings(source, text)

    result = source.analyse(text)
    hits = getattr(result, "hits", ())
    name = getattr(source, "name", "hnc")

    out: list[Finding] = []
    for hit in hits:
        levels = hit.all_levels or (hit.level,)
        if max(levels) <= SAFE_LEVEL:
            continue
        out.append(Finding(
            span=(hit.start, hit.start + len(hit.term)),
            surface=text,
            kind=KIND_SCORED,
            sources=(name,),
            levels=levels,
            # Neither backend is the original engine; see docs/backends.md.
            attested=False,
            action=None,
            reason="hnc_score",
        ))
    return out


def findings_from_wordlists(engine: PolicyEngine, text: str) -> list[Finding]:
    """Findings from the wordlist index, via the policy engine's own scan."""
    return list(engine._word_findings(text))


def gather(sources: TextSources, text: str,
           engine: PolicyEngine | None) -> list[Finding]:
    """Collect findings from every configured source, in a stable order."""
    out: list[Finding] = []
    if engine is not None:
        out.extend(findings_from_wordlists(engine, text))
    backend = sources.active_backend()
    if backend is not None:
        out.extend(findings_from_backend(backend, text))
    # Chunk structure is opt-in per source: ChunkSource.findings() returns []
    # unless emit_findings was set, so this is safe to call unconditionally.
    if sources.chunker is not None and hasattr(sources.chunker, "findings"):
        out.extend(sources.chunker.findings(text))
    return out


def verdict_from(findings: list[Finding]) -> Verdict:
    """Build a Verdict when no wordlist engine is available.

    Only backend evidence can reach here, and backend findings carry no action,
    so the action is None by construction. Kept explicit rather than assumed, so
    a future attested backend that *does* set actions is handled automatically.
    """
    best = None
    for finding in findings:
        if finding.action is None:
            continue
        if best is None or ACTION_STRENGTH.index(finding.action) > \
                ACTION_STRENGTH.index(best):
            best = finding.action
    return Verdict(findings=tuple(findings), action=best,
                   reason="strongest_finding" if best else "no_action",
                   attested=False)


def evaluate(sources: TextSources, text: str,
             engine: PolicyEngine | None) -> Verdict:
    """Full text pipeline: gather, then turn into a verdict.

    The reason distinguishes the two empty cases, because they mean different
    things:

    ==========================  ==================
    situation                   reason
    ==========================  ==================
    no source configured        ``no_wordlists``
    configured, nothing found   ``no_evidence``
    ==========================  ==================
    """
    findings = gather(sources, text, engine)

    if not findings:
        if not sources.configured:
            return Verdict(findings=(), action=None, reason="no_wordlists")
        return Verdict(findings=(), action=None, reason="no_evidence")

    if engine is not None:
        return engine.verdict(text, findings=findings)
    return verdict_from(findings)
