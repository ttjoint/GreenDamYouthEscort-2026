"""P28: reverse CHECKEX8x's judgement path, including its skin-tone stage.

Reproduces analysis/probe/p28_checkex8x.txt. Read-only.

Method: DISASM (capstone) + BYTES (constants from .rdata).
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

    def rd(rva: int, n: int = 8) -> bytes:
        for s in pe.sections:
            if s.VirtualAddress <= rva < s.VirtualAddress + s.SizeOfRawData:
                off = s.PointerToRawData + (rva - s.VirtualAddress)
                return raw[off:off + n]
        raise ValueError(f"rva {rva:#x} not mapped")

    def dbl(rva: int) -> float:
        return struct.unpack("<d", rd(rva))[0]

    md = Cs(CS_ARCH_X86, CS_MODE_32)

    print("== CHECKEX8x prologue: same 0x7ab7 guard, different return codes ==")
    n = 0
    for ins in md.disasm(image[0x6FB0:0x6FB0 + 0x60], base + 0x6FB0):
        print(f"  {ins.address:#010x}  {ins.mnemonic:8} {ins.op_str}")
        n += 1
        if n >= 22:
            break

    print("\n== resize target and size gate ==")
    for ins in md.disasm(image[0x7064:0x7064 + 0x30], base + 0x7064):
        print(f"  {ins.address:#010x}  {ins.mnemonic:8} {ins.op_str}")

    print("\n== main judgement @ 0x10006d40: extra width==160 gate ==")
    for ins in md.disasm(image[0x6D6E:0x6D6E + 0x40], base + 0x6D6E):
        print(f"  {ins.address:#010x}  {ins.mnemonic:8} {ins.op_str}")

    print("\n== thresholds ==")
    for rva, note in (
        (0xD648, "resize target width"),
        (0xD650, "ratio1 lower bound"),
        (0xD420, "ratio1 branch point"),
        (0xD550, "ratio2/ratio1 threshold"),
        (0xD530, "saturation lower bound"),
        (0xD528, "saturation upper bound"),
        (0xD538, "third band"),
        (0xD4F8, "hue full circle"),
        (0xD500, "hue band"),
        (0xD510, "value band low"),
        (0xD520, "value band high"),
    ):
        print(f"  [0x1000{rva:04x}] = {dbl(rva)!r:24} {note}")

    print("\n== hue grade table used by 0x10009360 ==")
    for rva, note in (
        (0xD4F0, "20"), (0xD4E8, "40"), (0xD4E0, "75"),
        (0xD4D8, "155"), (0xD458, "190"), (0xD4D0, "270"),
        (0xD4C8, "295"), (0xD430, "316"),
    ):
        print(f"  [0x1000{rva:04x}] = {dbl(rva):>7} {note}")

    print("\n== the ten hue buckets kept by 0x100096c0 ==")
    # 0x100096d6..0x1000971e pushes these as dwords.
    n = 0
    for ins in md.disasm(image[0x96D6:0x96D6 + 0x50], base + 0x96D6):
        if ins.mnemonic == "mov" and "0x" in ins.op_str:
            print(f"  {ins.address:#010x}  {ins.op_str}")
            n += 1
        if n >= 12:
            break

    print("\n== calls made by the main judgement ==")
    iat_names = {}
    for entry in pe.DIRECTORY_ENTRY_IMPORT:
        for imp in entry.imports:
            if imp.name:
                iat_names[imp.address] = imp.name.decode()
    stubs = {}
    rva = 0x9DD0
    while rva < 0x9E60:
        if image[rva] == 0xFF and image[rva + 1] == 0x25:
            target = struct.unpack("<I", image[rva + 2:rva + 6])[0]
            stubs[base + rva] = iat_names.get(target, "ordinal")
            rva += 6
        else:
            rva += 1
    for ins in md.disasm(image[0x6D40:0x6FB0], base + 0x6D40):
        if ins.mnemonic == "call" and ins.op_str.startswith("0x"):
            target = int(ins.op_str, 16)
            label = stubs.get(target, f"{target:#x}")
            print(f"  {ins.address:#010x}  {label}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
