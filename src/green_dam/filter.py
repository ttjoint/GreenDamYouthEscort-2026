"""One entry point that wires the layers together.

Everything below this module is deliberately independent: ``sysdat`` decodes,
``namelists`` stores, ``intercept`` decides, ``policy`` weighs evidence,
``eventlog`` records. That independence is why each piece is testable on its
own, and also why nothing below answers the only question a caller actually
has: *given this input, what do I do, and what gets written down?*

:class:`Filter` is that answer.

Four evidence paths, one shape
------------------------------
There are four independent sources of evidence:

============  ==========================  ==============================
path          produced by                 carries
============  ==========================  ==============================
``network``   :mod:`green_dam.intercept`  a :class:`Decision`
``text``      :mod:`green_dam.policy`     a :class:`Verdict`
``image``     :mod:`green_dam.imaging`    a :class:`ScreenResult`
``process``   the name list B             a :class:`Decision`
============  ==========================  ==============================

The process path is not separate machinery: list B is the first stage of the
network chain, and the original reached it through the same ``DBFP_Socket``
hook as everything else. It is listed separately here because callers ask about
it separately ("is this program watched?"), and :meth:`Filter.check_process`
answers that without inventing a fake request.

Each path has a ``check_*`` method that runs *only* that path and returns its
native measurement. :meth:`Filter.check` runs any combination and returns one
:class:`~green_dam.policy.Assessment`. Crucially, ``check`` calls the same
helpers the single-path methods use, so the two cannot disagree - a bug this
module actually had: ``check`` once skipped the HNC matcher that
``check_text`` consulted, so the same text gave different answers depending on
which entry point you used.

Gating happens once
-------------------
``sysdat`` slots 2 and 10 mean "stop filtering" and "stop keyword filtering".
Rather than each path testing them, every path asks :meth:`Filter._gate` and the
answer is recorded as a ``gated`` event. That keeps "looked and found nothing"
distinguishable from "was told not to look" - the difference the original
product hid from its users.

A gate suppresses evidence; it never manufactures ``allow``. A gated
:class:`Assessment` has ``action=None`` and a reason naming the switch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from . import config as config_mod
from . import sysdat
from .eventlog import (
    EventLog,
    record_for_assessment,
    record_for_decision,
    record_for_gate,
)
from .imaging import ScreenResult, screen
from .intercept import Decision, Interceptor, Request
from .namelists import NameLists
from .policy import (
    ACTION_STRENGTH,
    Assessment,
    PolicyEngine,
    PolicyTable,
    Verdict,
    assess,
)
from .wordlists import WordlistIndex

#: The four evidence paths, in the order ``assess`` ranks them.
Path = Literal["network", "text", "image", "process"]


@dataclass
class Filter:
    """Configured filter: policy array + lists + wordlists + log."""

    legacy: sysdat.PolicyArray | None = None
    lists: NameLists = field(default_factory=NameLists)
    words: WordlistIndex | None = None
    #: Optional HNC score-table matcher (see green_dam.semantic). A second,
    #: independent text evidence source: wordlists come from the external .lib
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

    # -- construction ------------------------------------------------------

    @classmethod
    def from_config_dir(cls, path: Path, *, log: EventLog | None = None,
                        words: WordlistIndex | None = None,
                        hnc: object | None = None) -> "Filter":
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
        return cls(legacy=legacy, log=log, words=words, hnc=hnc)

    # -- switches ----------------------------------------------------------

    @property
    def filtering_disabled(self) -> bool:
        return self.legacy is not None and self.legacy.filtering_disabled

    @property
    def keyword_filter_disabled(self) -> bool:
        return self.legacy is not None and self.legacy.keyword_filter_disabled

    @property
    def image_filter_enabled(self) -> bool:
        """True only when a policy array exists *and* enables it.

        No configuration means no image filtering: "not configured" must not
        read as "enabled".
        """
        return self.legacy is not None and self.legacy.image_filter_enabled

    def _gate(self, *, keyword: bool = False, image: bool = False) -> str | None:
        """The reason a path is switched off, or None when it is on.

        ``keyword`` adds slot 10; ``image`` adds slot 13. Slot 2 gates
        everything.
        """
        if self.filtering_disabled:
            return "legacy_filtering_disabled"
        if keyword and self.keyword_filter_disabled:
            return "legacy_keyword_filter_disabled"
        if image and not self.image_filter_enabled:
            return "image_filter_disabled"
        return None

    def _record_gate(self, reason: str) -> None:
        if self.log is not None:
            self.log.append(record_for_gate(reason))

    # -- path: network -----------------------------------------------------

    def check_request(self, request: Request) -> Assessment:
        """Decide a network request, honouring off-switches, and log it."""
        reason = self._gate()
        if reason is not None:
            self._record_gate(reason)
            return Assessment(action=None, source="policy", reason=reason)

        decision = self._interceptor.decide(request)
        if self.log is not None:
            self.log.append(record_for_decision(decision,
                                                direction=request.direction))
        return assess(network=decision)

    # -- path: process -----------------------------------------------------

    def check_process(self, process: str) -> Decision:
        """Is this program on the watched list (name list B)?

        List B is the process stage of the network chain, so this reuses the
        same matcher rather than duplicating it. Returns a :class:`Decision`
        because that is what the chain produces; the caller can pass it to
        :func:`green_dam.policy.assess` or just read ``blocked``.
        """
        from .intercept import Outcome, Stage

        reason = self._gate()
        if reason is not None:
            self._record_gate(reason)
            return Decision(Outcome.ALLOW, None, reason)

        record = self.lists["B"].match(process)
        if record is not None:
            return Decision(Outcome.BLOCK, Stage.PROCESS, "watched_process")
        return Decision(Outcome.ALLOW, None, "not_watched")

    # -- path: text --------------------------------------------------------

    def _text_findings(self, text: str) -> list:
        """Every text finding from every configured source.

        This is the single place text evidence is gathered. ``check_text`` and
        ``check`` both call it, so they cannot disagree about what a text
        contains.
        """
        findings: list = []
        engine = self._engine
        if engine is not None:
            findings.extend(engine._word_findings(text))
        if self.hnc is not None:
            from .semantic import analyse_findings

            findings.extend(analyse_findings(self.hnc, text))
        return findings

    def _text_verdict(self, text: str) -> Verdict:
        """Turn gathered findings into a Verdict, keeping reasons honest."""
        findings = self._text_findings(text)
        engine = self._engine

        if not findings:
            # Distinguish "no source configured" from "configured, no hits".
            # Collapsing them would make "not looking" read as "nothing found".
            if engine is None and self.hnc is None:
                return Verdict(findings=(), action=None, reason="no_wordlists")
            return Verdict(findings=(), action=None, reason="no_evidence")

        if engine is not None:
            return engine.verdict(text, findings=findings)
        return self._verdict_from_findings(findings)

    def check_text(self, text: str) -> Verdict:
        """Judge text against every configured text source."""
        reason = self._gate(keyword=True)
        if reason is not None:
            self._record_gate(reason)
            return Verdict(findings=(), action=None, reason=reason)

        verdict = self._text_verdict(text)
        if self.log is not None:
            self.log.append(record_for_assessment(assess(text=verdict),
                                                  direction="text"))
        return verdict

    @property
    def _engine(self) -> PolicyEngine | None:
        if self.words is None:
            return None
        return PolicyEngine(words=self.words, policies=self.policies)

    @staticmethod
    def _verdict_from_findings(findings) -> Verdict:
        """Build a Verdict from findings when no wordlist engine exists.

        Only HNC evidence can reach here, and HNC findings carry no action, so
        the action is None by construction. Kept explicit rather than assumed.
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

    # -- path: image -------------------------------------------------------

    def check_image(self, *, width: int, height: int,
                    red_channel: bytes | None = None) -> ScreenResult:
        """Screen one frame, honouring the image-filter switches.

        Returns the measurement only. Whether a suspect frame becomes a block is
        :meth:`check`'s job via ``image_action``, because the vendor's
        score-to-action mapping is still unknown.
        """
        reason = self._gate(image=True)
        if reason is not None:
            self._record_gate(reason)
            return ScreenResult(suspect=False, detail=reason)

        return screen(width=width, height=height, red_channel=red_channel)

    # -- combined ----------------------------------------------------------

    def check(self, *, request: Request | None = None,
              text: str | None = None,
              image: ScreenResult | None = None,
              image_action=None,
              process: str | None = None) -> Assessment:
        """Judge any combination of the four paths; return one action.

        Precedence is :func:`green_dam.policy.assess`'s: a network or process
        block wins, then the strongest remaining action, then nothing.

        Every path is evaluated through the same helper its ``check_*`` method
        uses, so the combined answer never contradicts the single-path answer.
        Exactly one log record is written.
        """
        network: Decision | None = None
        verdict: Verdict | None = None
        screen_result: ScreenResult | None = image

        # -- network and process both come from the same chain, so run the
        #    interceptor once with whatever the caller supplied.
        if request is not None or process is not None:
            reason = self._gate()
            if reason is not None:
                self._record_gate(reason)
                return Assessment(action=None, source="policy", reason=reason)

            if request is not None:
                network = self._interceptor.decide(request)
            if process is not None and (
                    network is None or not network.blocked):
                # Only consult the process list when the request did not
                # already block, so the recorded reason names the first hit -
                # the same first-hit-wins rule the original used.
                watched = self.check_process(process)
                if watched.blocked:
                    network = watched

        # -- text
        if text is not None:
            reason = self._gate(keyword=True)
            if reason is not None:
                self._record_gate(reason)
                verdict = Verdict(findings=(), action=None, reason=reason)
            else:
                verdict = self._text_verdict(text)

        # -- image: gate it here too. The single-path method already does, but
        #    check() must not rely on the caller having used it.
        if screen_result is not None and image_action is not None:
            reason = self._gate(image=True)
            if reason is not None:
                self._record_gate(reason)
                image_action = None

        result = assess(network=network, text=verdict, image=screen_result,
                        image_action=image_action)

        if self.log is not None:
            direction = (request.direction if request is not None
                         else ("text" if text is not None else ""))
            self.log.append(record_for_assessment(result, direction=direction))
        return result

    # -- introspection -----------------------------------------------------

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
