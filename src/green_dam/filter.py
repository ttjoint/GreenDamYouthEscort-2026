"""One entry point that wires the layers together.

Everything below this module is deliberately independent: ``sysdat`` decodes,
``gate`` decides what may run, ``namelists`` stores, ``intercept`` decides,
``textpath`` gathers text evidence, ``policy`` weighs evidence, ``eventlog``
records. That independence is why each piece is testable on its own, and also
why nothing below answers the only question a caller actually has: *given this
input, what do I do, and what gets written down?*

:class:`Filter` is that answer, and nothing more. It owns configuration and
orchestration; the four evidence paths live in their own modules:

============  ==========================  ==============================
path          delegated to                returns
============  ==========================  ==============================
``network``   :mod:`green_dam.intercept`  :class:`Decision`
``text``      :mod:`green_dam.textpath`   :class:`Verdict`
``image``     :mod:`green_dam.imaging`    :class:`ScreenResult`
``process``   name list B                 :class:`Decision`
============  ==========================  ==============================

The process path is not separate machinery: list B is the first stage of the
network chain, and the original reached it through the same ``DBFP_Socket`` hook.
It is exposed separately because callers ask about it separately ("is this
program watched?"), and :meth:`Filter.check_process` answers that without
inventing a fake request.

Gating
------
:class:`~green_dam.gate.Gate` answers "may this path run". This module asks it,
records the answer as a ``gated`` event, and never turns that answer into
``allow``. A gated result has ``action=None`` and a reason naming the switch.
That distinction - "looked and found nothing" versus "was told not to look" - is
the core behavioural correction this project makes against the original.

One record per call
-------------------
:meth:`Filter.check` writes exactly one log record regardless of how many paths
ran. Gates encountered on the way are collected and recorded once, at the end.
Writing one record per path would double every count and made a gated call emit
two records before this was fixed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import config as config_mod
from . import sysdat
from .eventlog import (
    EventLog,
    record_for_assessment,
    record_for_decision,
    record_for_gate,
)
from .gate import Gate
from .imaging import ScreenResult, screen
from .intercept import Decision, Interceptor, Outcome, Request, Stage
from .namelists import NameLists
from .policy import Assessment, PolicyEngine, PolicyTable, Verdict, assess
from .textpath import TextSources, evaluate
from .wordlists import WordlistIndex


@dataclass
class Filter:
    """Configured filter: policy array + lists + text sources + log."""

    legacy: sysdat.PolicyArray | None = None
    lists: NameLists = field(default_factory=NameLists)
    words: WordlistIndex | None = None
    #: Optional HNC score-table matcher (see green_dam.semantic). A second,
    #: independent text evidence source: wordlists come from the external .lib
    #: files, this one from the HNC score table.
    hnc: object | None = None
    #: Optional :class:`green_dam.backends.SemanticBackend`. When set, the text
    #: path consults it *instead of* ``hnc`` directly, so a caller can swap in
    #: the reference backend without touching Filter. ``hnc`` stays supported
    #: because that is what the existing call sites pass.
    backend: object | None = None
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
                        hnc: object | None = None,
                        backend: object | None = None) -> "Filter":
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
        return cls(legacy=legacy, log=log, words=words, hnc=hnc,
                   backend=backend)

    # -- switches ----------------------------------------------------------

    @property
    def gate(self) -> Gate:
        """The switches, derived from the policy array."""
        return Gate(legacy=self.legacy)

    @property
    def filtering_disabled(self) -> bool:
        return self.gate.filtering_disabled

    @property
    def keyword_filter_disabled(self) -> bool:
        return self.gate.keyword_filter_disabled

    @property
    def image_filter_enabled(self) -> bool:
        return self.gate.image_filter_enabled

    def _record_gate(self, reason: str) -> None:
        if self.log is not None:
            self.log.append(record_for_gate(reason))

    # -- text sources ------------------------------------------------------

    @property
    def text_sources(self) -> TextSources:
        return TextSources(wordlists=self.words, backend=self.backend,
                           matcher=self.hnc)

    @property
    def _engine(self) -> PolicyEngine | None:
        if self.words is None:
            return None
        return PolicyEngine(words=self.words, policies=self.policies)

    def _text_verdict(self, text: str) -> Verdict:
        """The text pipeline, with the engine built once per call."""
        return evaluate(self.text_sources, text, self._engine)

    # -- path: network -----------------------------------------------------

    def check_request(self, request: Request) -> Assessment:
        """Decide a network request, honouring off-switches, and log it."""
        reason = self.gate.reason()
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

        List B is the process stage of the network chain, so this reuses the same
        matcher rather than duplicating it.
        """
        reason = self.gate.reason()
        if reason is not None:
            self._record_gate(reason)
            return Decision(Outcome.ALLOW, None, reason)

        if self.lists["B"].match(process) is not None:
            return Decision(Outcome.BLOCK, Stage.PROCESS, "watched_process")
        return Decision(Outcome.ALLOW, None, "not_watched")

    # -- path: text --------------------------------------------------------

    def check_text(self, text: str) -> Verdict:
        """Judge text against every configured text source."""
        reason = self.gate.reason(keyword=True)
        if reason is not None:
            self._record_gate(reason)
            return Verdict(findings=(), action=None, reason=reason)

        verdict = self._text_verdict(text)
        if self.log is not None:
            self.log.append(record_for_assessment(assess(text=verdict),
                                                  direction="text"))
        return verdict

    # -- path: image -------------------------------------------------------

    def check_image(self, *, width: int, height: int,
                    red_channel: bytes | None = None) -> ScreenResult:
        """Screen one frame, honouring the image-filter switches.

        Returns the measurement only. Whether a suspect frame becomes a block is
        :meth:`check`'s job via ``image_action``.
        """
        reason = self.gate.reason(image=True)
        if reason is not None:
            self._record_gate(reason)
            return ScreenResult(suspect=False, detail=reason)

        return screen(width=width, height=height, red_channel=red_channel)

    # -- combined ----------------------------------------------------------

    def _resolve_network(self, request: Request | None,
                         process: str | None) -> tuple[Decision | None,
                                                       str | None]:
        """Run the network and process stages, returning (decision, gate).

        The process list is consulted only when the request did not already
        block, so the recorded reason names the first hit - the same
        first-hit-wins rule the original used.
        """
        reason = self.gate.reason()
        if reason is not None:
            return None, reason

        decision: Decision | None = None
        if request is not None:
            decision = self._interceptor.decide(request)
        if process is not None and (decision is None or not decision.blocked):
            watched = self.check_process(process)
            if watched.blocked:
                decision = watched
        return decision, None

    def _resolve_text(self, text: str | None) -> tuple[Verdict | None,
                                                       str | None]:
        if text is None:
            return None, None
        reason = self.gate.reason(keyword=True)
        if reason is not None:
            return Verdict(findings=(), action=None, reason=reason), reason
        return self._text_verdict(text), None

    def _resolve_image(self, image: ScreenResult | None,
                       image_action) -> tuple[ScreenResult | None, object,
                                              str | None]:
        """Apply the image gate to a caller-supplied screen result."""
        if image is None or image_action is None:
            return image, image_action, None
        reason = self.gate.reason(image=True)
        if reason is not None:
            # The frame may still be reported as evidence; only the action is
            # withheld. Dropping the measurement would lose information the
            # caller already computed.
            return image, None, reason
        return image, image_action, None

    def check(self, *, request: Request | None = None,
              text: str | None = None,
              image: ScreenResult | None = None,
              image_action=None,
              process: str | None = None) -> Assessment:
        """Judge any combination of the four paths; return one action.

        Precedence is :func:`green_dam.policy.assess`'s: a network or process
        block wins, then the strongest remaining action, then nothing.

        Every path is resolved by the same helper its ``check_*`` method uses, so
        the combined answer never contradicts the single-path answer. Exactly one
        log record is written.
        """
        network, network_gate = self._resolve_network(request, process)
        if network_gate is not None:
            # Slot 2 gates every path, so nothing else can produce evidence.
            self._record_gate(network_gate)
            return Assessment(action=None, source="policy", reason=network_gate)

        verdict, text_gate = self._resolve_text(text)
        screen_result, image_action, image_gate = self._resolve_image(
            image, image_action)

        gates = [g for g in (text_gate, image_gate) if g is not None]

        # Every supplied path was gated: nothing was judged, so say that rather
        # than reporting a clean run.
        supplied = sum(x is not None for x in (request, process, text, image))
        judged = network is not None or (verdict is not None
                                         and verdict.action is not None)
        if gates and len(gates) >= supplied and not judged \
                and image_action is None:
            self._record_gate(gates[0])
            return Assessment(action=None, source="policy", reason=gates[0])

        result = assess(network=network, text=verdict, image=screen_result,
                        image_action=image_action)

        if self.log is not None:
            self.log.append(record_for_assessment(
                result, direction=self._direction(request, text)))
        return result

    @staticmethod
    def _direction(request: Request | None, text: str | None) -> str:
        if request is not None:
            return request.direction
        return "text" if text is not None else ""

    # -- introspection -----------------------------------------------------

    def status(self) -> dict:
        """Counts and switches only - never list entries."""
        return {
            **self.gate.summary(),
            "image_filter_level": (
                self.legacy.image_filter_level if self.legacy else None),
            "lists": self.lists.summary(),
            "wordlists_loaded": self.words is not None,
            # Kept under its original name: callers and tests already use it,
            # and renaming it would be churn for no gain. It means "a text
            # backend or matcher is configured".
            "hnc_loaded": self.text_sources.configured,
            "text_sources_loaded": self.text_sources.configured,
            "log_records": len(self.log) if self.log is not None else 0,
        }
