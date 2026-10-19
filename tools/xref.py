import sys
import pefile
import capstone

path = sys.argv[1]
targets = {}
for arg in sys.argv[2:]:
    va, _, label = arg.partition(":")
    targets[int(va, 0)] = label or va

pe = pefile.PE(path, fast_load=True)
imagebase = pe.OPTIONAL_HEADER.ImageBase
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
refs = {}
for sect in pe.sections:
    if not sect.Name.startswith(b".text"):
        continue
    data = sect.get_data()
    base = imagebase + sect.VirtualAddress
    for ins in md.disasm(data, base):
        if ins.mnemonic not in ("push", "mov", "lea", "cmp", "and", "or"):
            continue
        for va, label in targets.items():
            if f"0x{va:x}" in ins.op_str:
                refs.setdefault(label, []).append(
                    (hex(ins.address), ins.mnemonic, ins.op_str))

for label, hits in refs.items():
    print(f"{label}: {len(hits)} refs")
    for addr, mnem, ops in hits[:12]:
        print(f"  {addr}  {mnem} {ops}")
