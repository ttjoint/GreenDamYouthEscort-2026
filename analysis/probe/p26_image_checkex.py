"""P26: verify CImage.dll's OpenCV usage is live, and enumerate CHECK* exports.

Reproduces analysis/probe/p26_image_checkex.txt. Read-only; no writes.

Method: PE-HDR (exports + imports) and DISASM (capstone over .text, collecting
real call instructions, then BFS from each CHECK* export).
"""
import sys
from collections import deque

import pefile
from capstone import CS_ARCH_X86, CS_MODE_32, Cs

DLL = sys.argv[1] if len(sys.argv) > 1 else \
    "LH-installed-tree/Windows/CImage.dll"


def main() -> int:
    pe = pefile.PE(DLL)
    base = pe.OPTIONAL_HEADER.ImageBase
    image = pe.get_memory_mapped_image()
    text = next(s for s in pe.sections if b".text" in s.Name)
    lo = base + text.VirtualAddress
    hi = lo + text.Misc_VirtualSize

    # -- exports of interest
    exports = {base + e.address: e.name.decode()
               for e in pe.DIRECTORY_ENTRY_EXPORT.symbols if e.name}
    checks = sorted((v, n) for v, n in exports.items()
                    if n.startswith("CHECK") or n in ("CAPSCREEN", "DES2GO"))
    print(f"CHECK* exports: {len(checks)}")
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    for i, (va, name) in enumerate(checks):
        end = checks[i + 1][0] if i + 1 < len(checks) else va + 0x1000
        count = sum(1 for _ in md.disasm(image[va - base:end - base], va))
        print(f"  {name:<14} {va:#010x}  {end - va:>5} bytes  {count:>5} insn")

    # -- OpenCV imports and their stubs
    iat_names = {}
    for entry in pe.DIRECTORY_ENTRY_IMPORT:
        for imp in entry.imports:
            if imp.name:
                iat_names[imp.address] = imp.name.decode()

    stubs = {}
    rva = 0x9DD0
    while rva < 0x9E3C:
        if image[rva] == 0xFF and image[rva + 1] == 0x25:
            target = int.from_bytes(image[rva + 2:rva + 6], "little")
            stubs[base + rva] = iat_names.get(target, f"?{target:#x}")
            rva += 6
        else:
            rva += 1
    print(f"\njump stubs in 0x9dd0..0x9e3c: {len(stubs)}")

    # -- every real call instruction
    insn_calls = {}
    for ins in md.disasm(image[text.VirtualAddress:], lo):
        if ins.mnemonic == "call" and ins.op_str.startswith("0x"):
            insn_calls[ins.address] = int(ins.op_str, 16)
    print(f"real call instructions in .text: {len(insn_calls)}")

    def calls_within(start, end):
        return [t for a, t in insn_calls.items() if start <= a < end]

    def reachable(start, depth=3):
        seen, queue, hits = {start}, deque([(start, 0)]), []
        while queue:
            fn, d = queue.popleft()
            if d > depth:
                continue
            later = sorted(v for v in exports if v > fn)
            end = later[0] if later else hi
            for target in calls_within(fn, end):
                if target in stubs:
                    hits.append(stubs[target])
                    continue
                if lo <= target < hi and target not in seen:
                    seen.add(target)
                    queue.append((target, d + 1))
        return hits

    print("\nOpenCV reachable from each CHECK* (<=3 levels):")
    any_cv = False
    for va, name in checks:
        cv = sorted({h for h in reachable(va) if h.startswith("cv")})
        if cv:
            any_cv = True
            print(f"  {name:<14} {len(cv):>2} OpenCV functions")
    if not any_cv:
        print("  (none) - imports would be dead")

    # -- stub call counts, to show nothing is a dead import
    print("\ncall count per OpenCV stub:")
    for stub_va, name in sorted(stubs.items(), key=lambda x: x[1]):
        n = sum(1 for t in insn_calls.values() if t == stub_va)
        print(f"  {name:<24} {n}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
