#!/usr/bin/env python3
"""Report overlap and explicit-level conflicts in imported wordlists."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from green_dam.wordlists import WordlistIndex  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path,
                    default=ROOT / "data" / "external-wordlists.json")
    ap.add_argument("--examples", type=int, default=20)
    args = ap.parse_args()
    index = WordlistIndex.from_external_json(args.input)
    duplicates = index.duplicate_sources()
    conflicts = index.level_conflicts()
    print("unique words:", index.size)
    print("source counts:")
    for source, count in index.source_counts().items():
        print(f"  {source}: {count}")
    print("duplicate-source words:", len(duplicates))
    print("level conflicts:", len(conflicts))
    if conflicts:
        print("conflict examples:")
        for hit in conflicts[:args.examples]:
            print(f"  {hit.word!r}: levels={hit.levels}, sources={hit.sources}")
    if duplicates:
        print("duplicate examples:")
        for hit in duplicates[:args.examples]:
            print(f"  {hit.word!r}: sources={hit.sources}, levels={hit.levels}")


if __name__ == "__main__":
    main()
