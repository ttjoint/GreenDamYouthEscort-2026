#!/usr/bin/env python3
"""Audit the decoded legacy category rules without changing their semantics."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    modes = Counter()
    terms = 0
    raw = 0
    malformed = []
    for rule in data["rules"]:
        if "terms" not in rule:
            raw += 1
            malformed.append(rule.get("raw", ""))
            continue
        terms += len(rule["terms"])
        modes.update(t["match"] for t in rule["terms"])
    unique = set()
    for rule in data["rules"]:
        for term in rule.get("terms", []):
            unique.add((term["text"], term["match"]))
    return {
        "source": data.get("source", path.name),
        "rules": data["count"],
        "expanded_terms": terms,
        "unique_terms": len(unique),
        "modes": dict(sorted(modes.items())),
        "raw_rules": raw,
        "raw_examples": malformed[:20],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", type=Path, default=ROOT / "data" / "legacy-imported" / "rules")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    reports = [audit(p) for p in sorted(args.rules.glob("*.json"))]
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    if args.json:
        args.json.write_text(json.dumps(reports, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
