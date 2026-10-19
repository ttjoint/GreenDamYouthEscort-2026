#!/usr/bin/env python3
"""Report what the advisory policy layer would suggest, without printing words.

The tool never echoes matched text; it only reports counts per action and the
list of evidence kinds that stayed unmapped.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from green_dam.policy import PolicyEngine, default_policy_table  # noqa: E402
from green_dam.wordlists import WordlistIndex  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path,
                    default=ROOT / "data" / "external-wordlists.json")
    ap.add_argument("--legacy", type=Path,
                    default=ROOT / "data" / "legacy-imported")
    ap.add_argument("--text-file", type=Path)
    args = ap.parse_args()

    index = WordlistIndex.from_external_json(args.input)
    table = default_policy_table(args.legacy)
    engine = PolicyEngine(words=index, policies=table)
    print(f"words indexed: {index.size}")
    print(f"trust domains loaded: {len(table.trust_domains)}")
    print(f"level actions configured: {len(table.level_actions)}")
    print(f"category actions configured: {len(table.category_actions)}")

    if args.text_file is not None:
        text = args.text_file.read_text(encoding="utf-8", errors="replace")
        verdict = engine.verdict(text)
        kinds = Counter(f.kind for f in verdict.findings)
        reasons = Counter(f.reason for f in verdict.findings)
        print(f"findings: {len(verdict.findings)}")
        print(f"kinds: {dict(sorted(kinds.items()))}")
        print(f"reasons: {dict(sorted(reasons.items()))}")
        print(f"verdict action: {verdict.action} ({verdict.reason})")


if __name__ == "__main__":
    main()
