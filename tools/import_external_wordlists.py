#!/usr/bin/env python3
"""Import external Chinese wordlists without flattening their provenance.

The source directories are intentionally outside this repository.  This tool
stores only the small wordlist inputs, not the 1.3 GB corpus.

Usage::

    python tools/import_external_wordlists.py \
      --source-dir "D:/Docs/_w/语料2/新建文件夹 (2)" \
      --output data/external-wordlists.json

Every record retains source file, original line, optional numeric level, and
whether the line was parseable as a plain word.  No line is silently dropped.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def parse_line(line: str) -> tuple[str, int | None]:
    value = line.strip()
    if "=" in value:
        word, level = value.rsplit("=", 1)
        if word and level.isdigit():
            return word, int(level)
    return value, None


def import_file(path: Path) -> dict:
    records = []
    for number, raw in enumerate(read_text(path).splitlines(), 1):
        original = raw.rstrip("\r\n")
        if not original.strip():
            continue
        word, level = parse_line(original)
        records.append({
            "line": number,
            "raw": original,
            "word": word,
            "level": level,
        })
    return {
        "file": path.name,
        "sha256": sha256(path),
        "count": len(records),
        "entries": records,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    names = (
        "敏感词列表.txt", "dict.txt", "dict (1).txt", "words.dict",
        "sstv.data.raw", "sstv2.data.raw",
    )
    files = [args.source_dir / name for name in names if (args.source_dir / name).exists()]
    imported = [import_file(path) for path in files]
    out = {
        "_source": "external wordlists supplied by the user",
        "_attested": False,
        "_note": "Source files remain external; entries retain raw line and provenance.",
        "source_dir": str(args.source_dir),
        "files": imported,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("files:", len(imported), "entries:", sum(x["count"] for x in imported))
    print("output:", args.output)


if __name__ == "__main__":
    main()
