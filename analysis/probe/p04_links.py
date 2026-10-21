#!/usr/bin/env python3
"""P04 - Cross-module dependency edges, derived from import tables only.

An edge A -> B exists iff A's import table names B AND B exports the symbol.
Optionally records the export RVA on the B side and the import IAT slot on A.

This is a machine fact: no interpretation, no disassembly. It is the backbone
of 06-LINKS.tsv; indirect edges (registry, file, COM) must be added by hand
with their own evidence.

Usage: python ANALYSIS/probe/p04_links.py > ANALYSIS/06-LINKS.tsv
"""
import os
import sys

import pefile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TREE = os.path.join(ROOT, "LH-installed-tree")


def load():
    mods = {}
    for dirpath, _dn, filenames in os.walk(TREE):
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            try:
                pe = pefile.PE(p, fast_load=True)
            except Exception:
                continue
            mods[fn.lower()] = (p, pe)
    return mods


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    mods = load()
    # module name -> {export_name: rva}
    exports = {}
    imports = {}
    for name, (p, pe) in mods.items():
        pe.parse_data_directories(directories=[
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"],
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
        ])
        ex = {}
        d = getattr(pe, "DIRECTORY_ENTRY_EXPORT", None)
        if d and d.symbols:
            for s in d.symbols:
                if s.name:
                    ex[s.name.decode("latin1")] = s.address
        exports[name] = ex
        im = []
        for e in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
            dll = e.dll.decode("latin1").lower()
            for i in e.imports:
                if i.name:
                    im.append((dll, i.name.decode("latin1"), i.address))
        imports[name] = im

    out = sys.stdout
    out.write("from\tto\tsymbol\tto_export_rva\tfrom_iat_rva\tkind\tmethod\tstatus\n")
    rows = 0
    for name in sorted(imports):
        for dll, fn, iat in imports[name]:
            if dll not in mods:
                continue  # system DLL, not our scope
            exp = exports.get(dll, {})
            if fn in exp:
                out.write(f"{name}\t{dll}\t{fn}\t{exp[fn]:#08x}\t{iat:#08x}\t"
                          f"import-table\tPE-HDR\tCLOSED\n")
                rows += 1
            else:
                out.write(f"{name}\t{dll}\t{fn}\t-\t{iat:#08x}\t"
                          f"import-table-ORDINAL-OR-RENAME\tPE-HDR\tPROBING\n")
                rows += 1
    for _n, (_p, pe) in mods.items():
        pe.close()
    sys.stderr.write(f"{rows} edges\n")


if __name__ == "__main__":
    main()
