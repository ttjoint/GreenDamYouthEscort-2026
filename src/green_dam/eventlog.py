"""Structured event logging for filter decisions.

Why this exists separately from :mod:`logging`
----------------------------------------------
Three requirements from ``docs/architecture.md`` cannot be met by ``logging``
alone:

1. **The log must say *why*.** A block from a host list and a block from a
   keyword are different facts. The record carries ``stage``/``reason``/
   ``source`` as fields, not buried in a message string.
2. **The log must be auditable.** Records are serialisable to JSON lines so an
   administrator can read, diff and archive them without parsing prose.
3. **The log must not leak content.** The original product uploaded collected
   text; that is explicitly not carried over. Here a record stores *where* a
   match happened (list name, byte span) and *what kind* of rule fired - never
   the matched text, never the body, never the host's full path.

The last point is the reason this module does not accept a ``Request`` and
render it. It accepts an :class:`~green_dam.intercept.Decision` or a
:class:`~green_dam.policy.Assessment` and extracts only the fields that are
safe by construction.

Redaction rule, stated once
---------------------------
Anything derived from user content is represented by **span and digest**, not
text. ``digest`` is a truncated SHA-256 of the matched substring, which lets an
administrator correlate two records ("same thing blocked twice") without the
log ever containing the thing.
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import IO, Iterable, Iterator

#: How many hex characters of the content digest are kept. 16 hex chars = 64
#: bits, enough to make accidental collisions negligible for correlation while
#: staying short in logs.
DIGEST_CHARS = 16


def digest(text: str) -> str:
    """Truncated SHA-256 of user content, for correlation without disclosure."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:DIGEST_CHARS]


class Event(str, Enum):
    """What kind of thing happened."""

    BLOCK = "block"
    ALLOW = "allow"
    #: A decision that produced no action (neither allow nor block).
    NO_ACTION = "no_action"
    #: The policy layer silenced evidence via a sys.dat off-switch.
    GATED = "gated"


@dataclass(frozen=True)
class LogRecord:
    """One audit line. Every field is safe to write to disk.

    ``content_digest`` is the only thing derived from user content, and it is a
    one-way truncated hash. ``detail`` is a short machine token (``"host_listed"``
    etc.), never a rendered sentence containing content.
    """

    event: Event
    #: Which layer decided: "network", "text", "policy".
    source: str = ""
    #: For network decisions: which stage fired (``"host"``, ``"content"``...).
    stage: str = ""
    #: Short machine token explaining the outcome.
    reason: str = ""
    #: Direction, for network events.
    direction: str = ""
    #: Length of the matched span, when there was one. 0 when not applicable.
    span_length: int = 0
    #: Truncated digest of the matched content, when there was one.
    content_digest: str = ""
    #: Wall-clock time, UTC, second resolution. Injected for testability.
    when: str = ""
    #: Monotonic sequence number, assigned by the writer.
    seq: int = 0

    def to_json(self) -> str:
        """One JSON object, no trailing newline."""
        payload = {
            "seq": self.seq,
            "when": self.when,
            "event": self.event.value,
            "source": self.source,
            "stage": self.stage,
            "reason": self.reason,
            "direction": self.direction,
            "span_length": self.span_length,
            "content_digest": self.content_digest,
        }
        # Drop empty fields so the log stays readable; keep seq/when/event.
        return json.dumps(
            {k: v for k, v in payload.items() if v not in ("", 0) or k in ("seq", "when", "event")},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def record_for_decision(decision, *, when: str | None = None) -> LogRecord:
    """Build a record from an :class:`intercept.Decision`.

    Only ``stage``/``reason``/``direction`` are read. The ``detail`` field of
    the decision is deliberately ignored: it may hold a matched string.
    """
    event = Event.BLOCK if getattr(decision, "blocked", False) else Event.ALLOW
    stage = getattr(decision, "stage", None)
    return LogRecord(
        event=event,
        source="network",
        stage=stage.value if isinstance(stage, Enum) else (str(stage) if stage else ""),
        reason=str(getattr(decision, "reason", "") or ""),
        when=when or utc_now(),
    )


def record_for_assessment(assessment, *, direction: str = "", when: str | None = None) -> LogRecord:
    """Build a record from a :class:`policy.Assessment`."""
    action = assessment.action
    if action == "block":
        event = Event.BLOCK
    elif action is None:
        event = Event.NO_ACTION
    else:
        event = Event.ALLOW

    # Prefer the network stage when the network side is what blocked.
    stage = ""
    if assessment.source == "network" and assessment.network is not None:
        raw = getattr(assessment.network, "stage", None)
        stage = raw.value if isinstance(raw, Enum) else (str(raw) if raw else "")

    return LogRecord(
        event=event,
        source=assessment.source or "",
        stage=stage,
        reason=assessment.reason or "",
        direction=direction,
        when=when or utc_now(),
    )


def record_for_gate(reason: str, *, when: str | None = None) -> LogRecord:
    """Build a record for a sys.dat off-switch silencing evidence."""
    return LogRecord(event=Event.GATED, source="policy", reason=reason,
                     when=when or utc_now())


def utc_now() -> str:
    """Second-resolution UTC timestamp in ISO-8601 with a trailing ``Z``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class EventLog:
    """Append-only in-memory log with an optional JSON-lines sink.

    The sink is opened lazily and flushed per record so a crash loses at most
    the record in flight. Writing is guarded by a lock because the service host
    will call this from several threads.
    """

    path: Path | None = None
    records: list[LogRecord] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _seq: int = 0
    _sink: IO[str] | None = field(default=None, repr=False)

    def append(self, record: LogRecord) -> LogRecord:
        """Assign a sequence number, store, and write to the sink if any."""
        with self._lock:
            self._seq += 1
            stamped = LogRecord(
                event=record.event,
                source=record.source,
                stage=record.stage,
                reason=record.reason,
                direction=record.direction,
                span_length=record.span_length,
                content_digest=record.content_digest,
                when=record.when or utc_now(),
                seq=self._seq,
            )
            self.records.append(stamped)
            if self.path is not None:
                if self._sink is None:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self._sink = self.path.open("a", encoding="utf-8", newline="\n")
                self._sink.write(stamped.to_json() + "\n")
                self._sink.flush()
            return stamped

    def extend(self, records: Iterable[LogRecord]) -> None:
        for rec in records:
            self.append(rec)

    def close(self) -> None:
        with self._lock:
            if self._sink is not None:
                self._sink.close()
                self._sink = None

    def __enter__(self) -> "EventLog":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- queries used by tests and the future console ----------------------

    def blocked(self) -> list[LogRecord]:
        return [r for r in self.records if r.event is Event.BLOCK]

    def by_stage(self, stage: str) -> list[LogRecord]:
        return [r for r in self.records if r.stage == stage]

    def __len__(self) -> int:
        return len(self.records)


def read_jsonl(path: Path) -> Iterator[dict]:
    """Read back a JSON-lines log. Used by tests and audit tooling."""
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)
