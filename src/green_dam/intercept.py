"""The interception decision chain, with no I/O.

This is the part of M2 that can be built and tested before any proxy exists:
given a request description, decide allow / block and say which stage produced
the decision. Transport (stage 1-4 in ``docs/architecture.md``) plugs into this
later and only supplies the inputs.

Order is the original's, and it is load-bearing
------------------------------------------------
``analysis/probe/p17`` and ``p19`` show the legacy filter checked, in this
order, and stopped at the first hit:

1. process is being watched at all
2. port / address list
3. protocol fingerprint (outbound) or binary sniff (inbound)
4. host / URL list
5. content: extracted URL (outbound) or keyword rules (inbound)

The order matters because it determines *why* something was blocked, which is
what the log records. It is also cheapest-first, which is why the original did
it that way: a process check costs nothing, a content scan costs a full body.

What is deliberately not here
-----------------------------
- No socket code, no proxy, no firewall. Those are transport.
- No verdict on *how strong* a signal is. The legacy score-to-action mapping
  was never recovered, so :class:`Decision` carries the reason, not a severity.
- No claim about HTTPS. Stages 2 and 3 supply plaintext or they do not; this
  module cannot tell the difference and does not pretend to.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Literal

from .namelists import NameLists

Direction = Literal["outbound", "inbound"]

#: Ports the original ran inbound keyword filtering on (``p19``).
HTTP_PORTS = frozenset({80, 81, 8080, 8081, 8888, 3128})

#: Error code the original returned to the caller when it blocked a socket.
#: 10054 is WSAECONNRESET - "connection reset by peer", chosen so the blocked
#: program sees a network fault rather than a policy decision.
BLOCK_ERRNO = 10054


class Stage(str, Enum):
    """Which check produced the decision. Ordered as the original ran them."""

    PROCESS = "process"
    ADDRESS = "address"
    FINGERPRINT = "fingerprint"
    HOST = "host"
    CONTENT = "content"


class Outcome(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"


@dataclass(frozen=True)
class Request:
    """Everything the chain is allowed to look at.

    Fields the transport cannot supply stay ``None``/empty; the chain then skips
    that stage rather than guessing. This is how stage 1 (no content) degrades
    gracefully into stage 3 (full content) without a separate code path.
    """

    direction: Direction
    #: Name of the process that owns the connection, if known.
    process: str | None = None
    #: Destination port, if known.
    port: int | None = None
    #: Destination address as text (hostname, SNI, or literal IP).
    host: str | None = None
    #: First bytes of the payload, for the fingerprint / binary-sniff stage.
    prefix: bytes = b""
    #: Decoded text body, when the transport could decrypt it.
    body: str | None = None


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    stage: Stage | None
    reason: str
    #: The list record that matched, kept for logging. Never rendered by tests.
    detail: str = ""

    @property
    def blocked(self) -> bool:
        return self.outcome is Outcome.BLOCK

    @property
    def errno(self) -> int | None:
        """The errno the original would have surfaced, for outbound blocks."""
        return BLOCK_ERRNO if self.blocked else None


#: Bytes that mark a payload as binary rather than text. The original sniffed
#: the first 10 bytes for values >= 0x80 (``p19``); here the rule is stated as
#: "any byte outside printable ASCII plus common whitespace" which is the same
#: test without hard-coding a byte-count.
_PRINTABLE = set(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}


def looks_binary(prefix: bytes, *, limit: int = 10) -> bool:
    """True when the first ``limit`` bytes contain a non-printable byte."""
    if not prefix:
        return False
    return any(b not in _PRINTABLE for b in prefix[:limit])


def extract_outbound_url(body: str) -> str | None:
    """Pull a URL out of an HTTP request line, if the body starts with one.

    The original compared ``body+4`` against ``http://`` and ``https:/`` (7
    bytes each, ``p18``), i.e. it expected a method prefix like ``GET ``. Here
    the method prefix is located rather than assumed, so both ``GET http://x``
    and a bare ``http://x`` are handled.
    """
    for marker in ("http://", "https://"):
        idx = body.find(marker)
        if idx < 0:
            continue
        end = idx
        while end < len(body) and body[end] not in " \r\n\t":
            end += 1
        return body[idx:end]
    return None


@dataclass
class Interceptor:
    """Runs the decision chain against the four name lists.

    ``log`` is optional. When supplied, every decision is appended to it; when
    not, :meth:`decide` stays a pure function, which is what most tests want.
    Logging lives here rather than in callers because the interceptor is the
    only place that knows which stage fired, and that is the field the log
    exists to record.
    """

    lists: NameLists = field(default_factory=NameLists)
    log: object | None = None

    def decide(self, request: Request) -> Decision:
        decision = self._decide(request)
        if self.log is not None:
            from .eventlog import record_for_decision

            self.log.append(record_for_decision(decision,
                                                direction=request.direction))
        return decision

    def _decide(self, request: Request) -> Decision:
        for stage_fn in self._stages(request.direction):
            decision = stage_fn(request)
            if decision is not None:
                return decision
        return Decision(Outcome.ALLOW, None, "no_stage_matched")

    # -- stage dispatch ---------------------------------------------------

    def _stages(self, direction: Direction):
        if direction == "outbound":
            return (
                self._stage_process,
                self._stage_address,
                self._stage_host,
                self._stage_outbound_content,
            )
        if direction == "inbound":
            # Binary sniffing is *inside* the content stage, not a stage of its
            # own: a binary body from a listed host must still be blocked, so a
            # sniff cannot short-circuit the chain with an allow.
            return (
                self._stage_process,
                self._stage_address,
                self._stage_host,
                self._stage_inbound_content,
            )
        raise ValueError(f"unknown direction: {direction!r}")

    # -- stages -----------------------------------------------------------

    def _stage_process(self, req: Request) -> Decision | None:
        if not req.process:
            return None
        if self.lists["B"].match(req.process) is not None:
            return Decision(Outcome.BLOCK, Stage.PROCESS, "watched_process")
        return None

    def _stage_address(self, req: Request) -> Decision | None:
        if req.port is None:
            return None
        target = f"{req.host or ''}:{req.port}"
        if self.lists["D"].match(target) is not None:
            return Decision(Outcome.BLOCK, Stage.ADDRESS, "address_listed")
        return None

    def _stage_host(self, req: Request) -> Decision | None:
        if not req.host:
            return None
        if self.lists["A"].match(req.host) is not None:
            return Decision(Outcome.BLOCK, Stage.HOST, "host_listed")
        return None

    def _stage_outbound_content(self, req: Request) -> Decision | None:
        if not req.body:
            return None
        url = extract_outbound_url(req.body)
        if url is None:
            return None
        if self.lists["A"].match(url) is not None:
            return Decision(Outcome.BLOCK, Stage.CONTENT, "url_in_body")
        return None

    def _stage_inbound_content(self, req: Request) -> Decision | None:
        if not req.body:
            return None
        # A binary body cannot be keyword-filtered; the original skipped it too
        # (p19). This suppresses the *content* check only - earlier stages have
        # already had their chance to block.
        if looks_binary(req.prefix):
            return None
        if req.port is not None and req.port not in HTTP_PORTS:
            # Keyword filtering only ran on the HTTP ports (p19).
            return None
        hit = self.lists["C"].match_rule(req.body)
        if hit is not None:
            return Decision(Outcome.BLOCK, Stage.CONTENT, "keyword_rule")
        return None


def blocking_stages(decisions: Iterable[Decision]) -> list[Stage]:
    """Stages that produced a block, in order. Convenience for logging tests."""
    return [d.stage for d in decisions if d.blocked and d.stage is not None]
