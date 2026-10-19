import sys
import re
import pefile
import struct
import capstone

path = sys.argv[1]
extra_base = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x400000
needles = {}
for rawarg in sys.argv[3:]:
    va = int(rawarg, 0)
    pat = struct.pack("<I", va)
    needles[rawarg] = pat

pe = pefile.PE(path, fast_load=True)
text = [s for s in pe.sections if s.Name.startswith(b".text")][0]
data = text.get_data()
base = extra_base + text.VirtualAddress
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
for ins in md.disasm(data, base):
    hit = [k for k, pat in needles.items() if pat in ins.bytes]
    if hit:
        print(hex(ins.address), ins.mnemonic, ins.op_str, "|", ins.bytes.hex(), "|", ",".join(hit))
