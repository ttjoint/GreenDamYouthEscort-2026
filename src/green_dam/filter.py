"""One entry point that wires the layers together.

Everything below this module is deliberately independent: ``sysdat`` decodes,
``namelists`` stores, ``intercept`` decides, ``policy`` weighs evidence,
``eventlog`` records. That independence is why each piece is testable on its
own, and also why nothing so far answers the only question a caller actually
has: *given this request, what do I do, and what gets written down?*

:class:`Filter` is that answer. It owns the configuration, the lists, the
policy table and the log, and exposes two methods:

* :meth:`Filter.check_request` - network path, returns an
  :class:`~green_dam.policy.Assessment`
* :meth:`Filter.check_text` - text path, returns a
  :class:`~green_dam.policy.Verdict`

Both record to the event log. Both honour the sys.dat off-switches.

Design choices worth stating
----------------------------
**The off-switches are checked in one place.** ``sysdat`` slots 2 and 10 mean
"stop filtering" and "stop keyword filtering". Rather than sprinkling that
check through the stages, :class:`Filter` consults it once, up front, and
records a ``gated`` event. That way the log distinguishes "looked and found
nothing" from "was told not to look" - which is the difference the original
product hid from its users.

**A gated request is allowed, but the reason is not "clean".** The assessment
says ``reason="legacy_filtering_disabled"``. A caller that renders this to a
user must not print "no problems found".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import config as config_mod
from . import sysdat
from .eventlog import EventLog, record_for_assessment, record_for_decision, record_for_gate
from .imaging import ScreenResult, screen
from .intercept import Decision, Interceptor, Outcome, Request
from .namelists import NameLists
from .policy import (
    ACTION_STRENGTH,
    Assessment,
    PolicyEngine,
    PolicyTable,
    Verdict,
    assess,
    default_policy_table,
)
from .wordlists import WordlistIndex


@dataclass
class Filter:
    """Configured filter: policy array + lists + wordlists + log."""

    legacy: sysdat.PolicyArray | None = None
    lists: NameLists = field(default_factory=NameLists)
    words: WordlistIndex | None = None
    #: Optional HNC score-table matcher (see green_dam.semantic). A second,
    #: independent evidence source: wordlists come from the external .lib
    #: files, this one from the HNC score table.
    hnc: object | None = None
    log: EventLog | None = None
    policies: PolicyTable | None = None

    def __post_init__(self) -> None:
        if self.policies is None:
            self.policies = PolicyTable(
                legacy=self.legacy,
                # The vendor's score-to-action mapping was never recovered, so
                # nothing is mapped by default. See docs/policy-layer.md 2.1.
                level_actions={},
                attested=False,
            )
        elif self.legacy is not None and self.policies.legacy is None:
            self.policies.legacy = self.legacy
        # The interceptor stays log-free; Filter owns logging so there is
        # exactly one record per call, with the direction attached.
        self._interceptor = Interceptor(lists=self.lists, log=None)

    # -- construction helpers --------------------------------------------

    @classmethod
    def from_config_dir(cls, path: Path, *, log: EventLog | None = None,
                        words: WordlistIndex | None = None) -> "Filter":
        """Build from a directory holding ``xconfigs.dat`` (or ``sys.dat``).

        Missing configuration is not an error: a fresh install has no file yet,
        and the shipped defaults are the correct starting state.
        """
        for name in ("xconfigs.dat", "sys.dat"):
            candidate = path / name
            if candidate.exists():
                legacy = config_mod.load(candidate)
                break
        else:
            legacy = config_mod.default_for_fresh_install()
        return cls(legacy=legacy, log=log, words=words)

    @property
    def _engine(self) -> PolicyEngine | None:
        if self.words is None:
            return None
        return PolicyEngine(words=self.words, policies=self.policies)

    @property
    def filtering_disabled(self) -> bool:
        return self.legacy is not None and self.legacy.filtering_disabled

    @property
    def keyword_filter_disabled(self) -> bool:
        return self.legacy is not None and self.legacy.keyword_filter_disabled

    def _gate(self, *, keyword: bool) -> str | None:
        """Return the reason filtering is off, or None when it is on."""
        if self.filtering_disabled:
            return "legacy_filtering_disabled"
        if keyword and self.keyword_filter_disabled:
            return "legacy_keyword_filter_disabled"
        return None

    # -- network path -----------------------------------------------------

    def check_request(self, request: Request) -> Assessment:
        """Decide a network request, honouring off-switches, and log it."""
        reason = self._gate(keyword=False)
        if reason is not None:
            if self.log is not None:
                self.log.append(record_for_gate(reason))
            return Assessment(action=None, source="policy", reason=reason)

        decision: Decision = self._interceptor.decide(request)
        if self.log is not None:
            self.log.append(record_for_decision(decision,
                                                direction=request.direction))
        return assess(network=decision)

    # -- text path --------------------------------------------------------

    def check_text(self, text: str) -> Verdict:
        """Judge text against the wordlists, honouring the keyword off-switch."""
        reason = self._gate(keyword=True)
        if reason is not None:
            if self.log is not None:
                self.log.append(record_for_gate(reason))
            return Verdict(findings=(), action=None, reason=reason)

        findings = []
        if self._engine is not None:
            findings.extend(self._engine._word_findings(text))
        if self.hnc is not None:
            findings.extend(self._hnc_findings(text))

        if not findings:
            reason = ("no_wordlists" if self._engine is None and self.hnc is None
                      else "no_evidence")
            verdict = Verdict(findings=(), action=None, reason=reason)
        else:
            verdict = self._engine.verdict(text, findings=findings)                 if self._engine is not None else self._verdict_from(findings)

        if self.log is not None:
            self.log.append(record_for_assessment(
                assess(text=verdict), direction="text"))
        return verdict

    def _hnc_findings(self, text: str) -> list:
        """HNC score-table findings for ``text``, levels >= 1 only."""
        from .semantic import analyse_findings

        return analyse_findings(self.hnc, text)

    def _verdict_from(self, findings) -> Verdict:
        """Build a Verdict from findings when no wordlist engine exists."""
        best = None
        for finding in findings:
            action = finding.action
            if action is None:
                continue
            if best is None or ACTION_STRENGTH.index(action) >                     ACTION_STRENGTH.index(best):
                best = action
        return Verdict(findings=tuple(findings), action=best,
                       reason="strongest_finding" if best else "no_action",
                       attested=False)

    # -- image path -------------------------------------------------------

    def check_image(self, *, width: int, height: int,
                    red_channel: bytes | None = None) -> ScreenResult:
        """Screen one frame, honouring the image-filter switches.

        Returns the measurement only. Whether a suspect frame becomes a block is
        :meth:`check`'s job via ``image_action``, because the vendor's
        score-to-action mapping is still unknown.
        """
        reason = self._gate(keyword=False)
        if reason is not None:
            if self.log is not None:
                self.log.append(record_for_gate(reason))
            return ScreenResult(suspect=False, detail=reason)

        if self.legacy is not None and not self.legacy.image_filter_enabled:
            # Image filtering is switched off in the policy array. Report that
            # rather than a clean frame - same rule as everywhere else.
            if self.log is not None:
                self.log.append(record_for_gate("image_filter_disabled"))
            return ScreenResult(suspect=False, detail="image_filter_disabled")

        if self.legacy is None:
            # No policy at all: treat image filtering as off rather than
            # silently screening. "Not configured" must not read as "enabled".
            if self.log is not None:
                self.log.append(record_for_gate("image_filter_disabled"))
            return ScreenResult(suspect=False, detail="image_filter_disabled")

        return screen(width=width, height=height, red_channel=red_channel)

    # -- combined ---------------------------------------------------------

    def check(self, *, request: Request | None = None,
              text: str | None = None,
              image: ScreenResult | None = None,
              image_action=None) -> Assessment:
        """Judge a request and/or text together, returning one action.

        A network block wins; otherwise a text action; otherwise nothing. The
        same precedence as :func:`green_dam.policy.assess`, applied once here so
        callers do not re-implement it.
        """
        network = None
        verdict = None

        if request is not None:
            reason = self._gate(keyword=False)
            if reason is not None:
                if self.log is not None:
                    self.log.append(record_for_gate(reason))
                return Assessment(action=None, source="policy", reason=reason)
            # Suppress nothing: the interceptor is always log-free here.
            network = self._interceptor.decide(request)

        if text is not None:
            reason = self._gate(keyword=True)
            if reason is not None:
                if self.log is not None:
                    self.log.append(record_for_gate(reason))
                verdict = Verdict(findings=(), action=None, reason=reason)
            elif self._engine is not None:
                verdict = self._engine.verdict(text)
            else:
                # No wordlists configured: say so rather than reporting a clean
                # result. 'Not looking' must not look like 'nothing found'.
                verdict = Verdict(findings=(), action=None, reason="no_wordlists")

        result = assess(network=network, text=verdict, image=image,
                        image_action=image_action)
        if self.log is not None:
            self.log.append(record_for_assessment(
                result, direction=request.direction if request else "text"))
        return result

    # -- introspection ----------------------------------------------------

    def status(self) -> dict:
        """Counts and switches only - never list entries."""
        return {
            "filtering_disabled": self.filtering_disabled,
            "keyword_filter_disabled": self.keyword_filter_disabled,
            "image_filter_enabled": (
                self.legacy.image_filter_enabled if self.legacy else None),
            "image_filter_level": (
                self.legacy.image_filter_level if self.legacy else None),
            "lists": self.lists.summary(),
            "wordlists_loaded": self.words is not None,
            "hnc_loaded": self.hnc is not None,
            "log_records": len(self.log) if self.log is not None else 0,
        }
