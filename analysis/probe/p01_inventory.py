#!/usr/bin/env python3
"""P01 - File inventory. The anchor for every other table row.

Emits, for every file in the analysis scope:
  path, size, sha256, md5, magic-derived type, PE-ness, section count

No interpretation. Byte facts only. Every row it produces is status=CLOSED
because it is directly re-derivable by rerunning this script.

Usage:  python ANALYSIS/probe/p01_inventory.py > ANALYSIS/01-FILES.tsv
"""
import hashlib
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Scope: the two extracted trees + the two containers + extracted side-cars.
SCOPE_DIRS = ["LH-installed-tree", "_decoded", "_drivers", "_urf_analysis", "_urf_images"]
SCOPE_FILES = ["LH-setup3.17.exe", "HNC信息智能过滤器_104_262457.exe"]


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def pe_info(p):
    """Return (is_pe, machine, characteristic_summary) or (False, '-', '-')."""
    try:
        with open(p, "rb") as f:
            head = f.read(0x400)
    except OSError:
        return False, "-", "-"
    if head[:2] != b"MZ" or len(head) < 0x40:
        return False, "-", "-"
    e_lfanew = struct.unpack_from("<I", head, 0x3C)[0]
    if e_lfanew + 0x18 > len(head):
        return False, "-", "-"
    if head[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        return False, "-", "-"
    machine = struct.unpack_from("<H", head, e_lfanew + 4)[0]
    nsec = struct.unpack_from("<H", head, e_lfanew + 6)[0]
    opt_magic = struct.unpack_from("<H", head, e_lfanew + 24)[0]
    bit = {0x10B: "PE32", 0x20B: "PE32+"}.get(opt_magic, "?")
    mname = {0x14C: "i386", 0x8664: "amd64"}.get(machine, hex(machine))
    return True, mname, f"{bit}/{mname}/nsec={nsec}"


def magic_type(p):
    try:
        with open(p, "rb") as f:
            b = f.read(64)
    except OSError:
        return "?"
    if b[:2] == b"MZ":
        return "PE"
    if b[:3] == b"GIF":
        return "GIF"
    if b[:2] == b"PK":
        return "ZIP"
    if b[:4] == b"\xd0\xcf\x11\xe0":
        return "OLE/CHM"
    if b[:8] == b"ITSF\x03\x00\x00\x00":
        return "CHM(ITSF)"
    if b[:5] in (b"HTTP/",):
        return "HTTP-RESPONSE"
    if b[:2] == b"\x1f\x8b":
        return "GZIP"
    # printability probe
    sample = b[:64]
    printable = sum(1 for c in sample if 9 <= c <= 13 or 32 <= c <= 126)
    if printable > 60:
        return "TEXT"
    return "BLOB"


def main():
    rows = []
    for f in SCOPE_FILES:
        full = os.path.join(ROOT, f)
        if os.path.exists(full):
            rows.append((f, full))
    for d in SCOPE_DIRS:
        base = os.path.join(ROOT, d)
        for dirpath, _dirnames, filenames in os.walk(base):
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                rows.append((os.path.relpath(full, ROOT).replace("\\", "/"), full))

    out = sys.stdout
    out.write("path\tsize\tsha256\tmd5\tmagic\tis_pe\tpe_detail\n")
    for rel, full in sorted(rows):
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        is_pe, _, detail = pe_info(full)
        out.write("\t".join([
            rel,
            str(size),
            sha256(full),
            md5(full),
            magic_type(full),
            "yes" if is_pe else "no",
            detail,
        ]) + "\n")


if __name__ == "__main__":
    main()
