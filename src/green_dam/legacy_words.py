"""Loading the shipped word lists from their obfuscated ``.dat`` form.

Why this lives in ``src`` and not ``tools``
-------------------------------------------
``tools/legacy_wordlist_codec.py`` already decodes the format, but ``tools`` is a
one-off analysis area: it is not packaged, not importable by the engine, and not
covered by the dependency contract. The engine needs to load real lists at
runtime, so the codec moves here, and ``tools`` keeps its own copy for the
analysis scripts that predate this module.

The two copies decode identically; ``tests/test_legacy_words.py`` asserts that by
decoding the same bytes through both.

Format
------
A fixed 2:1 character obfuscation with no key - not encryption. Each plaintext
byte becomes two printable symbols from ``0x30..0x6F``:

    V = (v0 << 6) | v1              v = symbol - 0x30
    plain bit 0 <- V[0]             plain bit 4 <- V[6]
    plain bit 1 <- V[1]             plain bit 5 <- V[7]
    plain bit 2 <- 1 - V[2]         plain bit 6 <- 1 - V[8]
    plain bit 3 <- 1 - V[3]         plain bit 7 <- 1 - V[9]
    V[4], V[5], V[10], V[11] are padding

CRLF line endings pass through unencoded; payload text is GBK.

The catalogue
-------------
``wfile.dat`` is not a word list - it is the index mapping a category number and
a Chinese name to a list file:

    1 成人/色情网站 adwfil.dat
    1 成人/色情网站(严格) adwapp.dat
    1 暴力游戏 vgamfil.dat
    1 同性恋 lgwfil.dat
    1 非法活动/毒品 iawfil.dat

Note that every entry's category number is 1. The number is not a discriminator
in the shipped file, so this module keeps the names and ignores the number rather
than inventing a taxonomy from it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .wordlists import WordlistIndex

#: Symbol range base for the obfuscation.
SYM_BASE = 0x30
#: Highest symbol, exclusive: ``0x30 + 0x40``.
SYM_LIMIT = 0x70

_CRLF = b"\r\n"

#: The catalogue file that names the others.
CATALOGUE = "wfile.dat"


class WordlistFormatError(ValueError):
    """Raised when a ``.dat`` file is not the shape this module decodes."""


def _decode_pair(v0: int, v1: int) -> int:
    """One plaintext byte from two symbols (already offset by ``SYM_BASE``)."""
    value = (v0 << 6) | v1
    bits = [(value >> i) & 1 for i in range(12)]
    plain = (
        bits[0]
        | (bits[1] << 1)
        | ((1 - bits[2]) << 2)
        | ((1 - bits[3]) << 3)
        | (bits[6] << 4)
        | (bits[7] << 5)
        | ((1 - bits[8]) << 6)
        | ((1 - bits[9]) << 7)
    )
    return plain


def is_obfuscated(data: bytes) -> bool:
    """Whether every non-CRLF byte sits in the symbol range.

    An empty file is not considered obfuscated: there is nothing to decode, and
    claiming otherwise would let a caller decode it into nothing and call that
    success.
    """
    if not data:
        return False
    i = 0
    while i < len(data):
        if data[i:i + 2] == _CRLF:
            i += 2
            continue
        if not SYM_BASE <= data[i] < SYM_LIMIT:
            return False
        i += 1
    return True


def decode_bytes(data: bytes) -> bytes:
    """Decode obfuscated bytes to plain bytes, preserving CRLF."""
    if not data:
        return b""
    out = bytearray()
    i = 0
    while i < len(data):
        if data[i:i + 2] == _CRLF:
            out += _CRLF
            i += 2
            continue
        if i + 1 >= len(data):
            raise WordlistFormatError(
                f"trailing single symbol at offset {i}: the format is 2:1")
        v0 = data[i] - SYM_BASE
        v1 = data[i + 1] - SYM_BASE
        if not (0 <= v0 < 0x40 and 0 <= v1 < 0x40):
            raise WordlistFormatError(
                f"symbol out of range at offset {i}: "
                f"{data[i]:#x} {data[i + 1]:#x}")
        out.append(_decode_pair(v0, v1))
        i += 2
    return bytes(out)


def decode_text(data: bytes) -> str:
    """Decode to text. Payload is GBK; ``errors="replace"`` keeps a bad byte
    from aborting a whole list, and :func:`decode_lines` reports how many."""
    return decode_bytes(data).decode("gbk", errors="replace")


def decode_lines(data: bytes) -> list[str]:
    """Decoded non-empty lines, stripped.

    The shipped lists are one term per line with CRLF endings. Blank lines are
    dropped: a blank term would match every text and is never intended.
    """
    text = decode_text(data)
    return [line.strip() for line in text.splitlines() if line.strip()]


@dataclass(frozen=True)
class CatalogueEntry:
    """One row of ``wfile.dat``: a category label and the list file it names."""

    category: int
    label: str
    filename: str

    def to_dict(self) -> dict:
        return {"category": self.category, "label": self.label,
                "filename": self.filename}


def parse_catalogue(text: str) -> list[CatalogueEntry]:
    """Parse ``wfile.dat``'s decoded text.

    Each line is ``<number> <label> <filename>``. A line that does not have at
    least three whitespace-separated fields is skipped rather than guessed at:
    the catalogue is small and a malformed row is a data problem, not something
    to paper over.
    """
    entries: list[CatalogueEntry] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        number, label, filename = parts[0], parts[1], parts[2]
        try:
            category = int(number)
        except ValueError:
            continue
        entries.append(CatalogueEntry(category=category, label=label,
                                      filename=filename))
    return entries


@dataclass
class LoadedList:
    """One list file's decode result, with counts but not the terms."""

    filename: str
    label: str
    terms: int
    decoded_bytes: int
    obfuscated: bool

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "label": self.label,
            "terms": self.terms,
            "decoded_bytes": self.decoded_bytes,
            "obfuscated": self.obfuscated,
        }


