#!/usr/bin/env python3
"""P03 - Full export and import tables for every PE in the install tree.

Emits two sections:
  ## EXPORTS  module<TAB>ordinal<TAB>rva<TAB>name
  ## IMPORTS  module<TAB>dll<TAB>function

This is the source for 05-INTERFACES.tsv and for every cross-module edge in
06-LINKS.tsv. Nothing here is interpreted; names and RVA only.

Usage: python ANALYSIS/probe/p03_exports.py > ANALYSIS/05-INTERFACES-RAW.txt
"""
import os
import sys

import pefile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TREE = os.path.join(ROOT, "LH-installed-tree")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out = sys.stdout
    peps = []
    for dirpath, _dn, filenames in os.walk(TREE):
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            try:
                pe = pefile.PE(p, fast_load=True)
            except Exception:
                continue
            if pe.FILE_HEADER.Machine == pefile.MACHINE_TYPE["IMAGE_FILE_MACHINE_I386"]:
                peps.append((os.path.relpath(p, ROOT).replace("\\", "/"), p, pe))
            else:
                pe.close()
    peps.sort(key=lambda t: t[0])

    out.write("## EXPORTS\tmodule\tordinal\trva\tname\n")
    for rel, p, pe in peps:
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]]
        )
        d = getattr(pe, "DIRECTORY_ENTRY_EXPORT", None)
        if not d:
            continue
        short = rel.split("/")[-1]
        for s in d.symbols:
            nm = s.name.decode("latin1") if s.name else "(ordinal-only)"
            out.write(f"EXP\t{short}\t{s.ordinal}\t{s.address:#08x}\t{nm}\n")

    out.write("## IMPORTS\tmodule\tdll\tfunction\tiat_rva\n")
    for rel, p, pe in peps:
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]]
        )
        short = rel.split("/")[-1]
        for e in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
            dll = e.dll.decode("latin1")
            for imp in e.imports:
                nm = imp.name.decode("latin1") if imp.name else f"(ord {imp.ordinal})"
                out.write(f"IMP\t{short}\t{dll}\t{nm}\t{imp.address:#08x}\n")

    for _rel, _p, pe in peps:
        pe.close()


if __name__ == "__main__":
    main()
