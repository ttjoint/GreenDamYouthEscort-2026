"""Recover the API hook table that `Handler.dll` hands to `HASrv.dll`.

`HASrv.dll` is a generic inline-hooking engine. It knows nothing about
pornography, word lists, or China: its 64 KB of code contains no hooked API
names at all, only the four exports `SetApiHookList`, `InstallApiHook`,
`UninstallApiHook` and `LoadClient`. Everything it hooks is passed in from
outside, which is why the interesting data lives in `Handler.dll`.

Two tables are in `Handler.dll`'s `.data`:

* `0x31014178` — used when the ANSI flag `[0x31018fac]` is zero
* `0x31014470` — used when that flag is non-zero

`Handler.dll` loads the engine with `LoadLibraryA("HASrv.dll")` (falling back
to `LoadLibraryExA`), resolves the three entry points, then calls
`SetApiHookList(table)`. Which table it passes is decided by that one global.

Record layout — 108 bytes (`0x6c`), which is exactly the stride `HASrv.dll`
walks in `InstallApiHook` (`lea eax,[esi+esi*2]` then `lea eax,[eax+eax*8]`,
i.e. `esi * 27` dwords):

    +0x00  16 bytes   DLL name, NUL padded
    +0x10  16 bytes   imported API name, NUL padded
    +0x20  16 bytes   zero in the shipped data (runtime scratch)
    +0x30  16 bytes   zero in the shipped data; `InstallApiHook` scans it
                      for a NUL, so it becomes the original-function slot
    +0x40  4 bytes    argument count
    +0x44  60 bytes   zeroed

Only three fields carry data on disk. That matters for reading this file:
the tables are *declarations of intent*, and the addresses they will patch
are written at install time, not built in.

`SetApiHookList` clears a 0x21c-dword (2160 byte) global region, so the
engine holds at most 20 records. The wide table uses 15, the ANSI one 4.

This tool reports names and counts. It never reads a word list.

Usage::

    python tools/audit_api_hooks.py --legacy ../LH-installed-tree
    python tools/audit_api_hooks.py --legacy ../LH-installed-tree --json out.json
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

RECORD_SIZE = 0x6C
MAX_RECORDS = 20  # SetApiHookList clears 0x21c dwords == 2160 bytes

HANDLER_BASE = 0x31000000
ANSI_TABLE = 0x31014178
WIDE_TABLE = 0x31014470

# The selector is written at 0x31002290 from a settings.ini lookup.
SELECTOR_VA = 0x31018FAC
SELECTOR_WRITE_RVA = 0x2290
SELECTOR_INI_VA = 0x3101536C
SELECTOR_SECTION_VA = 0x31015354
SELECTOR_KEY_VA = 0x31015360

# Field offsets inside a record.
OFF_DLL = 0x00
OFF_API = 0x10
OFF_KEY = 0x30
OFF_ARGN = 0x40


@dataclass
class HookRecord:
    index: int
    dll: str
    api: str
    key: str
    arg_count: int

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "dll": self.dll,
            "api": self.api,
            "key": self.key,
            "arg_count": self.arg_count,
        }


def _cstr(blob: bytes, size: int) -> str:
    return blob[:size].split(b"\x00")[0].decode("ascii", "replace")


def _read_table(raw: bytes, va: int, limit: int = MAX_RECORDS) -> list[HookRecord]:
    """Walk a NUL-terminated table of fixed-size records."""
    off = va - HANDLER_BASE
    out: list[HookRecord] = []
    for n in range(limit):
        rec = raw[off + n * RECORD_SIZE : off + (n + 1) * RECORD_SIZE]
        if len(rec) < RECORD_SIZE:
            break
        dll = _cstr(rec[OFF_DLL : OFF_DLL + 16], 16)
        api = _cstr(rec[OFF_API : OFF_API + 16], 16)
        if not dll or not api:
            break
        out.append(
            HookRecord(
                index=n,
                dll=dll,
                api=api,
                key=_cstr(rec[OFF_KEY : OFF_KEY + 16], 16),
                arg_count=struct.unpack("<I", rec[OFF_ARGN : OFF_ARGN + 4])[0],
            )
        )
    return out


def _group(records: list[HookRecord]) -> dict[str, list[str]]:
    """Group API names by owning DLL, order preserved."""
    grouped: dict[str, list[str]] = {}
    for r in records:
        grouped.setdefault(r.dll, []).append(r.api)
    return grouped


def _classify(dll: str, api: str) -> str:
    """Bucket a hook by what it is actually for.

    Two of these buckets are *not* filtering. They disable the user's ability
    to turn the product off, which is worth naming separately.
    """
    low = api.lower()
    if dll.lower().startswith("gdi32"):
        return "text_rendering"
    if dll.lower().startswith("advapi32") and low.startswith("reg"):
        return "self_protect"
    if low in ("openprocess",):
        return "self_protect"
    if low.startswith("openservice"):
        return "self_protect"
    return "other"


def audit(legacy: Path) -> dict:
    handler = legacy / "Windows" / "Handler.dll"
    raw = handler.read_bytes()

    ansi = _read_table(raw, ANSI_TABLE)
    wide = _read_table(raw, WIDE_TABLE)
    buckets: dict[str, list[str]] = {}
    for r in wide:
        buckets.setdefault(_classify(r.dll, r.api), []).append(r.api)

    return {
        "_note": (
            "API hook tables passed from Handler.dll to HASrv.dll. "
            "HASrv.dll itself contains no hooked API names."
        ),
        "handler_size": handler.stat().st_size,
        "record_size": RECORD_SIZE,
        "engine_max_records": MAX_RECORDS,
        "ansi": {
            "va": hex(ANSI_TABLE),
            "count": len(ansi),
            "by_dll": _group(ansi),
            "records": [r.as_dict() for r in ansi],
        },
        "wide": {
            "va": hex(WIDE_TABLE),
            "count": len(wide),
            "by_dll": _group(wide),
            "records": [r.as_dict() for r in wide],
        },
        "buckets": {k: sorted(v) for k, v in sorted(buckets.items())},
        "wide_superset_of_ansi": set(
            (r.dll, r.api) for r in ansi
        ).issubset({(r.dll, r.api) for r in wide}),
        "selector": _selector(raw),
    }


def _selector(raw: bytes) -> dict:
    """Decode how the two tables are chosen.

    `[0x31018fac]` is written once, at 0x31002290, as `setne dl` over a
    comparison against 2. The value compared comes from settings.ini.
    """
    return {
        "global_va": hex(SELECTOR_VA),
        "write_rva": hex(SELECTOR_WRITE_RVA),
        "ini_path": _cstr(raw[SELECTOR_INI_VA - HANDLER_BASE :][:64], 64),
        "section": _cstr(raw[SELECTOR_SECTION_VA - HANDLER_BASE :][:32], 32),
        "key": _cstr(raw[SELECTOR_KEY_VA - HANDLER_BASE :][:32], 32),
        "ansi_table_when": "value == 2",
        "wide_table_when": "value != 2",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--legacy", required=True, type=Path)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args(argv)

    report = audit(args.legacy)

    print("== HASrv.dll is a generic engine ==")
    print(f"  max records it can hold   {report['engine_max_records']}")
    print(f"  record stride             {report['record_size']} bytes")
    print()
    for tag in ("ansi", "wide"):
        t = report[tag]
        print(f"== {tag} table @ {t['va']}: {t['count']} hooks ==")
        for dll, apis in t["by_dll"].items():
            print(f"  {dll:<14} {len(apis):>2}  {', '.join(apis)}")
        print()
    print("== what they are for ==")
    for bucket, apis in report["buckets"].items():
        print(f"  {bucket:<16} {len(apis):>2}  {', '.join(apis)}")
    print()
    print(f"wide table contains every ansi entry: {report['wide_superset_of_ansi']}")

    sel = report["selector"]
    print()
    print("== which table gets installed ==")
    print(f"  {sel['ini_path']} [{sel['section']}] {sel['key']}")
    print(f"  ansi table when {sel['ansi_table_when']}")
    print(f"  wide table when {sel['wide_table_when']}")

    if args.json:
        args.json.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
