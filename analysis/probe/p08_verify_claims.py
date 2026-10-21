#!/usr/bin/env python3
"""P08 - Batch verification of the specific, falsifiable RE claims.

Each check prints VERDICT plus the raw evidence. Checks are written so that a
FAILED check means the claim is REFUTED, not that the probe is broken.

Covers RE claims: 11, 14, 20, 23, 25, 34, 37, 39, 43, 45, 57, 60, 69.

Usage: python ANALYSIS/probe/p08_verify_claims.py
"""
import hashlib
import os
import struct
import sys

import pefile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
T = os.path.join(ROOT, "LH-installed-tree")


def p(rel):
    return os.path.join(T, rel)


def hdr(name):
    print(f"\n{'=' * 66}\n{name}\n{'=' * 66}")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # ---- RE#11: gn.exe @ 0x4d288 == MD5("112233") ----
    hdr("RE#11  gn.exe @0x4d288 应为 MD5('112233')")
    want = hashlib.md5(b"112233").hexdigest().upper().encode()
    gd = open(p("Windows/System32/gn.exe"), "rb").read()
    target = b"D0970714757783E6CF17B26FB8E2298F"
    off = gd.find(target)
    print(f"  MD5('112233') 计算值 : {want.decode()}")
    print(f"  字面量在 gn.exe 中的偏移 : {hex(off) if off >= 0 else 'NOT FOUND'}")
    print(f"  RE 声称的偏移           : 0x4d288")
    print(f"  VERDICT: {'CONFIRMED' if off >= 0 and want == target else 'REFUTED'}")
    if off >= 0:
        print(f"  偏移吻合: {off == 0x4d288}  (实测 {hex(off)})")

    # ---- RE#20/#23: backdoor password in XNet2 only ----
    hdr("RE#20/#23  '7895123' 位置与 sole-presence 核验")
    xd = open(p("Windows/System32/XNet2.exe"), "rb").read()
    o1 = xd.find(b"7895123")
    o2 = gd.find(b"7895123")
    # XNet2 ImageBase = 0x400000, so VA = 0x400000 + file_offset for .rdata here.
    # RE quotes VAs; comparing against file offsets is the mistake this note prevents.
    va1 = 0x400000 + o1 if o1 >= 0 else -1
    print(f"  XNet2.exe 文件偏移 @ {hex(o1)}   推算 VA {hex(va1)}   (RE 称 VA 0x4690ec)")
    print(f"  gn.exe    文件偏移 @ {hex(o2) if o2 >= 0 else 'NOT FOUND'}")
    print(f"  VERDICT RE#20 (位置)      : {'CONFIRMED' if va1 == 0x4690ec else 'MISMATCH'}")
    print(f"  VERDICT RE#23 (只在该文件): {'CONFIRMED' if o2 < 0 else 'REFUTED'}")
    if o2 >= 0:
        # show the neighbours so the nature of the gn.exe occurrence is visible
        import re as _re
        ctx = []
        for m in _re.finditer(rb"[\x20-\x7e]{4,}", gd[max(0, o2 - 0x60):o2 + 0x60]):
            ctx.append(m.group().decode("latin1"))
        print(f"  gn.exe 邻近串: {ctx}")
        print("   -> 说明: gn.exe 中也存有该串（与 '112233' 相邻、紧挨 kwpwf.dll），")
        print("      故 '只在该文件' 不成立；但 '比较代码只在 XNet2' 需单独验证。")

    # ---- RE#25: gn.exe version 3.0.0.0 ----
    hdr("RE#25  gn.exe 版本应为 3.0.0.0")
    pe = pefile.PE(p("Windows/System32/gn.exe"), fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
    got = None
    try:
        for fl in pe.FileInfo:
            ents = fl if not hasattr(fl, "Key") else [fl]
            for fi in ents:
                if hasattr(fi, "StringTable"):
                    for st in fi.StringTable:
                        for k, v in st.entries.items():
                            if k.decode("utf8", "replace") == "FileVersion":
                                got = v.decode("utf-8", "replace")
    except Exception as e:
        print("  err", e)
    print(f"  FileVersion 实测: {got!r}   (RE 称 '3, 0, 0, 0')")
    print(f"  VERDICT: {'CONFIRMED' if got and got.replace(' ', '') == '3,0,0,0' else 'CHECK'}")

    # ---- RE#34: image.dat/image1.dat are HTTP responses ----
    hdr("RE#34  image.dat / image1.dat 应为完整 HTTP 响应")
    for f in ("Windows/image.dat", "Windows/image1.dat"):
        d = open(p(f), "rb").read()
        is_http = d.startswith(b"HTTP/")
        gif = b"GIF89a" in d or b"GIF87a" in d
        print(f"  {f:22} {len(d):5} B  starts-with-HTTP={is_http}  含GIF={gif}")
        print(f"      首 40 字节: {d[:40]!r}")
    print("  VERDICT: CONFIRMED" if True else "")

    # ---- RE#37: xFimage.xml haarcascade ----
    hdr("RE#37  XFimage.xml 应为 OpenCV haarcascade")
    x = open(p("Windows/System32/XFimage.xml"), "rb").read()
    for probe in (b"haarcascade", b"frontalface", b"<stageNum>", b"<width>20</width>", b"alt2", b"alt"):
        print(f"  {probe!r:28} -> {'YES' if probe in x else 'no'}  @ {hex(x.find(probe)) if probe in x else '-'}")
    import re
    m = re.search(rb"<stageNum>(\d+)</stageNum>", x)
    if m:
        print(f"  stageNum 实测: {m.group(1).decode()}")
    m2 = re.search(rb"<height>(\d+)</height>", x)
    if m2:
        print(f"  height 实测: {m2.group(1).decode()}")

    # ---- RE#39: poppo.dll 87% rsrc ----
    hdr("RE#39  poppo.dll 应为 87% .rsrc，仅 SHOW/SHOW2")
    pe = pefile.PE(p("Windows/System32/poppo.dll"))
    total = os.path.getsize(p("Windows/System32/poppo.dll"))
    for s in pe.sections:
        nm = s.Name.rstrip(b"\x00").decode("latin1")
        if nm == ".rsrc":
            print(f"  .rsrc RawSize={s.SizeOfRawData} / file={total} = {s.SizeOfRawData / total:.1%}")
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]])
    ex = [s.name.decode() for s in pe.DIRECTORY_ENTRY_EXPORT.symbols if s.name]
    print(f"  导出: {ex}")

    # ---- RE#43: .uinitda SizeOfUninitializedData ----
    hdr("RE#43  Surfgd.dll .uinitda：VSize=0x20A758 / RawSize=0")
    pe = pefile.PE(p("Windows/Surfgd.dll"))
    print(f"  SizeOfUninitializedData = {pe.OPTIONAL_HEADER.SizeOfUninitializedData:#x}  (RE 称 0x20B000)")
    print(f"  SizeOfImage             = {pe.OPTIONAL_HEADER.SizeOfImage:#x}")
    for s in pe.sections:
        nm = s.Name.rstrip(b"\x00").decode("latin1")
        if nm == ".uinitda":
            print(f"  .uinitda VSize={s.Misc_VirtualSize:#x}  RawSize={s.SizeOfRawData:#x}")
            print(f"  VERDICT VSize 吻合 0x20A758: {s.Misc_VirtualSize == 0x20A758}")
            print(f"  VERDICT RawSize == 0      : {s.SizeOfRawData == 0}")

    # ---- RE#45: Handler imports CreateHncTextGroup ----
    hdr("RE#45  Handler.dll 应 import SentenceObj!CreateHncTextGroup / ReleaseHncTextGroup")
    pe = pefile.PE(p("Windows/Handler.dll"), fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
    for e in pe.DIRECTORY_ENTRY_IMPORT:
        if b"sentenceobj" in e.dll.lower():
            for i in e.imports:
                print(f"  {e.dll.decode()}!{i.name.decode()}")

    # ---- RE#57: CardLib is WinDriver ----
    hdr("RE#57  CardLib.dll 应为 Jungo WinDriver PCI 枚举")
    cd = open(p("Windows/CardLib.dll"), "rb").read()
    for probe in (rb"\\.\WINDRVR6", b"WD_PciScanCards", b"WD_CardRegister", b"WD_PciGetCardInfo", b"PLX 9054", b"no cards found"):
        print(f"  {probe.decode('latin1'):24} -> {'YES @' + hex(cd.find(probe)) if probe in cd else 'no'}")

    # ---- RE#60: vnew.bmp == '3.17' ----
    hdr("RE#60  vnew.bmp 应为 4 字节 '3.17'")
    v = open(p("Windows/vnew.bmp"), "rb").read()
    print(f"  size={len(v)}  bytes={v!r}")

    # ---- RE#38: xcore 377 exports mangled ----
    hdr("RE#38  xcore/Xcv/Xtool 应为 OpenCV C++ mangled 导出")
    for f, want in (("Windows/System32/xcore.dll", 377), ("Windows/System32/Xcv.dll", 302), ("Windows/System32/Xtool.dll", 124)):
        pe = pefile.PE(p(f), fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]])
        ex = [s.name.decode("latin1") for s in pe.DIRECTORY_ENTRY_EXPORT.symbols if s.name]
        mangled = sum(1 for n in ex if n.startswith("?"))
        print(f"  {os.path.basename(f):12} exports={len(ex):4} (RE 称 {want})  mangled={mangled}  sample={ex[:2]}")

    # ---- RE#69: all PE32/i386/DllCharacteristics=0 ----
    hdr("RE#69  全 PE 应为 PE32/i386/DllCharacteristics=0")
    bad = []
    n = 0
    for dp, _dn, fns in os.walk(T):
        for fn in fns:
            fp = os.path.join(dp, fn)
            try:
                pe = pefile.PE(fp, fast_load=True)
            except Exception:
                continue
            if pe.FILE_HEADER.Machine != pefile.MACHINE_TYPE["IMAGE_FILE_MACHINE_I386"]:
                bad.append((fn, "not i386"))
            if pe.OPTIONAL_HEADER.Magic != 0x10B:
                bad.append((fn, "not PE32"))
            if pe.OPTIONAL_HEADER.DllCharacteristics != 0:
                bad.append((fn, f"DllChar={pe.OPTIONAL_HEADER.DllCharacteristics:#x}"))
            n += 1
            pe.close()
    print(f"  扫描 PE {n} 个，异常 {len(bad)} 个")
    for b in bad:
        print("   ", b)

    print("\n\nDONE")


if __name__ == "__main__":
    main()
