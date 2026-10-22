"""P27: reverse CHECKEX13x's main judgement path in CImage.dll.

Reproduces analysis/probe/p27_checkex13x.txt. Read-only.

Method: DISASM (capstone) + BYTES (constant extraction from .rdata).
Every number printed here is read out of the binary, not assumed.
"""
import struct
import sys

import pefile
from capstone import CS_ARCH_X86, CS_MODE_32, Cs

DLL = sys.argv[1] if len(sys.argv) > 1 else \
    "LH-installed-tree/Windows/CImage.dll"


def main() -> int:
    pe = pefile.PE(DLL)
    base = pe.OPTIONAL_HEADER.ImageBase
    image = pe.get_memory_mapped_image()
    with open(DLL, "rb") as fh:
        raw = fh.read()

    def read_double(rva: int) -> float:
        for s in pe.sections:
            if s.VirtualAddress <= rva < s.VirtualAddress + s.SizeOfRawData:
                off = s.PointerToRawData + (rva - s.VirtualAddress)
                return struct.unpack("<d", raw[off:off + 8])[0]
        raise ValueError(f"rva {rva:#x} not mapped")

    def read_u32(rva: int) -> int:
        for s in pe.sections:
            if s.VirtualAddress <= rva < s.VirtualAddress + s.SizeOfRawData:
                off = s.PointerToRawData + (rva - s.VirtualAddress)
                return struct.unpack("<I", raw[off:off + 4])[0]
        raise ValueError(f"rva {rva:#x} not mapped")

    md = Cs(CS_ARCH_X86, CS_MODE_32)

    # -- CHECKEX13x prologue: the shared anti-debug guard
    print("== CHECKEX13x @ 0x10007ea0: prologue ==")
    n = 0
    for ins in md.disasm(image[0x7EA0:0x7EA0 + 0x40], base + 0x7EA0):
        print(f"  {ins.address:#010x}  {ins.mnemonic:8} {ins.op_str}")
        n += 1
        if n >= 12:
            break

    print("\n== size gate (both dimensions must be >= 0x50) ==")
    for ins in md.disasm(image[0x7F88:0x7F90], base + 0x7F88):
        print(f"  {ins.address:#010x}  {ins.mnemonic:8} {ins.op_str}")

    # -- the main judgement function
    print("\n== main judgement @ 0x100078e0 ==")
    n = 0
    for ins in md.disasm(image[0x78E0:0x78E0 + 0x120], base + 0x78E0):
        print(f"  {ins.address:#010x}  {ins.mnemonic:8} {ins.op_str}")
        n += 1
        if n >= 40:
            break

    # -- constants used by the main judgement
    print("\n== constants in the main judgement ==")
    for rva, note in (
        (0xD690, "ratio1 lower bound"),
        (0xD568, "ratio2 lower bound"),
        (0xD418, "ratio1 branch point"),
        (0xD6A0, "ratio1 -> 0..255 scale"),
        (0xD698, "ratio1 offset"),
        (0xD508, "1/255 normalisation"),
        (0xD530, "channel lower bound"),
        (0xD570, "channel upper bound"),
        (0xD658, "stddev threshold"),
        (0xD660, "grey rounding"),
        (0xD668, "grey weight C"),
        (0xD670, "grey weight B"),
        (0xD678, "grey weight A"),
    ):
        print(f"  [0x1000{rva:04x}] = {read_double(rva)!r:24} {note}")

    # -- texture / complexity grade table
    print("\n== grade boundaries used by 0x100027e0 ==")
    for rva, note in (
        (0xD4F0, "20"), (0xD4E8, "40"), (0xD4E0, "75"),
        (0xD4D8, "155"), (0xD458, "190"), (0xD4D0, "270"),
        (0xD4C8, "295"), (0xD430, "316"),
        (0xD4C0, "low band 0.2"), (0xD4B8, "low band 0.7"),
    ):
        print(f"  [0x1000{rva:04x}] = {read_double(rva)!r:10} {note}")

    # -- per-grade weight table
    print("\n== per-grade weight table @ 0x1000d110 ==")
    for i in range(8):
        print(f"  [{i}] = {read_double(0xD110 + i * 8)!r}")

    # -- the OpenCV stubs this function reaches, in call order
    print("\n== call order inside 0x10007140 (the analysis pipeline) ==")
    iat_names = {}
    for entry in pe.DIRECTORY_ENTRY_IMPORT:
        for imp in entry.imports:
            if imp.name:
                iat_names[imp.address] = imp.name.decode()
    stubs = {}
    rva = 0x9DD0
    while rva < 0x9E60:
        if image[rva] == 0xFF and image[rva + 1] == 0x25:
            target = read_u32(rva + 2)
            stubs[base + rva] = iat_names.get(target, f"ordinal#{target & 0xFFFF}")
            rva += 6
        else:
            rva += 1

    for ins in md.disasm(image[0x7140:0x78E0], base + 0x7140):
        if ins.mnemonic == "call" and ins.op_str.startswith("0x"):
            target = int(ins.op_str, 16)
            label = stubs.get(target)
            if label:
                print(f"  {ins.address:#010x}  {label}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