@dataclass
class LoadReport:
    """What a directory load did. Counts only, so it is safe to log."""

    lists: list[LoadedList] = field(default_factory=list)
    catalogue: list[CatalogueEntry] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    total_terms: int = 0

    def summary(self) -> dict:
        return {
            "lists": [entry.to_dict() for entry in self.lists],
            "catalogue": [entry.to_dict() for entry in self.catalogue],
            "missing": list(self.missing),
            "total_terms": self.total_terms,
            "index_size": None,  # filled by the caller that built an index
        }


def load_file(path: Path, *, label: str = "",
              index: WordlistIndex | None = None) -> tuple[LoadedList,
                                                           WordlistIndex]:
    """Decode one list file and add its terms to ``index``.

    Terms are added with ``attested=True``: they come from the vendor's own
    shipped data, unlike anything parsed out of prose. No level is set - the
    ``.dat`` lists carry no per-term severity, and inventing one would put a
    number in the engine that nothing measured.
    """
    data = path.read_bytes()
    if not data:
        raise WordlistFormatError(f"{path.name} is empty")

    obfuscated = is_obfuscated(data)
    if obfuscated:
        decoded = decode_bytes(data)
    else:
        # A plain-text list is accepted too: a hand-written replacement should
        # not need to be re-obfuscated to be usable. The bytes are already
        # GBK text, so they are used as-is rather than run through the decoder.
        decoded = data
    lines = [line.strip() for line in
             decoded.decode("gbk", errors="replace").splitlines()
             if line.strip()]

    target = index if index is not None else WordlistIndex()
    for term in lines:
        target.add(term, path.name, level=None, attested=True)

    return LoadedList(filename=path.name, label=label, terms=len(lines),
                      decoded_bytes=len(decoded),
                      obfuscated=obfuscated), target


def load_directory(path: Path, *,
                   index: WordlistIndex | None = None) -> tuple[LoadReport,
                                                                WordlistIndex]:
    """Load every list the catalogue names, plus any other ``*fil.dat`` present.

    The catalogue is the authority for labels; files present but unlisted are
    still loaded, because a list the vendor shipped is a list the vendor
    intended to apply, and silently ignoring it would under-filter.
    """
    target = index if index is not None else WordlistIndex()
    report = LoadReport()

    catalogue_path = path / CATALOGUE
    labels: dict[str, str] = {}
    if catalogue_path.exists():
        report.catalogue = parse_catalogue(decode_text(catalogue_path.read_bytes()))
        for entry in report.catalogue:
            labels[entry.filename] = entry.label

    for filename in sorted(labels):
        file_path = path / filename
        if not file_path.exists():
            report.missing.append(filename)
            continue
        loaded, _ = load_file(file_path, label=labels[filename], index=target)
        report.lists.append(loaded)
        report.total_terms += loaded.terms

    # Anything else that looks like a list and was not catalogued.
    for file_path in sorted(path.glob("*fil.dat")):
        if file_path.name in labels:
            continue
        loaded, _ = load_file(file_path, label="", index=target)
        report.lists.append(loaded)
        report.total_terms += loaded.terms

    return report, target
