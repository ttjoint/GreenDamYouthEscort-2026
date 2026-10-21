#!/usr/bin/env python3
"""
Import legacy assets into the new project's data format.

This is a one-shot migration helper. The legacy install tree is *not*
shipped with the new project; point ``--legacy`` at a directory that
contains the unpacked 3.17 tree, run this script once, and commit the
resulting ``data/`` output.

Usage::

    python tools/import_legacy.py --legacy /path/to/LH-installed-tree
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import falunword
import legacy_rules
from green_dam import sysdat
import legacy_wordlist_codec as codec

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_obfuscated(path: Path) -> str:
    raw = path.read_bytes()
    if not codec.is_obfuscated(raw):
        return raw.decode("gbk", errors="replace")
    return codec.decode_text(raw)


def import_policy(legacy: Path) -> dict:
    """
    sys.dat / xconfigs.dat: 67 little-endian uint32 policy slots.

    Slot names come from :mod:`green_dam.sysdat`, which carries the name table
    read out of ``XNet2.exe`` (``0x68dbc``-``0x68f78``) and the
    ``index = slot_no - 1`` mapping proven by the ``rep movsd`` at ``0x40bc16``.

    A previously exported anchor ``17 -> allow_auto_shutdown`` was **wrong**:
    it came from the separate log-event numbering. Policy 17 is
    ``allow_auto_screenshot``. Only the first 25 slots are named; the rest are
    reserved and exported raw.
    """
    sysdir = legacy / "Windows" / "System32"
    blob = (sysdir / "sys.dat").read_bytes()
    slots = list(struct.unpack("<%dI" % (len(blob) // 4), blob))

    ex = (sysdir / "sysEx.dat").read_bytes()
    ex_slots = list(struct.unpack("<%dI" % (len(ex) // 4), ex))

    names = {str(i + 1): name for i, name in enumerate(sysdat.SLOT_NAMES)}
    return {
        "source": "sys.dat",
        "note": "Legacy 67-slot policy array. Policy numbering is 1-based: "
                "index = slot_no - 1 (proven at XNet2.exe 0x40bc16). Only "
                "slots 1..25 are named; 26..67 are reserved and always zero.",
        "slot_count": len(slots),
        "slots": slots,
        "known_slots": names,
        "preset_level_6": {str(k): v for k, v in sysdat.PRESET_LEVEL_6.items()},
        "sysEx": {"slot_count": len(ex_slots), "slots": ex_slots},
        "sha256": sha256(sysdir / "sys.dat"),
    }


def import_categories(legacy: Path) -> dict:
    sysdir = legacy / "Windows" / "System32"
    idx = legacy_rules.read_category_index(
        DATA / "_staging" / legacy_rules.CATEGORY_INDEX
    )
    return {"categories": idx}


def import_trust_urls(legacy: Path) -> dict:
    sysdir = legacy / "Windows" / "System32"
    text = read_obfuscated(sysdir / "TrustUrl.dat")
    entries = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return {
        "source": "TrustUrl.dat",
        "note": "Built-in trusted-domain list shipped with 3.17.",
        "count": len(entries),
        "entries": entries,
        "sha256": sha256(sysdir / "TrustUrl.dat"),
    }


def import_im_targets(legacy: Path) -> dict:
    """
    kwselectinfopp.dll: not a PE image, just a packed record table.

    8 records x 84 bytes:
      +0x00  uint32  always 0
      +0x04  char[30] process name (ASCII)
      +0x22  char[34] display name (GBK)
      +0x44  byte[16] reserved, all zero
    """
    path = legacy / "Windows" / "kwselectinfopp.dll"
    blob = path.read_bytes()
    rec = 84
    if len(blob) % rec:
        raise ValueError(f"{path}: size {len(blob)} not a multiple of {rec}")

    out = []
    for i in range(len(blob) // rec):
        r = blob[i * rec : (i + 1) * rec]
        proc = r[4:0x22].split(b"\0", 1)[0].decode("ascii", errors="replace")
        name = r[0x22:0x44].split(b"\0", 1)[0].decode("gbk", errors="replace")
        out.append({"process": proc, "display_name": name})

    return {
        "source": "kwselectinfopp.dll",
        "note": "Monitored-application list. Treated as firewall target table "
        "in the new implementation, not as an interception allowlist.",
        "record_size": rec,
        "count": len(out),
        "targets": out,
        "sha256": sha256(path),
    }


def import_falun(legacy: Path) -> dict:
    """
    FalunWord.lib: HNC lexicon container.

    See ``tools/falunword.py`` for how the layout was recovered from the
    consumer side (``CFWordLib::LoadLib``). The container holds a
    character-chain trie plus an independent sense table and a UTF-16 pool.
    """
    path = legacy / "Windows" / "HNCLIB" / "FalunWord.lib"
    blob = path.read_bytes()
    lay = falunword.Layout(blob)
    ok = lay.verify()
    if not ok:
        raise SystemExit("FalunWord.lib layout did not close - refusing to import")

    terms = falunword.iter_chains(lay)
    senses = falunword.decode_sense_table(lay)

    words = [t["word"] for t in terms]
    with_sense = [t for t in terms if t["sense_id"] is not None]

    return {
        "source": "FalunWord.lib",
        "status": "decoded",
        "note": "Term list recovered from the character-chain trie. The `next` "
        "field is a character path, not a sibling list, so following it from a "
        "chain start spells a whole term. An independent sense table "
        "(offset+length into a UTF-16 pool) cross-checks the result.",
        "limitation": "This file does NOT carry the per-word normal/blocked "
        "judgement the runtime uses at [item+0x28]. That field's source, "
        "FMean.tmp, was never shipped; see docs/falunword-format.md. The "
        "hong_hei class below is real data and can seed a rebuilt mapping, "
        "but the old mapping itself cannot be recovered from this file.",
        "layout": {
            "file_size": len(blob),
            "chain_nodes": lay.n1,
            "sense_records": lay.n2,
            "pool_chars": lay.n3,
            "trailer": lay.trailer.decode("ascii", "replace"),
            "layout_verified": ok,
        },
        "term_count": len(words),
        "classified_terms": len(with_sense),
        "terms": words,
        # Only terms whose terminal node carries a sense id have a category;
        # mapping id -> hong_hei via the sense table gives a usable severity.
        "term_senses": [
            {"word": t["word"], "sense_id": t["sense_id"]} for t in with_sense
        ],
        "senses": senses,
        "sha256": sha256(path),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--legacy", required=True, type=Path)
    args = ap.parse_args()
    legacy: Path = args.legacy

    out = DATA / "legacy-imported"
    staging = DATA / "_staging"

    # --- wordlist-codec files -------------------------------------------
    sysdir = legacy / "Windows" / "System32"
    staging.mkdir(parents=True, exist_ok=True)
    for name in [
        "wfile.dat",
        "TrustUrl.dat",
        "adwfil.dat",
        "adwapp.dat",
        "iawfil.dat",
        "lgwfil.dat",
        "vgamfil.dat",
    ]:
        text = read_obfuscated(sysdir / name)
        (staging / (name + ".txt")).write_text(text, encoding="utf-8")

    # --- rule lists ------------------------------------------------------
    rules = {}
    for name in ["adwfil", "adwapp", "iawfil", "lgwfil", "vgamfil"]:
        src = staging / (name + ".dat.txt")
        rules[name] = legacy_rules.parse_file(src)
        legacy_rules.dump(rules[name], out / "rules" / (name + ".json"))

    # --- simple assets ---------------------------------------------------
    legacy_rules.dump(import_policy(legacy), out / "policy.json")
    legacy_rules.dump(import_trust_urls(legacy), out / "trust-urls.json")
    legacy_rules.dump(import_im_targets(legacy), out / "im-targets.json")

    idx = legacy_rules.read_category_index(staging / "wfile.dat.txt")
    legacy_rules.dump({"categories": idx}, out / "categories.json")

    falun = import_falun(legacy)
    legacy_rules.dump(falun, out / "falun-terms.json")

    # --- summary ---------------------------------------------------------
    summary = {
        "categories": len(idx),
        "trust_urls": len(json.loads((out / "trust-urls.json").read_text("utf-8"))["entries"]),
        "im_targets": len(json.loads((out / "im-targets.json").read_text("utf-8"))["targets"]),
        "rules": {k: v["count"] for k, v in rules.items()},
        "falun_terms": falun["term_count"],
        "falun_classified": falun["classified_terms"],
        "falun_status": falun["status"],
    }
    legacy_rules.dump(summary, out / "summary.json")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
