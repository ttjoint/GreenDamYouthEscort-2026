"""Word packs: the word lists as installable, switchable data.

The original product shipped its lists inside the binary and offered no way to
see what was in them or turn one off. That is the single most criticised part of
it, and the criticism is not about which words are there - it is that a parent
could not find out, could not disable a list, and could not add their own.

This module makes the lists data:

**A pack is a file.** One JSON document with a name, a category, a licence, and
the terms. Importing is copying the file into the data directory. There is no
compile step and no format the parent cannot read.

**Every pack is switchable.** A pack that is present but disabled contributes
nothing. This is the difference between "we shipped a list you cannot remove" and
"we shipped a list you can turn off", and it is the whole point.

**Packs are enumerable and exportable.** A parent can ask what is installed, how
many terms each holds, and dump one to a file to inspect or share it.

Why the shipped political list is *not* in the deployment
---------------------------------------------------------
``docs/corpus-eval.md`` §4.1 measured the legacy list against two corpora:

    ordinary novels   96.12% of files hit
    adult corpus      97.28% of files hit

A filter that fires on 96% of ordinary text and 97% of the text it is supposed to
catch is not filtering. That list was built for a different purpose, and shipping
it inside a product sold as a parental control would be both ineffective and a
statement this project has no business making on a parent's behalf.

So it is available as a pack - importable, inspectable, switchable - and not as a
default. The default is empty. A parent who wants a list adds one.

Pack format
-----------
::

    {
      "name": "示例词库",
      "id": "example",
      "category": "adult",
      "licence": "CC0-1.0",
      "source": "hand-written for testing",
      "terms": ["甲", "乙"]
    }

``id`` must match ``[a-z0-9][a-z0-9_-]*``: it becomes a filename, and a pack whose
id contains ``..`` or a slash would write outside the pack directory. The id is
validated on import, not on load, because a hostile pack is the one case worth
refusing loudly.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterable
from pathlib import Path

__all__ = [
    "PACK_SUFFIX",
    "PACK_DIR",
    "CATEGORIES",
    "TERM_LIMIT",
    "Pack",
    "PackError",
    "PackIndex",
    "parse_pack",
    "parse_payload",
    "validate_id",
]

PACK_SUFFIX = ".wordpack.json"

#: Directory under the machine-wide data tree where packs live.
PACK_DIR = "wordpacks"

#: A pack larger than this is almost certainly not a word list. 200,000 terms is
#: an order of magnitude above the largest legacy list (9,048).
TERM_LIMIT = 200_000

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

#: Categories a pack may declare. Descriptive only - nothing branches on them -
#: but a closed set keeps the UI from having to render arbitrary strings, and it
#: makes "what kinds of lists exist" answerable without reading every pack.
CATEGORIES = ("adult", "violence", "gambling", "drugs", "custom", "other")


class PackError(ValueError):
    """A pack is malformed, unsafe, or cannot be installed."""


@dataclass(frozen=True)
class Pack:
    """One word list, as loaded from disk."""

    id: str
    name: str
    category: str
    licence: str
    source: str
    terms: tuple[str, ...] = ()
    #: Whether the pack contributes to filtering. A disabled pack is still
    #: listed and still inspectable - disabling is not hiding.
    enabled: bool = True
    #: Set when the file exists but could not be parsed. Such a pack is listed
    #: with zero terms and this explanation, never silently dropped: a parent who
    #: imported a file needs to know it did not take effect.
    error: str = ""

    @property
    def usable(self) -> bool:
        return self.enabled and not self.error

    def to_dict(self) -> dict[str, object]:
        """Summary only. Terms are counted, never listed.

        The engine's ``status`` has the same rule: reporting a count is fine,
        reporting the terms turns the status output into a copy of the list.
        """
        return {
            "id": self.id, "name": self.name, "category": self.category,
            "licence": self.licence, "source": self.source,
            "terms": len(self.terms), "enabled": self.enabled,
            "usable": self.usable, "error": self.error,
        }


def validate_id(pack_id: str) -> str:
    """Check a pack id is safe to use as a filename, and return it."""
    if not _ID.match(pack_id):
        raise PackError(
            f"pack id {pack_id!r} is not allowed. An id must be lowercase "
            f"letters, digits, '_' or '-', start with a letter or digit, and be "
            f"at most 64 characters. The id becomes a filename, so anything else "
            f"could write outside the pack directory.")
    return pack_id


def parse_payload(payload: object, *, fallback_id: str = "") -> Pack:
    """Turn a decoded JSON document into a Pack. Never raises.

    Pure: takes a decoded object, returns a Pack. Reading the file is the
    caller's job, because the engine performs no I/O (A5). A malformed document
    comes back as a Pack with ``error`` set rather than an exception, so a
    directory full of packs where one is corrupt still lists the others.
    """
    if not isinstance(payload, dict):
        return Pack(id=fallback_id, name=fallback_id, category="other",
                    licence="", source="", error="the document is not an object")

    pack_id = str(payload.get("id") or fallback_id)
    try:
        validate_id(pack_id)
    except PackError as exc:
        return Pack(id=fallback_id or "invalid", name=str(payload.get("name")
                    or fallback_id or "invalid"), category="other",
                    licence="", source="", error=str(exc))

    raw_terms = payload.get("terms")
    if not isinstance(raw_terms, list):
        return Pack(id=pack_id, name=str(payload.get("name") or pack_id),
                    category="other", licence="", source="",
                    error="'terms' must be a list")

    terms: list[str] = []
    seen: set[str] = set()
    for item in raw_terms:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        terms.append(text)
    if len(terms) > TERM_LIMIT:
        return Pack(id=pack_id, name=str(payload.get("name") or pack_id),
                    category="other", licence="", source="",
                    error=f"{len(terms)} terms exceeds the {TERM_LIMIT} limit")

    category = str(payload.get("category") or "other").lower()
    if category not in CATEGORIES:
        category = "other"

    return Pack(
        id=pack_id,
        name=str(payload.get("name") or pack_id),
        category=category,
        licence=str(payload.get("licence") or ""),
        source=str(payload.get("source") or ""),
        terms=tuple(terms),
        enabled=bool(payload.get("enabled", True)),
    )


def parse_pack(text: str, *, fallback_id: str = "") -> Pack:
    """Parse a pack from its JSON text. Never raises; ``error`` carries the why."""
    try:
        payload = json.loads(text)
    except ValueError as exc:
        return Pack(id=fallback_id, name=fallback_id, category="other",
                    licence="", source="", error=f"not valid JSON: {exc}")
    return parse_payload(payload, fallback_id=fallback_id)


def index_of(packs: Iterable[Pack]) -> PackIndex:
    """Build a PackIndex from already-loaded packs."""
    packs = tuple(sorted(packs, key=lambda pack: pack.id))
    warnings = tuple(f"{p.id}: {p.error}" for p in packs if p.error)
    return PackIndex(packs=packs, warnings=warnings)


@dataclass
class PackIndex:
    """The loaded set, with the enable/disable state applied."""

    packs: tuple[Pack, ...] = ()
    directory: Path | None = None
    #: Warnings from loading, e.g. a pack file that could not be parsed.
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def get(self, pack_id: str) -> Pack | None:
        for pack in self.packs:
            if pack.id == pack_id:
                return pack
        return None

    @property
    def usable(self) -> tuple[Pack, ...]:
        """Packs that actually contribute terms."""
        return tuple(p for p in self.packs if p.usable)

    @property
    def terms(self) -> tuple[str, ...]:
        """Every term from every usable pack, de-duplicated, order preserved."""
        seen: set[str] = set()
        out: list[str] = []
        for pack in self.usable:
            for term in pack.terms:
                if term in seen:
                    continue
                seen.add(term)
                out.append(term)
        return tuple(out)

    @property
    def total_terms(self) -> int:
        return len(self.terms)

    def summary(self) -> dict[str, object]:
        return {
            "packs": [p.to_dict() for p in self.packs],
            "enabled": sum(1 for p in self.packs if p.usable),
            "total_terms": self.total_terms,
            "warnings": list(self.warnings),
        }
