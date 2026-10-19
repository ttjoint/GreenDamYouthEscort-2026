"""Evidence-to-action bridge for the reimplemented filter.

This module is deliberately inert: it turns evidence (wordlist hits, legacy
category rules, trust URLs) into an *advisory* action, and never performs any
blocking. Anything whose real-world meaning is not proven stays ``None``.

See ``docs/policy-layer.md`` for the reasoning.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal

from .wordlists import WordHit, WordlistIndex

ROOT = Path(__file__).resolve().parents[2]

Action = Literal["allow", "log", "warn", "block", "quarantine"]

# Strongest last: aggregation picks the highest index that is not None.
ACTION_STRENGTH = ("allow", "log", "warn", "quarantine", "block")

#: Evidence kinds, in the order the doc describes them.
KIND_LIBRARY_ONLY = "library_only"
KIND_SCORED = "scored"
KIND_LEVELED = "leveled"
KIND_RULE = "rule"
KIND_TRUST = "trust"


@dataclass(frozen=True)
class Finding:
    """One piece of evidence, plus the action the policy table derives from it."""

    span: tuple[int, int]
    surface: str
    kind: str
    sources: tuple[str, ...]
    category: str | None = None
    levels: tuple[int, ...] = ()
    attested: bool = False
    action: Action | None = None
    reason: str = ""

    @property
    def matched(self) -> str:
        start, end = self.span
        return self.surface[start:end]


@dataclass(frozen=True)
class Verdict:
    findings: tuple[Finding, ...]
    action: Action | None
    reason: str = ""
    #: Honest flag: no part of this verdict is backed by reverse engineering.
    attested: bool = False


def _strength(action: Action | None) -> int:
    if action is None:
        return -1
    return ACTION_STRENGTH.index(action)


@dataclass
class PolicyTable:
    """Explicit, overridable mapping from evidence to advisory action.

    Defaults are *our* choice, not the vendor's: the shipped binary's
    ``反动度`` / ``红黑度`` to action mapping is still unknown, so
    ``attested`` stays False until there is disassembly evidence.
    """

    level_actions: dict[int, Action] = field(default_factory=dict)
    category_actions: dict[str, Action] = field(default_factory=dict)
    trust_domains: frozenset[str] = frozenset()
    unknown_rule_action: Action | None = None
    attested: bool = False

    def for_level(self, levels: tuple[int, ...]) -> tuple[Action | None, str]:
        if not levels:
            return None, "no_level"
        known = [self.level_actions[lv] for lv in levels if lv in self.level_actions]
        if not known:
            return None, "level_unmapped"
        best = max(known, key=_strength)
        return best, "level_mapped"

    def for_category(self, category: str | None) -> tuple[Action | None, str]:
        if category is None:
            return None, "no_category"
        action = self.category_actions.get(category)
        if action is None:
            return None, "category_unmapped"
        return action, "category_mapped"

    def in_trust(self, text: str) -> bool:
        if not self.trust_domains:
            return False
        low = text.lower()
        return any(domain in low for domain in self.trust_domains)


@dataclass
class PolicyEngine:
    words: WordlistIndex
    policies: PolicyTable

    # -- evidence sources -------------------------------------------------

    def _word_findings(self, text: str) -> list[Finding]:
        out: list[Finding] = []
        for start, hit in self.words.scan(text):
            span = (start, start + len(hit.word))
            if hit.levels:
                action, reason = self.policies.for_level(hit.levels)
                kind = KIND_LEVELED
            else:
                action, reason = None, "library_only"
                kind = KIND_LIBRARY_ONLY
            out.append(Finding(
                span=span,
                surface=text,
                kind=kind,
                sources=hit.sources,
                levels=hit.levels,
                attested=hit.attested,
                action=action,
                reason=reason,
            ))
        return out

    # -- verdict ----------------------------------------------------------

    def verdict(self, text: str, findings: Iterable[Finding] | None = None) -> Verdict:
        items = tuple(self._word_findings(text) if findings is None else findings)
        if not items:
            return Verdict(findings=(), action=None, reason="no_evidence")

        if self.policies.in_trust(text):
            return Verdict(findings=items, action="allow", reason="trusted_domain")

        unmapped = [f for f in items if f.action is None]
        if len(unmapped) == len(items):
            return Verdict(findings=items, action=None, reason="all_unmapped")

        best = max(items, key=lambda f: _strength(f.action))
        return Verdict(
            findings=items,
            action=best.action,
            reason="strongest_finding",
            attested=self.policies.attested and all(
                f.attested for f in items if f.action is not None),
        )


# -- loaders --------------------------------------------------------------

def load_trust_domains(path: Path) -> frozenset[str]:
    """Read the imported trust URL list as lowercase host fragments."""
    obj = json.loads(path.read_text(encoding="utf-8"))
    out = set()
    for entry in obj.get("entries", []):
        text = entry.strip().lower()
        if not text:
            continue
        text = re.sub(r"^[a-z]+://", "", text)
        text = text.split("/")[0]
        if text:
            out.add(text)
    return frozenset(out)


def default_policy_table(legacy_dir: Path | None = None) -> PolicyTable:
    """Build the default table. Every mapped value is our choice, not vendor."""

    trust = frozenset()
    if legacy_dir is not None:
        candidate = legacy_dir / "trust-urls.json"
        if candidate.exists():
            trust = load_trust_domains(candidate)
    return PolicyTable(
        level_actions={},
        category_actions={},
        trust_domains=trust,
        unknown_rule_action=None,
        attested=False,
    )
