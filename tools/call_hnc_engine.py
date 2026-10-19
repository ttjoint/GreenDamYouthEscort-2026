# -*- coding: utf-8 -*-
"""直接调用原始 HNC 引擎（SentenceObj.dll）。

## 背景

`LH-installed-tree/Windows/SentenceObj.dll`（544,768 字节，x86）是
HNC 语义引擎的 C++ 实现，导出 72 个函数。本脚本试着真实调用它。

同时兼容 Python 2.7（32 位）与 Python 3，但**必须 32 位**——
DLL 是 PE32，64 位进程无法 LoadLibrary。

## 技术障碍与解法

1. **位数**：见上。本机 `D:\\Python27\\python.exe` 是 2.7-32。

2. **thiscall**：`CFWordLib`/`CSentenceObj` 的成员函数用 `__thiscall`
   （`this` 走 ECX 而非栈）。ctypes 无法表达，所以生成机器码 thunk：

       B8 <fn:u32>        mov eax, fn
       B9 <this:u32>      mov ecx, this
       FF E0              jmp eax

   用 `jmp` 而非 `call`：被调函数（`QAE` = __thiscall）末尾是 `ret n`，
   它会直接把返回地址弹给我们的调用者。这样 thunk 无需知道参数个数。

3. **词库路径**：`CreateHncTextGroup` 收**目录**不是文件。DLL 内部拼
   `%s\\FalunWord.lib`、`%s\\FMean.tmp`、`%s\\FMean.lib`、`%s\\FWL.tmp`、
   `%s\\FWL.txt`（字符串簇在文件偏移 0x45334 附近）。

## 输出原则

每个调用如实报告成功/失败，失败时保留 DLL 自己打印的错误文本。
不粉饰——原始引擎若因缺文件不能用，这本身就是本轮的结论。
"""

import ctypes
import os
import struct
import sys

# 不写中文路径字面量：Python 2 下源码 UTF-8 解码后再经 mbcs/ANSI 传
# 给 Win32 会发生二次转码，路径变成乱码。改为从脚本自身位置向上推导。
HERE = os.path.dirname(os.path.abspath(__file__))
UP2 = os.path.dirname(os.path.dirname(HERE))       # LH-setup3.17/
BASE = os.path.join(UP2, "LH-installed-tree", "Windows")
if not os.path.isdir(BASE):
    sys.exit("找不到安装树：" + BASE)
DLL_NAME = "SentenceObj.dll"
LIB_DIR = os.path.join(BASE, "HNCLIB")


def out(msg):
    sys.stdout.write(msg + "\n")


def check_bits():
    if ctypes.sizeof(ctypes.c_void_p) != 4:
        sys.exit("必须用 32 位 Python 运行（DLL 是 PE32 x86）。\n"
                 "本机可用：D:\\Python27\\python.exe")


def make_thiscall(fn_addr, this_addr):
    """生成 thiscall 调用桩，返回 (可调用地址, 持有 buffers 的列表)。

    机器码：

        B8 <fn:u32>        mov  eax, fn
        B9 <this:u32>      mov  ecx, this        ; __thiscall 的 this
        FF D0              call eax              ; 建立正常返回地址
        C3                 ret                   ; 回到 Python

    用 `call` 而非 `jmp` 的原因：这些函数带 SEH 序言
    （`push -1; push handler; mov eax,fs:[0]; push eax;
      mov fs:[0],esp`），SEH 链需要一个合法的返回地址位置。
    用 `jmp` 会把 Python 的返回地址留在栈上，帧布局错位，
    实测表现为 `access violation writing 0x...008`
    （写到 this+0x1008，越过对象边界）。

    参数个数由被调方 `ret n` 自己清（QAE = __thiscall 默认约定），
    所以这里的 `ret` 一定被执行——除非被调方是 `ret n`，那时
    它会先弹出返回地址再到我们这条 `ret` 之前……不成立。

    因此：若函数是 `ret n`（被调方清栈），本桩的 `C3` 不会执行，
    控制权直接回到 Python——这正是我们想要的。若函数是裸 `ret`
    （调用方清栈），`ret n` 不为 0 时才会落到 `C3`。
    """
    code = (b"¸" + struct.pack("<I", fn_addr)
            + b"¹" + struct.pack("<I", this_addr)
            + b"ÿÐ"          # call eax
            + b"Ã")              # ret
    buf = ctypes.create_string_buffer(code, len(code))
    old = ctypes.c_uint32()
    kern = ctypes.WinDLL("kernel32")
    kern.VirtualProtect(buf, len(code), 0x40, ctypes.byref(old))
    return ctypes.cast(buf, ctypes.c_void_p).value, [buf, old]


