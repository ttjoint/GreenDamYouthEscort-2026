#!/usr/bin/env python3
"""P02 - PE module facts, straight from the PE header/tables.

Emits only mechanizable facts:
  path, machine, subsystem, dll_flags, timestamp, entry_rva, imagebase,
  sections, export_count, import_dll_count, import_func_count, version_company,
  version_product, version_desc

It deliberately does NOT assign a "responsibility". Responsibility claims must be
earned per-row in 02-MODULES.tsv with a method tag, not inferred here.

Usage: python ANALYSIS/probe/p02_modules.py > ANALYSIS/02-MODULES-RAW.tsv
"""
import os
import sys

import pefile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TREE = os.path.join(ROOT, "LH-installed-tree")

SUBSYSTEM = {
    1: "NATIVE", 2: "GUI", 3: "CUI", 9: "WINCE", 10: "EFI",
}


def version_strings(pe):
    """Return (company, product, desc) from VS_VERSIONINFO, or ('', '', '')."""
    out = {"CompanyName": "", "ProductName": "", "FileDescription": ""}
    try:
        if not hasattr(pe, "FileInfo") or not pe.FileInfo:
            return out["CompanyName"], out["ProductName"], out["FileDescription"]
        for fi_list in pe.FileInfo:
            entries = fi_list
            if hasattr(fi_list, "Key") and fi_list.Key == b"StringFileInfo":
                entries = [fi_list]
            for fi in entries:
                if not hasattr(fi, "StringTable"):
                    continue
                for st in fi.StringTable:
                    for k, v in st.entries.items():
                        key = k.decode("utf-8", "replace")
                        if key in out:
                            out[key] = v.decode("utf-8", "replace")
    except Exception:
        pass
    return out["CompanyName"], out["ProductName"], out["FileDescription"]


def safe(s):
    """Version strings in these binaries are UTF-8 bytes stored in a VS_VERSIONINFO
    that Windows would render as GBK. Decoding as UTF-8 is correct; GBK yields mojibake.
    Verified: CompanyName of gn.exe == '金惠科技---大正科技' under UTF-8, garbage under GBK."""
    if isinstance(s, bytes):
        try:
            s = s.decode("utf-8")
        except UnicodeDecodeError:
            s = s.decode("gbk", "replace")
    return "".join(ch if ch >= " " else " " for ch in s).replace("\t", " ")


def main():
    rows = []
    for dirpath, _dn, filenames in os.walk(TREE):
        for fn in filenames:
            rows.append(os.path.join(dirpath, fn))

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out = sys.stdout
    out.write("\t".join([
        "path", "machine", "subsystem", "is_dll", "entry_rva", "image_base",
        "sections", "export_count", "import_dlls", "import_funcs",
        "image_size", "company", "product", "description",
    ]) + "\n")

    for full in sorted(rows):
        rel = os.path.relpath(full, ROOT).replace("\\", "/")
        try:
            pe = pefile.PE(full, fast_load=True)
            pe.parse_data_directories(
                directories=[
                    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"],
                    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"],
                ]
            )
        except Exception:
            continue

        machine = pefile.MACHINE_TYPE.get(pe.FILE_HEADER.Machine, hex(pe.FILE_HEADER.Machine))
        subsystem = SUBSYSTEM.get(pe.OPTIONAL_HEADER.Subsystem, str(pe.OPTIONAL_HEADER.Subsystem))
        is_dll = "yes" if pe.is_dll() else "no"
        entry = pe.OPTIONAL_HEADER.AddressOfEntryPoint

        exp = 0
        if hasattr(pe, "DIRECTORY_ENTRY_EXPORT") and pe.DIRECTORY_ENTRY_EXPORT.symbols:
            exp = len(pe.DIRECTORY_ENTRY_EXPORT.symbols)

        imp_dlls = 0
        imp_funcs = 0
        if hasattr(pe, "DIRECTORY_ENTRY_IMPORT"):
            imp_dlls = len(pe.DIRECTORY_ENTRY_IMPORT)
            for e in pe.DIRECTORY_ENTRY_IMPORT:
                imp_funcs += len(e.imports)

        company, product, desc = version_strings(pe)
        out.write("\t".join([
            rel, machine, subsystem, is_dll, hex(entry), hex(pe.OPTIONAL_HEADER.ImageBase),
            str(pe.FILE_HEADER.NumberOfSections), str(exp), str(imp_dlls), str(imp_funcs),
            str(pe.OPTIONAL_HEADER.SizeOfImage),
            safe(company), safe(product), safe(desc),
        ]) + "\n")
        pe.close()


if __name__ == "__main__":
    main()
