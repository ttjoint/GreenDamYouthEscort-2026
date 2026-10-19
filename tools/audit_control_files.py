"""Audit the legacy *control* files that were never classified as word lists.

The 3.17 tree mixes three completely different kinds of payload in
`System32\\`:

1. **Word/rule lists** (`adwfil.dat`, `TrustUrl.dat`, ...) — 2:1 obfuscated,
   decoded by `tools/legacy_wordlist_codec.py`.
2. **Raw little-endian control structs** (`sys.dat`, `sysEx.dat`,
   `xconfigs.dat`) — *not* obfuscated at all, they are plain `uint32` arrays.
   Running the codec over them silently produces 134 `<` characters, which is
   a good example of why "it decoded without error" is not evidence.
3. **Plain ASCII policy lists** (`filtport.dat`) — plaintext, no codec.

This tool reports structure and counts only. It never echoes a list entry,
because `filtport.dat` names third-party proxy tools and `TrustUrl.dat` holds
hosts.

Usage::

    python tools/audit_control_files.py --legacy ../LH-installed-tree
    python tools/audit_control_files.py --legacy ../LH-installed-tree --json out.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from legacy_wordlist_codec import decode_text  # noqa: E402

# Files that are plain uint32 arrays, not obfuscated text.
DWORD_FILES = ("sys.dat", "sysEx.dat", "xconfigs.dat")
# Files that are plain ASCII/GBK, no codec.
PLAIN_FILES = ("filtport.dat",)


@dataclass
class DwordFile:
    name: str
    size: int
    sha256: str
    word_count: int
    # Sorted counts so the reader can see the shape without seeing the values.
    value_histogram: dict[str, int] = field(default_factory=dict)
    max_value: int = 0
    nonzero_count: int = 0
    # Text decoded by the *word list* codec, kept only as a guard.
    codec_roundtrip_is_ascii_garbage: bool = False


@dataclass
class PlainFile:
    name: str
    size: int
    sha256: str
    trailing_crlf: bool
    ascii_only: bool
    line_count: int
    # tuple-splitting summary; the separator is '/' in this file
    entry_fields: int = 0
    entries_with_numeric_tail: int = 0
    entries_with_named_proto: int = 0
    kinds: dict[str, int] = field(default_factory=dict)


@dataclass
class CodecFile:
    name: str
    size: int
    sha256: str
    obfuscated: bool
    line_count: int = 0
    unique_lines: int = 0
    min_len: int = 0
    max_len: int = 0


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def audit_dword(name: str, data: bytes) -> DwordFile:
    count = len(data) // 4
    values = struct.unpack("<%dI" % count, data[: count * 4])
    hist: dict[str, int] = {}
    for v in values:
        hist[str(v)] = hist.get(str(v), 0) + 1
    # The codec runs over the same bytes without raising — demonstrate that
    # "decodes" is not the same as "is obfuscated text".
    try:
        decoded = decode_text(data)
        garbage = set(decoded) == {"<"}
    except Exception:  # pragma: no cover - defensive
        garbage = False
    return DwordFile(
        name=name,
        size=len(data),
        sha256=_sha256(data),
        word_count=count,
        value_histogram=dict(sorted(hist.items(), key=lambda kv: int(kv[0]))),
        max_value=max(values) if values else 0,
        nonzero_count=sum(1 for v in values if v),
        codec_roundtrip_is_ascii_garbage=garbage,
    )


def audit_plain(name: str, data: bytes) -> PlainFile:
    # Deliberately decode as latin-1: this file is single-byte ASCII and we do
    # not want a stray byte to abort the audit.
    text = data.decode("latin-1")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    fields = 0
    numeric_tail = 0
    named_proto = 0
    kinds: dict[str, int] = {}
    for ln in lines:
        parts = ln.split("/")
        fields = max(fields, len(parts))
        # `名字/端口/协议` — the middle field is the port, the tail is the
        # transport name. Check both explicitly instead of only the tail.
        if len(parts) >= 2 and parts[1].isdigit():
            numeric_tail += 1
        if len(parts) >= 3 and parts[-1].isalpha() and 2 <= len(parts[-1]) <= 5:
            named_proto += 1
        elif parts and parts[-1].isdigit():
            numeric_tail += 1
        for p in parts:
            kind = "digits" if p.isdigit() else "alpha" if p.isalpha() else "mixed"
            kinds[kind] = kinds.get(kind, 0) + 1
    return PlainFile(
        name=name,
        size=len(data),
        sha256=_sha256(data),
        trailing_crlf=data.endswith(b"\r\n"),
        ascii_only=all(0x20 <= b < 0x7F or b in (13, 10) for b in data),
        line_count=len(lines),
        entry_fields=fields,
        entries_with_numeric_tail=numeric_tail,
        entries_with_named_proto=named_proto,
        kinds=dict(sorted(kinds.items())),
    )


def audit_codec(name: str, data: bytes) -> CodecFile:
    obfuscated = data and all(0x30 <= b <= 0x6F for b in data if b not in (13, 10))
    if not obfuscated:
        return CodecFile(name=name, size=len(data), sha256=_sha256(data),
                         obfuscated=False)
    lines = [ln for ln in decode_text(data).splitlines() if ln.strip()]
    lengths = [len(ln) for ln in lines] or [0]
    return CodecFile(
        name=name,
        size=len(data),
        sha256=_sha256(data),
        obfuscated=True,
        line_count=len(lines),
        unique_lines=len(set(lines)),
        min_len=min(lengths),
        max_len=max(lengths),
    )


def run(legacy: Path) -> dict:
    sysdir = legacy / "Windows" / "System32"
    report: dict = {
        "_note": "Structure and counts only. No list entry is reproduced.",
        "system32": str(sysdir),
        "dword_files": [],
        "plain_files": [],
        "codec_files": [],
        "missing_declared": [],
    }
    for name in DWORD_FILES:
        p = sysdir / name
        if p.exists():
            report["dword_files"].append(asdict(audit_dword(name, p.read_bytes())))
    for name in PLAIN_FILES:
        p = sysdir / name
        if p.exists():
            report["plain_files"].append(asdict(audit_plain(name, p.read_bytes())))
    for name in ("wfile.dat", "TrustUrl.dat", "adwapp.dat", "adwfil.dat",
                 "iawfil.dat", "lgwfil.dat", "vgamfil.dat"):
        p = sysdir / name
        if p.exists():
            report["codec_files"].append(asdict(audit_codec(name, p.read_bytes())))
    # Files `Surfgd.dll` knows how to open but the package does not ship.
    for name in ("uFWORD.dat", "uTUrl.Dat", "uFUrl.Dat", "filtpros.Dat",
                 "FiltUrl.Dat", "server.dat"):
        if not (sysdir / name).exists() and not (sysdir / name.lower()).exists():
            report["missing_declared"].append(name)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--legacy", required=True, type=Path)
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args(argv)

    report = run(args.legacy)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                             encoding="utf-8")

    print("== uint32 control structs (NOT obfuscated) ==")
    for f in report["dword_files"]:
        print("  %-14s %6d bytes  %4d dwords  max=%-4d nonzero=%-4d codec_garbage=%s"
              % (f["name"], f["size"], f["word_count"], f["max_value"],
                 f["nonzero_count"], f["codec_roundtrip_is_ascii_garbage"]))
    print("== plain ASCII policy lists ==")
    for f in report["plain_files"]:
        print("  %-14s %6d bytes  %3d lines  fields=%d proto=%d ascii=%s"
              % (f["name"], f["size"], f["line_count"], f["entry_fields"],
                 f["entries_with_named_proto"], f["ascii_only"]))
    print("== obfuscated lists ==")
    for f in report["codec_files"]:
        print("  %-14s %6d bytes  obfuscated=%-5s lines=%-5d unique=%-5d len=%d..%d"
              % (f["name"], f["size"], f["obfuscated"], f["line_count"],
                 f["unique_lines"], f["min_len"], f["max_len"]))
    if report["missing_declared"]:
        print("== declared by Surfgd.dll but not shipped ==")
        print("  " + ", ".join(report["missing_declared"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
