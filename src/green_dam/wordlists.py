"""Unified query layer for imported legacy and external wordlists.

This module deliberately does not turn every trie occurrence into a block.
It returns provenance and levels so callers can choose policy explicitly.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class WordHit:
    word: str
    sources: tuple[str, ...]
    levels: tuple[int, ...]
    raw_forms: tuple[str, ...]
    attested: bool = False

    @property
    def max_level(self) -> int | None:
        return max(self.levels) if self.levels else None


class WordlistIndex:
    """Exact and longest-match lookup over imported wordlists."""

    def __init__(self) -> None:
        self._items: dict[str, dict[str, set]] = {}

    def add(self, word: str, source: str, *, level: int | None = None,
            raw: str | None = None, attested: bool = False) -> None:
        word = word.strip()
        if not word:
            return
        item = self._items.setdefault(word, {
            "sources": set(), "levels": set(), "raw": set(),
            "attested": False,
        })
        item["sources"].add(source)
        item["raw"].add(raw if raw is not None else word)
        if level is not None:
            item["levels"].add(level)
        item["attested"] |= attested

    def get(self, word: str) -> WordHit | None:
        item = self._items.get(word)
        if item is None:
            return None
        return WordHit(
            word=word,
            sources=tuple(sorted(item["sources"])),
            levels=tuple(sorted(item["levels"])),
            raw_forms=tuple(sorted(item["raw"])),
            attested=item["attested"],
        )

    def longest_at(self, text: str, start: int = 0) -> WordHit | None:
        """Return the longest exact entry beginning at *start*."""
        best = None
        for end in range(start + 1, len(text) + 1):
            hit = self.get(text[start:end])
            if hit is not None:
                best = hit
        return best

    def scan(self, text: str) -> list[tuple[int, WordHit]]:
        """Scan left-to-right using longest match at every position."""
        out = []
        i = 0
        while i < len(text):
            hit = self.longest_at(text, i)
            if hit is None:
                i += 1
            else:
                out.append((i, hit))
                i += len(hit.word)
        return out

    @classmethod
    def from_external_json(cls, path: Path) -> "WordlistIndex":
        obj = json.loads(path.read_text(encoding="utf-8"))
        index = cls()
        for file_obj in obj["files"]:
            source = file_obj["file"]
            for entry in file_obj["entries"]:
                index.add(entry["word"], source, level=entry["level"],
                          raw=entry["raw"], attested=False)
        return index

    @property
    def size(self) -> int:
        return len(self._items)

    def source_counts(self) -> dict[str, int]:
        """Number of unique words contributed by each source."""
        counts: dict[str, int] = {}
        for item in self._items.values():
            for source in item["sources"]:
                counts[source] = counts.get(source, 0) + 1
        return dict(sorted(counts.items()))

    def level_conflicts(self) -> list[WordHit]:
        """Words whose explicitly assigned numeric levels disagree."""
        out = []
        for word in sorted(self._items):
            item = self._items[word]
            if len(item["levels"]) > 1:
                hit = self.get(word)
                assert hit is not None
                out.append(hit)
        return out

    def duplicate_sources(self) -> list[WordHit]:
        """Words occurring in at least two input files."""
        out = []
        for word in sorted(self._items):
            item = self._items[word]
            if len(item["sources"]) > 1:
                hit = self.get(word)
                assert hit is not None
                out.append(hit)
        return out
