"""The legacy 144-byte name-list record and its rule grammar.

Two things live here, both decoded from ``Surfgd.dll``:

* :class:`NameRecord` - the fixed 144-byte (``0x90``) slot the original used
  for every name list. Layout is in ``analysis/probe/p21_keyword_record.txt``.
* :func:`parse_rule` / :class:`Rule` - the ``{a,b}[c,d]`` grammar that the
  inbound keyword filter matched with, decoded from the matcher at ``0x1000219e``.

Grammar (``CLOSED``, from the matcher's byte comparisons)
--------------------------------------------------------
===========  ====  ===============================================
token        code  meaning
===========  ====  ===============================================
``{``        7b    open a group; groups are ANDed together
``}``        7d    close a group
``[``        5b    open a class inside a group; entries are ORed
``]``        5d    close a class
``,``        2c    separator between entries
===========  ====  ===============================================

So ``{a,b}[c,d]`` means "contains (a or b) AND contains (c or d)". A group with
no ``[]`` is simply an OR of its entries, which is the common single-term case.

Why a parser instead of the original's five nested loops
-------------------------------------------------------
The original walked up to five groups with five nested counters. That structure
is an implementation detail, not semantics; here the rule is parsed once into a
tuple of frozensets and evaluated with ``all()``/``any()``. Same truth table,
readable code. The group cap is preserved as a documented limit because the
original really does cap at five - a sixth group is rejected rather than
silently ignored.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterable, Iterator

#: Size of one name-list slot in the legacy layout.
RECORD_SIZE = 144
#: Offset of the rule/name string inside a record.
RECORD_TEXT_OFFSET = 0
#: Offset of the "this slot is in use" flag.
RECORD_FLAG_OFFSET = 0x84
#: Maximum number of ``{}`` groups the original matcher walks.
MAX_GROUPS = 5
#: Maximum number of entries in one class/group (bounded by the 0x50-byte
#: scratch buffer the original used per word).
MAX_ENTRIES_PER_GROUP = 0x50

#: Text field width available before the flag byte.
TEXT_FIELD_SIZE = RECORD_FLAG_OFFSET - RECORD_TEXT_OFFSET
#: End offset (exclusive) of the text field.
TEXT_FIELD_END = RECORD_TEXT_OFFSET + TEXT_FIELD_SIZE


class RuleSyntaxError(ValueError):
    """Raised when a rule string does not match the legacy grammar."""


@dataclass(frozen=True)
class Rule:
    """A parsed legacy rule: groups of alternatives, ANDed together.

    ``groups`` is a tuple of frozensets. Each frozenset is an OR; the tuple is
    an AND. An empty rule (no groups) matches nothing.
    """

    groups: tuple[frozenset[str], ...]
    source: str = ""

    def matches(self, text: str) -> bool:
        """True when every group has at least one alternative present in text.

        Matching is a plain substring test, as the original used ``strstr``.
        Callers that need case-insensitivity must lowercase both sides; this
        method does not, because the original's lowercasing happened upstream
        in the record-building path, not in the matcher.
        """
        if not self.groups:
            return False
        return all(
            any(entry in text for entry in group)
            for group in self.groups
        )

    @property
    def terms(self) -> tuple[str, ...]:
        """Every alternative, deduplicated, in first-seen order."""
        seen: dict[str, None] = {}
        for group in self.groups:
            for entry in sorted(group):
                seen.setdefault(entry, None)
        return tuple(seen)


def _read_bracketed(text: str, i: int, source: str) -> tuple[frozenset[str], int]:
    """Read one ``{...}`` or ``[...]`` group starting at ``i``.

    Returns the group's entries and the index just past the closing bracket.
    Extracted from :func:`parse_rule` so the main loop stays shallow enough to
    read: the bracket handling is a self-contained step and nesting it inline
    pushed the function to six levels.
    """
    opener = text[i]
    closer = "}" if opener == "{" else "]"
    end = text.find(closer, i + 1)
    if end < 0:
        raise RuleSyntaxError(f"unclosed {opener!r} in rule: {source!r}")
    entries = _split_entries(text[i + 1:end], source)
    return frozenset(entries), end + 1


def _read_bare(text: str, i: int, source: str) -> tuple[str | None, int]:
    """Read one unbracketed entry starting at ``i``.

    Returns the entry (or None when it was blank) and the index where scanning
    should resume.
    """
    n = len(text)
    end = i
    while end < n and text[end] not in "{[,]}":
        end += 1
    piece = text[i:end].strip()
    if piece and any(c in piece for c in "{}[]"):
        raise RuleSyntaxError(f"nested bracket in rule: {source!r}")
    return (piece or None), end


def parse_rule(source: str) -> Rule:
    """Parse a legacy rule string.

    Accepts both the fully bracketed form ``{a,b}[c,d]`` and the bare
    comma-separated form ``a,b`` (which the original treats as one OR group).
    """
    text = source.strip()
    if not text:
        return Rule(groups=(), source=source)

    groups: list[frozenset[str]] = []
    # Bare (unbracketed) runs accumulate into ONE group until a bracket starts.
    bare: list[str] = []
    i = 0
    n = len(text)
    saw_bracket = False

    while i < n:
        ch = text[i]
        if ch in "{[":
            if bare:
                groups.append(frozenset(bare))
                bare.clear()
            saw_bracket = True
            group, i = _read_bracketed(text, i, source)
            if group:
                groups.append(group)
            while i < n and text[i] in ", \t":
                i += 1
        elif ch in "}]":
            raise RuleSyntaxError(f"unexpected {ch!r} at {i} in rule: {source!r}")
        elif ch == ",":
            if saw_bracket:
                raise RuleSyntaxError(
                    f"stray {ch!r} at {i} in rule: {source!r}")
            i += 1
        else:
            piece, i = _read_bare(text, i, source)
            if piece:
                bare.append(piece)

    if bare:
        groups.append(frozenset(bare))

    if len(groups) > MAX_GROUPS:
        raise RuleSyntaxError(
            f"rule has {len(groups)} groups, legacy matcher caps at {MAX_GROUPS}: "
            f"{source!r}")

    for group in groups:
        if len(group) > MAX_ENTRIES_PER_GROUP:
            raise RuleSyntaxError(
                f"group has {len(group)} entries, legacy cap is "
                f"{MAX_ENTRIES_PER_GROUP}: {source!r}")

    return Rule(groups=tuple(groups), source=source)


def _split_entries(body: str, source: str) -> list[str]:
    entries = [part.strip() for part in body.split(",")]
    out = [e for e in entries if e]
    for entry in out:
        if "{" in entry or "}" in entry or "[" in entry or "]" in entry:
            raise RuleSyntaxError(f"nested bracket in rule: {source!r}")
    return out


def match_any(rules: Iterable[Rule], text: str) -> Rule | None:
    """First rule that matches ``text``, or ``None``."""
    for rule in rules:
        if rule.matches(text):
            return rule
    return None


@dataclass(frozen=True)
class NameRecord:
    """One 144-byte slot from a legacy name list.

    Only two fields are attested: the text at offset 0 and the in-use flag at
    ``0x84``. The other 139 bytes are copied verbatim by the original and never
    interpreted by any code path we could find, so they are preserved as
    opaque bytes in :attr:`raw` rather than given invented meanings.

    ``raw`` is the whole 144-byte slot. :attr:`text` and :attr:`in_use` are
    views onto it; constructing a record from just a text and a flag builds a
    zero-filled slot around them.
    """

    text: str
    in_use: bool
    raw: bytes | None = None

    def __post_init__(self) -> None:
        encoded = self.text.encode("gbk", errors="replace")
        if len(encoded) >= TEXT_FIELD_SIZE:
            raise ValueError(
                f"text needs {len(encoded)} bytes, field is {TEXT_FIELD_SIZE}")
        if self.raw is not None and len(self.raw) != RECORD_SIZE:
            raise ValueError(
                f"raw must be {RECORD_SIZE} bytes, got {len(self.raw)}")

    @property
    def rule(self) -> Rule:
        """The text interpreted as a rule (relevant for the keyword list)."""
        return parse_rule(self.text)

    def to_bytes(self) -> bytes:
        """Encode to the legacy 144-byte form.

        When ``raw`` is present the text and flag are patched into a copy of
        it, so every opaque byte survives a decode/encode round trip.
        """
        out = bytearray(self.raw if self.raw is not None else bytes(RECORD_SIZE))
        field = self.text.encode("gbk", errors="replace")
        # Overwrite only the text field (padding with NULs up to its end); every
        # byte outside it is opaque and must survive untouched.
        out[RECORD_TEXT_OFFSET:TEXT_FIELD_END] = (
            field + b"\x00" * (TEXT_FIELD_SIZE - len(field)))
        struct.pack_into("<I", out, RECORD_FLAG_OFFSET, 1 if self.in_use else 0)
        return bytes(out)


def decode_record(blob: bytes) -> NameRecord:
    """Decode exactly :data:`RECORD_SIZE` bytes into a :class:`NameRecord`."""
    if len(blob) != RECORD_SIZE:
        raise ValueError(f"expected {RECORD_SIZE} bytes, got {len(blob)}")
    field = blob[RECORD_TEXT_OFFSET:TEXT_FIELD_END]
    nul = field.find(b"\x00")
    raw_text = field if nul < 0 else field[:nul]
    text = raw_text.decode("gbk", errors="replace")
    flag = struct.unpack_from("<I", blob, RECORD_FLAG_OFFSET)[0]
    return NameRecord(text=text, in_use=flag == 1, raw=blob)


def encode_record(record: NameRecord) -> bytes:
    """Encode back to the legacy 144-byte form (round-trip partner)."""
    return record.to_bytes()


def iter_records(blob: bytes) -> Iterator[NameRecord]:
    """Decode a concatenated array of records, skipping out-of-use slots."""
    if len(blob) % RECORD_SIZE:
        raise ValueError(
            f"blob length {len(blob)} is not a multiple of {RECORD_SIZE}")
    for off in range(0, len(blob), RECORD_SIZE):
        record = decode_record(blob[off:off + RECORD_SIZE])
        if record.in_use:
            yield record
