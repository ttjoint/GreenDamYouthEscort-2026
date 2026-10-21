"""The four legacy name lists as mutable containers.

The original kept four separate arrays, each a packed sequence of 144-byte
records with its own counter and capacity. This module models them uniformly,
because the differences that matter are only: which list, how big, and whether
lookups are exact or substring.

Capacities are ``CLOSED`` from ``cmp [count], imm`` in ``Surfgd.dll``:

========  =============  =========  ==========================================
list      count global   capacity   evidence
========  =============  =========  ==========================================
``A``     ``10228120``   ``100``    ``cmp [0x10228120],0x64`` @ ``0x2d34``
``B``     ``10228124``   unbounded  no immediate compare; only loop bounds
``C``     ``1022811c``   ``1000``   ``cmp [0x1022811c],0x3e8`` @ ``0x1acd``
``D``     ``1022812c``   ``1500``   ``cmp [0x1022812c],0x5dc`` @ ``0x27b5``
========  =============  =========  ==========================================

What the lists are *for* comes from the call sites, not from the storage code
(``analysis/probe/p20``, ``p24``):

- ``A`` outbound URL / protocol fingerprints (``DBFP_Send``)
- ``B`` watched process names (``DBFP_Socket``, matched via Toolhelp)
- ``C`` inbound keyword rules (``DBFP_Recv``)
- ``D`` runtime list, the only one with a full add/remove API from the UI

Matching semantics differ per list, and this module does not pretend otherwise:

- ``A``, ``B``, ``D`` use substring (``strstr``) or exact (``strcmp``) matching
  on a plain string.
- ``C`` holds rule strings parsed by :mod:`green_dam.namelist`.

Both are supported; :meth:`NameList.match` dispatches on ``kind``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Iterator, Literal

from .namelist import NameRecord, Rule, encode_record, parse_rule

ListName = Literal["A", "B", "C", "D"]
MatchKind = Literal["substring", "exact", "rule"]

#: Capacity per list. ``None`` means no immediate compare was found.
CAPACITY: dict[str, int | None] = {"A": 100, "B": None, "C": 1000, "D": 1500}

#: What each list is consulted for, and how it matches.
#: ``role`` is descriptive only; nothing branches on it.
SPEC: dict[str, tuple[str, MatchKind]] = {
    "A": ("outbound URL / protocol fingerprint", "substring"),
    "B": ("watched process name", "exact"),
    "C": ("inbound keyword rule", "rule"),
    "D": ("runtime dynamic entry", "substring"),
}


class ListFull(RuntimeError):
    """Raised when an add would exceed the legacy capacity."""


@dataclass
class NameList:
    """A mutable name list with the legacy capacity and matching semantics."""

    name: ListName
    kind: MatchKind | None = None
    capacity: int | None = None
    records: list[NameRecord] = field(default_factory=list)
    #: Descriptive only (from :data:`SPEC`); nothing branches on it.
    role: str = field(default="", init=False)

    def __post_init__(self) -> None:
        if self.name not in CAPACITY:
            raise ValueError(f"unknown list name: {self.name!r}")
        role, default_kind = SPEC[self.name]
        if self.kind is None:
            self.kind = default_kind
        if self.capacity is None:
            self.capacity = CAPACITY[self.name]
        self.role = role

    # -- size -------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.records)

    @property
    def full(self) -> bool:
        return self.capacity is not None and len(self.records) >= self.capacity

    # -- mutation ---------------------------------------------------------

    def add(self, text: str, *, dedupe: bool = True) -> bool:
        """Append ``text``. Returns True when the list changed.

        Mirrors the legacy add path (``0x279b`` for list D): reject empty text,
        reject a duplicate, reject when full. Duplicate detection compares the
        text case-insensitively because every legacy add path lowercased first
        (``0x14076``) - so callers need not pre-normalise.
        """
        if not text:
            return False
        if self.full:
            raise ListFull(
                f"list {self.name} is full ({self.capacity} records)")
        if dedupe and self.contains(text):
            return False
        self.records.append(NameRecord(text=text, in_use=True))
        return True

    def remove(self, text: str) -> bool:
        """Remove the first record whose text matches case-insensitively."""
        needle = text.lower()
        for i, rec in enumerate(self.records):
            if rec.text.lower() == needle:
                del self.records[i]
                return True
        return False

    def clear(self) -> None:
        self.records.clear()

    # -- lookup -----------------------------------------------------------

    def contains(self, text: str) -> bool:
        """Exact (case-insensitive) membership, used by dedupe and list B."""
        needle = text.lower()
        return any(rec.text.lower() == needle for rec in self.records)

    def match(self, text: str) -> NameRecord | None:
        """First record that matches ``text`` under this list's semantics.

        ``exact``     case-insensitive equality
        ``substring`` case-insensitive containment, either direction is NOT
                      tried - the needle (record text) must appear in the haystack
        ``rule``      the record text is a rule; see :meth:`match_rule`
        """
        low = text.lower()
        for rec in self.records:
            if self.kind == "exact":
                if rec.text.lower() == low:
                    return rec
            elif self.kind == "substring":
                if rec.text.lower() in low:
                    return rec
            elif self.kind == "rule":
                if rec.rule.matches(text):
                    return rec
            else:  # pragma: no cover - guarded by __post_init__
                raise AssertionError(f"unknown match kind {self.kind!r}")
        return None

    def match_rule(self, text: str) -> tuple[NameRecord, Rule] | None:
        """Rule-aware match; only meaningful for list C."""
        for rec in self.records:
            rule = rec.rule
            if rule.matches(text):
                return rec, rule
        return None

    # -- serialisation ----------------------------------------------------

    def to_blob(self) -> bytes:
        """Packed form, as the legacy array would hold it."""
        return b"".join(encode_record(rec) for rec in self.records)

    @classmethod
    def from_blob(cls, name: ListName, blob: bytes) -> "NameList":
        """Decode a packed array, dropping slots whose flag is not set."""
        from .namelist import RECORD_SIZE, iter_records

        if len(blob) % RECORD_SIZE:
            raise ValueError(
                f"blob length {len(blob)} is not a multiple of {RECORD_SIZE}")
        out = cls(name=name)
        for rec in iter_records(blob):
            out.records.append(rec)
        if out.full:
            raise ListFull(f"decoded list {name} exceeds capacity")
        return out

    def __iter__(self) -> Iterator[NameRecord]:
        return iter(self.records)


@dataclass
class NameLists:
    """All four lists, addressed by name."""

    lists: dict[str, NameList] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in CAPACITY:
            self.lists.setdefault(name, NameList(name=name))  # type: ignore[arg-type]

    def __getitem__(self, name: str) -> NameList:
        return self.lists[name]

    def __iter__(self) -> Iterator[NameList]:
        return iter(self.lists.values())

    def summary(self) -> dict[str, dict[str, object]]:
        """Counts only - never the entries themselves."""
        return {
            name: {
                "role": SPEC[name][0],
                "kind": SPEC[name][1],
                "count": len(lst),
                "capacity": lst.capacity,
            }
            for name, lst in self.lists.items()
        }


def load_lists(blobs: dict[str, bytes]) -> NameLists:
    """Build :class:`NameLists` from ``{name: packed_blob}``."""
    out = NameLists()
    for name, blob in blobs.items():
        out.lists[name] = NameList.from_blob(name, blob)  # type: ignore[arg-type]
    return out