def to_ansi(text):
    """把路径转成 ANSI 字节串（DLL 用 char* 接路径）。"""
    if isinstance(text, type(b"")):
        return text
    if not isinstance(text, type(u"")):
        text = text.decode("mbcs")
    try:
        return text.encode("mbcs")
    except UnicodeEncodeError:
        return text.encode("gbk", "replace")


def addr_of(dll, name):
    return ctypes.cast(dll[name], ctypes.c_void_p).value


def thiscall(dll, name, this_addr, restype, *argtypes):
    """取一个成员函数的可调用对象（thiscall 已适配）。"""
    fn = addr_of(dll, name)
    code_addr, keep = make_thiscall(fn, this_addr)
    proto = ctypes.WINFUNCTYPE(restype, *argtypes)
    return proto(code_addr), keep


def main():
    check_bits()
    out("=" * 68)
    out("原始 HNC 引擎调用测试")
    out("=" * 68)
    out("Python   %d 位  %s" % (ctypes.sizeof(ctypes.c_void_p) * 8,
                                sys.version.split()[0]))
    out("DLL      " + DLL_NAME + "  @" + BASE)
    out("词库目录 " + LIB_DIR)
    out("")

    # LoadLibraryA 无法处理非 ANSI 路径（本路径含中文），所以先切工作
    # 目录再按裸文件名加载。GetShortPathNameW 在本卷上也返回 FAIL，
    # 所以只有这一条路。
    # Python 2 下 chdir 必须传 unicode，传 utf-8 bytes 会报 Error 3。
    base = BASE if isinstance(BASE, type(u"")) else BASE.decode("mbcs")
    os.chdir(base)
    dll = ctypes.CDLL(DLL_NAME)
    out("[OK] LoadLibrary 成功，基址 0x%X" % dll._handle)

    # ---------- 1. CreateHncTextGroup ----------
    out("")
    out("--- 1. CreateHncTextGroup（目录参数） ---")
    create = dll["?CreateHncTextGroup@@YAPAVIHncTextGroup@@PAD@Z"]
    create.restype = ctypes.c_void_p
    create.argtypes = [ctypes.c_char_p]
    group = create(to_ansi(LIB_DIR))
    out("  返回 %r" % (group,))
    if group:
        release = dll["?ReleaseHncTextGroup@@YAXPAVIHncTextGroup@@@Z"]
        release.restype = None
        release.argtypes = [ctypes.c_void_p]
        release(group)
        out("  [OK] 建组并释放成功 —— 原始引擎完全可用")
        return
    out("  [FAIL] 返回 NULL（DLL 上面那句 'Ini Error!' 是它自己打印的）")

    # ---------- 2. 引擎需要的文件 ----------
    out("")
    out("--- 2. 引擎需要的文件（从 DLL 字符串还原） ---")
    for name in ("FalunWord.lib", "FMean.lib", "FMean.tmp",
                 "FWL.tmp", "FWL.txt", "UserWord.lib"):
        path = os.path.join(LIB_DIR, name)
        if os.path.exists(path):
            out("  %-16s 存在    %10d 字节" % (name, os.path.getsize(path)))
        else:
            out("  %-16s **缺失**" % name)

    # ---------- 3. 直接调 CFWordLib::LoadLib ----------
    out("")
    out("--- 3. 直接调 CFWordLib::LoadLib（thiscall thunk） ---")
    # CFWordLib 构造函数实测写在 this+0x108 起（`mov [esi+0x108], ebx`），
    # 对象内部还有自己的堆/索引指针。留足 256KB 并清零。
    obj = ctypes.create_string_buffer(0x40000)
    obj_addr = ctypes.cast(obj, ctypes.c_void_p).value

    # 注意：构造函数的导出修饰名是 ??0CFWordLib@@QAE@XZ，
    # 但实际地址是 0x10003e40（不是 0x22d0——那是别的函数）。
    # 实测：导出名写着 ??0CFWordLib@@QAE@XZ（无参），但用 0 参调用时报
    # "not enough arguments"，用 call/jmp 又踩栈——说明真实签名带参数。
    # 逐个试 0/1/2/3 个整数参数，记录哪个能返回而不崩。
    ctor_ok = None
    for nargs in (0, 1, 2):
        # 每轮用干净对象，避免半构造状态
        obj = ctypes.create_string_buffer(0x40000)
        a = ctypes.cast(obj, ctypes.c_void_p).value
        args = tuple([ctypes.c_int] * nargs)
        fn, keep = thiscall(dll, "??0CFWordLib@@QAE@XZ", a, None, *args)
        try:
            if nargs == 0:
                fn()
            elif nargs == 1:
                fn(0)
            else:
                fn(0, 0)
            ctor_ok = (nargs, obj, a)
            out("  [OK] 构造成功（%d 个参数），对象 @ 0x%X" % (nargs, a))
            break
        except Exception as exc:  # noqa: BLE001
            out("  构造 nargs=%d 失败：%r" % (nargs, exc))
    if ctor_ok is None:
        out("  [FAIL] 三种参数个数都失败，放弃成员函数路线")
        out("")
        out("  这不影响主结论：CreateHncTextGroup 已经证明引擎因缺文件")
        out("  无法初始化（见上面第 2 节的文件清单）。")
        return
    nargs, obj, obj_addr = ctor_ok

    load, keep2 = thiscall(dll, "?LoadLib@CFWordLib@@QAEHPAD@Z", obj_addr,
                           ctypes.c_int, ctypes.c_char_p)
    lib_file = os.path.join(LIB_DIR, "FalunWord.lib")
    rc = load(to_ansi(lib_file))
    out("  LoadLib 返回 %r" % (rc,))
    if rc:
        out("  [OK] FalunWord.lib 加载成功")
    else:
        out("  [FAIL] FalunWord.lib 加载失败（返回 0）")

    # ---------- 4. 分词测试 ----------
    if rc:
        out("")
        out("--- 4. CutWordMax 分词测试 ---")
        try:
            cut, keep3 = thiscall(dll, "?CutWordMax@CFWordLib@@QAEHPAGHPAUCFWordPosRec@@H@Z",
                                  obj_addr, ctypes.c_int,
                                  ctypes.c_wchar_p, ctypes.c_int,
                                  ctypes.c_void_p, ctypes.c_int)
            text = u"他昨天在图书馆遇到了老朋友"
            rec = ctypes.create_string_buffer(0x4000)
            n = cut(text, len(text), rec, 256)
            out("  分词返回 %r（%d 个词）" % (n, n))
            if n > 0:
                # CFWordPosRec 结构布局未知，先 dump 原始字节供分析
                out("  原始记录前 128 字节：")
                for i in range(0, 128, 16):
                    chunk = rec.raw[i:i + 16]
                    hx = " ".join("%02x" % (ord(c) if isinstance(c, str) else c)
                                  for c in chunk)
                    out("    %04x  %s" % (i, hx))
        except Exception as exc:  # noqa: BLE001
            out("  [FAIL] 分词异常：%r" % (exc,))

        # 判定函数
        out("")
        out("--- 5. 内容判定函数探测 ---")
        for name in ("?GetWordFanDong@CFWordLib@@QAEHH@Z",
                     "?GetWordHongHei@CFWordLib@@QAEHH@Z",
                     "?GetNigWordCount@CFWordLib@@UAEHXZ",
                     "?GetRedWordCount@CFWordLib@@UAEHXZ",
                     "?GetSenseWordCount@CFWordLib@@UAEHXZ"):
            try:
                fn, keep = thiscall(dll, name, obj_addr, ctypes.c_int)
                out("  %-40s -> %r" % (name.split("@")[0][1:], fn()))
            except Exception as exc:  # noqa: BLE001
                out("  %-40s -> 异常 %r" % (name.split("@")[0][1:], exc))

        try:
            unini, keep = thiscall(dll, "?Unini@CFWordLib@@QAEXXZ", obj_addr, None)
            unini()
            out("")
            out("  [OK] Unini 返回")
        except Exception as exc:  # noqa: BLE001
            out("  Unini 异常：%r" % (exc,))


if __name__ == "__main__":
    main()
