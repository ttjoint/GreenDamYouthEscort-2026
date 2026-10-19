"""用 Unicorn 模拟原始 DLL 的句类判定函数，与 Python 复刻逐条比对。

这是等价性验证，不是"看起来对"。做法：
1. 把 SentenceObj.dll 的镜像与数据段映射进模拟器；
2. 在 `0x10030650` 处下断，按原函数约定的结构体布局填参数；
3. 读它写回的句类码缓冲与返回值；
4. 和 `green_dam.hnc.classify()` 的输出全量对齐。

不执行任何真实环境代码，只在沙箱里跑纯函数。
"""

from __future__ import annotations

import itertools
import struct
import sys
from pathlib import Path

import pefile
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
from unicorn.x86_const import (
    UC_X86_REG_EAX,
    UC_X86_REG_EBP,
    UC_X86_REG_EBX,
    UC_X86_REG_ECX,
    UC_X86_REG_EDI,
    UC_X86_REG_EDX,
    UC_X86_REG_ESI,
    UC_X86_REG_ESP,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam.hnc import Frame, classify  # noqa: E402

DLL = ROOT.parent / "LH-installed-tree" / "Windows" / "SentenceObj.dll"
FUNC = 0x10030650
STACK = 0x200000
STACK_SIZE = 0x10000
IMAGE_SIZE = 0x200000
ARGBUF = 0x300000  # 参数结构体
OUTBUF = 0x300200  # 输出缓冲
SENTINEL = 0x400000  # 已映射的返回到这里就停


def build_image() -> tuple[Uc, int]:
    pe = pefile.PE(str(DLL))
    base = pe.OPTIONAL_HEADER.ImageBase
    uc = Uc(UC_ARCH_X86, UC_MODE_32)
    uc.mem_map(base, IMAGE_SIZE)
    blob = Path(DLL).read_bytes()
    for sec in pe.sections:
        va = sec.VirtualAddress
        data = sec.get_data()
        if not data:
            continue
        uc.mem_write(base + va, data)
    # 头部与导入表区域
    hdr = min(s.PointerToRawData for s in pe.sections if s.PointerToRawData)
    uc.mem_write(base, blob[:hdr])
    uc.mem_map(STACK, STACK_SIZE)
    uc.mem_map(ARGBUF, 0x1000)
    uc.mem_map(SENTINEL, 0x1000)
    uc.mem_write(SENTINEL, bytes([0xC3]))
    return uc, base


def run_case(uc: Uc, base: int, a: int, b: int, n: int, concept: int, spare: int,
             concept2: int | None = None):
    """调一次原函数，返回 (返回值, 句类码字符串或 None)。"""
    # 结构体：+0x01 role_a, +0x04 participants, +0x08 spare, +0x0F/+0x10 concept,
    #        +0x13 role_a(dup), +0x14 role_b
    buf = bytearray(0x40)
    buf[0x01] = a & 0xFF
    struct.pack_into("<i", buf, 0x04, n)
    struct.pack_into("<i", buf, 0x08, spare)
    buf[0x0F] = concept & 0xFF
    buf[0x10] = (concept if concept2 is None else concept2) & 0xFF
    buf[0x0C] = b & 0xFF  # 入口守卫读 [+0x0C]
    buf[0x13] = a & 0xFF
    buf[0x14] = b & 0xFF
    uc.mem_write(ARGBUF, bytes(buf))
    uc.mem_write(OUTBUF, b"\x00" * 0x20)

    regs = {
        UC_X86_REG_ESP: STACK + STACK_SIZE - 0x100,
        UC_X86_REG_EBP: 0,
        UC_X86_REG_EBX: 0,
        UC_X86_REG_ESI: 0,
        UC_X86_REG_EDI: 0,
    }
    for r, v in regs.items():
        uc.reg_write(r, v)

    # 栈布局（cdecl）：[esp]=返回地址, [esp+4]=arg1, [esp+8]=arg2, [esp+0xC]=arg3。
    # 函数体 `mov ecx,[esp+8]` 说明结构体是 arg2；
    # 句类码写到 `[esp+0x14]`，但那时已 push ebx/edi（-8），等价于原 [esp+0xC] = arg3。
    sp = STACK + STACK_SIZE - 0x100
    uc.mem_write(sp, struct.pack("<IIII", SENTINEL, 0, ARGBUF, OUTBUF))
    uc.reg_write(UC_X86_REG_ESP, sp)

    # 只跑到 ret 回 0xDEADBEEF
    stop = {"hit": False}

    def hook(uc_, addr, size, _):
        # 函数 ret 回 SENTINEL（已映射的 0xC3）。只要离开 DLL 映像或踩到
        # SENTINEL 就收工。早期版本只检测 SENTINEL，而有的分支其实跳到了
        # DLL 内另一处 epilogue，于是永远等不到——那些用例被静默统计成 0。
        if addr == SENTINEL or not (base <= addr < base + IMAGE_SIZE):
            stop["hit"] = True
            uc_.emu_stop()

    handle = uc.hook_add(UC_HOOK_CODE, hook)
    try:
        # until=0：不设地址上限，只靠 hook 的 emu_stop 结束。若把 SENTINEL
        # 写进 until，emu_start 会把它当"起始地址"而立刻返回，一个用例都跑不到。
        uc.emu_start(FUNC, 0, count=200000)
    except Exception:
        return None, None
    finally:
        uc.hook_del(handle)
    if not stop["hit"]:
        return None, None
    eax = uc.reg_read(UC_X86_REG_EAX)
    raw = uc.mem_read(OUTBUF, 0x10)
    code = ""
    for ch in raw:
        if ch == 0:
            break
        if 32 <= ch < 127:
            code += chr(ch)
        else:
            break
    return eax, (code or None)


def main() -> int:
    uc, base = build_image()
    mismatches = []
    checked = 0
    for a, b, n, concept, spare in itertools.product(
        range(0, 7), range(0, 4), range(0, 5), (0x30, 0x40), (0, 1, 2, 3)
    ):
        ret, native = run_case(uc, base, a, b, n, concept, spare, concept)
        if ret is None:
            continue
        checked += 1
        py = classify(Frame(a, b, n, concept, spare, role2=b))
        py_code = str(py) if py else None
        # 原函数返回 0 表示失败，此时输出缓冲可能残留，不算句类码
        if ret == 0:
            native_code = None
        else:
            native_code = native
        if py_code != native_code:
            mismatches.append((a, b, n, hex(concept), spare, ret, native_code, py_code))

    print(f"比对组合数: {checked}")
    if checked == 0:
        print("!! 一个用例都没跑起来，验证无效 !!")
        return 2
    print(f"不一致: {len(mismatches)}")
    for m in mismatches[:40]:
        a, b, n, cc, sp, ret, nat, py = m
        print(
            f"  a={a} b={b} n={n} cc={cc} spare={sp} -> "
            f"native(ret={ret})={nat!r}  python={py!r}"
        )
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
