"""Disassembly index for the legacy `.urf` loader call sites.

`1.urf`-`9.urf` are loaded by a function pointer reached through the host
module (`XNet2.exe` / `gn.exe` / ...). Several earlier rounds tried to decode
the file format and stalled; the productive question turned out to be *who
calls the loader and with what arguments*.

This tool answers that question mechanically: it scans a PE's `.text` for

* `push imm32` operands that land in `.rdata`/`.data` (string arguments),
* indirect `call reg` / `call [mem]` sites (the loader is never a direct call),
* direct references to the four-byte `.urf` magic,

and prints an annotated window around every `"%s\\%d.urf"` format string.
It prints *structure*, i.e. addresses and mnemonics. Literal string bytes are
summarised by length + sha1 prefix so the output stays safe to paste around.

Usage::

    python tools/disasm_urf_loader.py ../LH-installed-tree/Windows/System32/gn.exe
    python tools/disasm_urf_loader.py ../LH-installed-tree/Windows/System32/XNet2.exe --json out.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

try:
    import capstone
    import pefile
except ImportError:  # pragma: no cover
    print("capstone and pefile are required", file=sys.stderr)
    raise SystemExit(2)

URF_FORMAT = b"%s\\%d.urf"
URF_MAGIC = bytes.fromhex("5599259b")
# 32-char loader key name; compare by hash so we never print it as data.
KEY_SHAPE_LEN = 32


def _pe(path: Path):
    pe = pefile.PE(str(path), fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    return pe, base


def _image(path: Path) -> bytes:
    return path.read_bytes()


def section_of(pe, rva: int) -> str:
    for s in pe.sections:
        start = s.VirtualAddress
        end = start + max(s.Misc_VirtualSize, s.SizeOfRawData)
        if start <= rva < end:
            name = s.Name.rstrip(b"\x00").decode("latin-1")
            return name
    return "?"


def iter_text(pe, image: bytes):
    """Yield decoded instructions of every executable section."""
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    base = pe.OPTIONAL_HEADER.ImageBase
    for s in pe.sections:
        if not (s.Characteristics & 0x20000000):  # IMAGE_SCN_MEM_EXECUTE
            continue
        off = s.PointerToRawData
        size = s.Misc_VirtualSize
        if not off or not size:
            continue
        chunk = image[off: off + size]
        start = base + s.VirtualAddress
        for ins in md.disasm(chunk, start):
            yield ins


def read_cstring(image: bytes, pe, va: int, limit: int = 96) -> bytes | None:
    try:
        rva = va - pe.OPTIONAL_HEADER.ImageBase
        off = pe.get_offset_from_rva(rva)
    except Exception:
        return None
    raw = image[off: off + limit]
    end = raw.find(b"\x00")
    if end < 0:
        return None
    return raw[:end]


def _describe(payload: bytes) -> dict:
    printable = sum(1 for b in payload if 0x20 <= b < 0x7F)
    return {
        "len": len(payload),
        "sha1_8": hashlib.sha1(payload).hexdigest()[:8],
        "printable_ratio": round(printable / max(len(payload), 1), 3),
        "utf16le": len(payload) > 1 and payload[1::2].count(0) > len(payload) // 4,
    }


def scan(path: Path, window: int = 0x30) -> dict:
    pe, base = _pe(path)
    image = _image(path)
    insns = list(iter_text(pe, image))

    pushes: dict[int, list[tuple[int, str]]] = {}
    indirect_calls: list[tuple[int, str]] = []
    urf_fmt_sites: list[dict] = []

    for ins in insns:
        if ins.mnemonic == "push":
            try:
                target = int(ins.op_str, 16)
            except ValueError:
                continue
            sec = section_of(pe, target - base)
            if sec not in ("?", ".text"):
                payload = read_cstring(image, pe, target)
                pushes.setdefault(sec, []).append((ins.address, hex(target)))
                if payload == URF_FORMAT:
                    urf_fmt_sites.append({"push_at": hex(ins.address),
                                          "string_va": hex(target)})
        elif ins.mnemonic == "call" and (ins.op_str.startswith("dword ptr [")
                                         or ins.op_str in ("eax", "ebx", "ecx",
                                                           "edx", "esi", "edi",
                                                           "ebp")):
            indirect_calls.append((ins.address, ins.op_str))

    windows = []
    by_addr = {ins.address: ins for ins in insns}
    addrs = sorted(by_addr)
    for site in urf_fmt_sites:
        start_va = int(site["push_at"], 16) - window
        rows = []
        for a in addrs:
            if a < start_va or a > int(site["push_at"], 16) + window:
                continue
            ins = by_addr[a]
            row = {"at": hex(a), "mnemonic": ins.mnemonic, "ops": ins.op_str}
            ops = ins.op_str
            if ops.startswith("0x"):
                try:
                    v = int(ops, 16)
                except ValueError:
                    v = 0
                if v:
                    payload = read_cstring(image, pe, v)
                    if payload:
                        row["string"] = _describe(payload)
            rows.append(row)
        windows.append({"format_string_va": site["string_va"], "rows": rows})

    key_like = []
    for sec, items in pushes.items():
        for addr, sva in items:
            payload = read_cstring(image, pe, int(sva, 16))
            if payload and len(payload) == KEY_SHAPE_LEN and payload.isalpha():
                # 32 uppercase letters: the loader's name argument.
                key_like.append({"push_at": hex(addr), "string_va": sva,
                                 "sha1_8": hashlib.sha1(payload).hexdigest()[:8]})

    return {
        "module": str(path),
        "image_base": hex(base),
        "sections": [
            {"name": s.Name.rstrip(b"\x00").decode("latin-1"),
             "va": hex(base + s.VirtualAddress),
             "vsize": s.Misc_VirtualSize,
             "raw": s.PointerToRawData}
            for s in pe.sections
        ],
        "urf_format_sites": urf_fmt_sites,
        "loader_name_pushes": key_like,
        "indirect_call_count": len(indirect_calls),
        "urf_magic_present": image.find(URF_MAGIC) >= 0,
        "windows": windows,
    }


def loader_call_prologue(report: dict) -> dict:
    """Extract the canonical loader invocation from a scan report.

    Returns the two argument pushes that matter:

    * ``name`` — the 32-letter string pushed as the 4th argument,
    * ``flag`` — the small immediate pushed as the 3rd argument (always ``1``).

    Both hosts (`XNet2.exe`, `gn.exe`) produce the same shape, which is what
    makes the signature `loader(ctx, path, flag, name, out, reserved)`
    defensible rather than guessed.
    """
    out: dict = {}
    for win in report["windows"]:
        rows = win["rows"]
        for i, row in enumerate(rows):
            desc = row.get("string")
            if desc and desc["len"] == KEY_SHAPE_LEN and row["mnemonic"] == "push":
                out["name"] = {"at": row["at"], "sha1_8": desc["sha1_8"]}
                # Walk backwards for `push 1` (the flag) and the path build.
                for back in range(i - 1, max(i - 6, -1), -1):
                    prev = rows[back]
                    if prev["mnemonic"] == "push" and prev["ops"].isdigit():
                        out.setdefault("flag", {"at": prev["at"],
                                                "value": int(prev["ops"])})
                break
        if "name" in out:
            break
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("module", type=Path)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--window", type=lambda s: int(s, 0), default=0x30)
    args = ap.parse_args(argv)

    report = scan(args.module, args.window)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                             encoding="utf-8")

    print("module      ", report["module"])
    print("image base  ", report["image_base"])
    print("urf magic   ", "present" if report["urf_magic_present"] else "absent")
    print("indirect call sites:", report["indirect_call_count"])
    print("'%s\\\\%d.urf' push sites:", len(report["urf_format_sites"]))
    for site in report["urf_format_sites"]:
        print("   push @ %s  string @ %s" % (site["push_at"], site["string_va"]))
    prologue = loader_call_prologue(report)
    if prologue:
        print("loader call prologue:", json.dumps(prologue, sort_keys=True))
    print("32-letter name pushes:", len(report["loader_name_pushes"]))
    for k in report["loader_name_pushes"]:
        print("   push @ %s  string @ %s  sha1_8=%s"
              % (k["push_at"], k["string_va"], k["sha1_8"]))
    for w in report["windows"]:
        print("-- window around %s --" % w["format_string_va"])
        for row in w["rows"]:
            extra = ""
            if "string" in row:
                d = row["string"]
                extra = "   ; len=%d sha1_8=%s" % (d["len"], d["sha1_8"])
            print("   %s  %-8s %s%s" % (row["at"], row["mnemonic"], row["ops"], extra))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
