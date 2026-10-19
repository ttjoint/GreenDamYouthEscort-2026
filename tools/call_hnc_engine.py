# -*- coding: utf-8 -*-
"""在真实进程里加载并调用原始 HNC 引擎（SentenceObj.dll）。

**必须用 32 位 Python 2.7**：`D:\\Python27\\python.exe tools/call_hnc_engine.py`

为什么是 Python 2.7：DLL 是 PE32 x86，64 位进程加载会直接拒绝。
Python 2 还有额外好处——`argtypes` 不严格，便于试探签名。

## 已经确认的事实（见 docs/hnc-engine-probe.md）

1. `CreateHncTextGroup(path)` 的 path **必须带尾反斜杠**：

       "D:\\...\\HNCLIB"    -> NULL + "Ini Error!"
       "D:\\...\\HNCLIB\\\\"   -> 非零指针

   因为内部是 `sprintf(buf, "%s\\\\FalunWord.lib", path)`。

2. 返回的是**子对象指针** `g`，主对象在 `g - 0x124`（记作 `global`）。
   `CorpGroup` / `MakeALC` 内部会做 `add ecx, -0x124`，
   所以调用时要传 `g`，它们自己回退到 `global`。

3. 对象分配大小 0x101000（1,052,672 字节）。

4. `?CorpGroup@...`（vtable[6] = 0x2df0）只是转发到
   `?MakeALC@CFWordLib@@QAEHPAG0_NPAH@Z`（0x28d0）。

5. **`MakeALC` 是逐 token 的，不是整句入口。** 反汇编它的全部
   返回点，返回码是：

       @0x2c64  return 1   整句处理完成
       @0x2c21  return 7   单个 token 处理完，累加 [ebp+0x10047c] 后返回
       @0x2c05  return 2
       @0x2c37  return 4
       @0x2d45/0x2d4d  return -1   失败

   所以我们看到的一堆 `ret=7` 不是错误，是"处理了一个 token"。
   但它在外层循环里只被调用一次就返回了，说明缺一个驱动整句的
   循环骨架——那个骨架在 `CSentenceObj` 里。

6. `?GetALC@@YAHPAG0_N@Z`（0x4e70）是全局包装：先查
   `[0x100B31E8]`，非空才往下走，否则 `mov eax,7 / ret`。
   实测它抛 C++ 异常（0xE06D7363），内部依赖尚未就绪。

## 结论

**原始引擎可以加载、可以初始化、可以取出接口对象、可以调用
无参数的计数函数**。但要「喂一段文本、拿回块结构」，需要
`CSentenceObj::GetSentenceALC`（0x6dc0），而它要一个已 `Ini`
过的 `CSentenceObj` 实例（`?Ini@CSentenceObj@@QAEHPAD@Z`，0x68d0），
那条构造链还没打通。

**这一步没突破之前，不能把原始引擎当分析后端用。**
本工具的作用是证实引擎可运行，并保留继续探测的脚手架。
"""

from __future__ import print_function

import ctypes
import os
import shutil
import struct
import sys
import tempfile

# 引擎必需的两样东西，源码树里各在别处，统一拷到临时目录跑。
HERE = os.path.dirname(os.path.abspath(__file__))
CANDIDATE_BASES = (
    os.path.join(HERE, "..", "..", "LH-installed-tree", "Windows"),
    os.path.join(HERE, "..", "LH-installed-tree", "Windows"),
)

# 导出符号（都带 MSVC 修饰名）。
SYM_CREATE = "?CreateHncTextGroup@@YAPAVIHncTextGroup@@PAD@Z"
SYM_GETALC = "?GetALC@@YAHPAG0_N@Z"

# 全局 CFWordLib* 所在位置（CreateHncTextGroup 里 a3 ... 即 mov [0x100B31E8], eax）
OFF_GLOBAL_SLOT = 0xB31E8

# 子对象相对主对象的位移。
SUB_OBJ_DELTA = 0x124


def _find_engine_dir():
    for base in CANDIDATE_BASES:
        base = os.path.abspath(base)
        dll = os.path.join(base, "SentenceObj.dll")
        lib = os.path.join(base, "HNCLIB", "FalunWord.lib")
        if os.path.isfile(dll) and os.path.isfile(lib):
            return base
    return None


class HncEngine(object):
    """把引擎摆到临时目录、加载、建组。

    拷贝到临时目录不是为了整洁，是为了避开中文路径：
    `LoadLibraryA` 处理不了非 ASCII 路径，而仓库路径含中文。
    """

    def __init__(self, source_dir=None):
        self.source_dir = source_dir or _find_engine_dir()
        if not self.source_dir:
            raise RuntimeError("找不到 SentenceObj.dll / HNCLIB/FalunWord.lib")
        self.tmpdir = tempfile.mkdtemp(prefix="hnceng_")
        shutil.copy(os.path.join(self.source_dir, "SentenceObj.dll"), self.tmpdir)
        os.mkdir(os.path.join(self.tmpdir, "HNCLIB"))
        shutil.copy(
            os.path.join(self.source_dir, "HNCLIB", "FalunWord.lib"),
            os.path.join(self.tmpdir, "HNCLIB"),
        )
        self._oldcwd = os.getcwd()
        os.chdir(self.tmpdir)
        self.dll = ctypes.CDLL("SentenceObj.dll")
        self.base = self.dll._handle
        self.group = None

    # ---- 生命周期 ----

    def create(self, trailing_backslash=True):
        """建组。trailing_backslash=False 用来复现 "Ini Error!"。"""
        create = self.dll[SYM_CREATE]
        create.restype = ctypes.c_void_p
        create.argtypes = [ctypes.c_char_p]
        path = os.path.join(self.tmpdir, "HNCLIB")
        if trailing_backslash:
            path += "\\"
        self.group = create(path)
        return self.group

    def global_obj(self):
        """主对象指针（= group - 0x124）。"""
        ptr = ctypes.cast(
            self.base + OFF_GLOBAL_SLOT, ctypes.POINTER(ctypes.c_void_p)
        )[0]
        return ptr

    def close(self):
        try:
            os.chdir(self._oldcwd)
        except OSError:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # ---- 探测：直接调内部函数 ----

    def call_raw(self, fn_addr, this, args):
        """用一个手写 asm thunk 调 `__thiscall`。

        为什么不用 CFUNCTYPE：目标函数以 `ret 0x10`（被调方弹栈）结尾，
        ctypes 仍会按 cdecl 再弹一次，栈立刻失衡，报
        `access violation writing 0x00000007` 这类假崩溃。

        thunk = 依次 push 参数 -> mov ecx,this -> mov eax,fn -> call eax -> ret
        参数从右往左 push，返回后不额外弹栈（交给被调方的 ret N）。
        """
        buf = ctypes.create_string_buffer(512)
        code = b""
        for value in reversed(args):
            code += b"\x68" + struct.pack("<I", value)      # push imm32
        code += b"\xB9" + struct.pack("<I", this)           # mov ecx, this
        code += b"\xB8" + struct.pack("<I", fn_addr)        # mov eax, fn
        code += b"\xFF\xD0"                                 # call eax
        code += b"\xC3"                                     # ret
        ctypes.memmove(buf, code, len(code))

        kern = ctypes.WinDLL("kernel32")
        old = ctypes.c_uint32()
        kern.VirtualProtect(buf, len(code), 0x40, ctypes.byref(old))

        fn = ctypes.CFUNCTYPE(ctypes.c_int)(
            ctypes.cast(buf, ctypes.c_void_p).value
        )
        return fn()

    def get_alc(self, text, flag=False):
        """调全局 ?GetALC（0x4e70）。

        返回 7 表示「全局 wordlib 未初始化」，是状态码不是分析结果。
        """
        getalc = self.dll[SYM_GETALC]
        getalc.restype = ctypes.c_int
        getalc.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_bool]
        return getalc(text, u"", flag)

    def make_alc(self, text):
        """调 ?MakeALC（0x28d0，CorpGroup 真正实现）。

        实测：文本 ≤7 字正常返回，≥8 字越界读崩溃。
        这个上限不是我们的错，是引擎内部缓冲区边界。
        """
        out1 = ctypes.c_int(0)
        out2 = ctypes.c_int(0)
        sbuf = ctypes.create_unicode_buffer(text)
        ebuf = ctypes.create_unicode_buffer(u"")
        # 注意：传主对象（global），不是建组返回的子对象。
        # 传子对象会让 MakeALC 内部 -0x124 后越界，报
        # "access violation writing 0x10"。
        this = self.global_obj()
        try:
            ret = self.call_raw(
                self.base + 0x28D0,   # 必须加实际基址，不能硬编码 0x100028D0
                this,
                [
                    ctypes.addressof(sbuf),
                    ctypes.addressof(ebuf),
                    ctypes.addressof(out1),
                    ctypes.addressof(out2),
                ],
            )
            return {"ret": ret, "out1": out1.value, "out2": out2.value}
        except Exception as exc:   # WindowsError / ArgumentError 都兜住
            return {"ret": None, "error": str(exc)[:60],
                    "out1": None, "out2": None}


def main(argv):
    print("Python:", sys.version.split()[0], "(%d-bit)" % (8 * struct.calcsize("P")))
    if struct.calcsize("P") * 8 != 32:
        print("必须是 32 位 Python（DLL 为 PE32 x86）")
        return 2

    src = _find_engine_dir()
    if not src:
        print("找不到引擎文件")
        return 2
    print("引擎目录:", src)
    print()

    eng = HncEngine(src)
    try:
        # 1) 复现 Ini Error!
        print("== 1. 路径少尾反斜杠 ==")
        print("   create(无尾 \\) -> %r" % (eng.create(trailing_backslash=False),))
        print()

        # 2) 正常建组
        print("== 2. 路径带尾反斜杠 ==")
        g = eng.create()
        print("   create(带尾 \\) -> %#x" % (g or 0))
        print("   global(= group-0x124) -> %#x" % (eng.global_obj() or 0))
        print()

        if not g:
            print("建组失败，后续跳过")
            return 1

        # 3) 计数接口
        print("== 3. 无参计数接口 ==")
        for off, name in (
            (0x100350, "GetSenseWordCount"),
            (0x100354, "GetNigWordCount"),
            (0x100358, "GetRedWordCount"),
        ):
            val = ctypes.cast(
                eng.global_obj() + off, ctypes.POINTER(ctypes.c_uint32)
            )[0]
            print("   %-18s = %d" % (name, val))
        print()

        # 4) GetALC
        print("== 4. 全局 ?GetALC ==")
        for text in (u"\u4ed6\u6628\u5929", u"\u6211\u8981\u6740\u4e86\u4f60"):
            try:
                print("   ret = %r" % (eng.get_alc(text),))
            except Exception as exc:
                print("   ERR %s" % (str(exc)[:60],))
        print()

        # 5) MakeALC 的返回码
        print("== 5. ?MakeALC 返回码（7 = 处理完一个 token，不是错误）==")
        for n in range(2, 10):
            r = eng.make_alc(u"\u4ed6" * n)
            note = ""
            if r.get("ret") == 7:
                note = "  token-processed"
            elif r.get("error"):
                note = "  " + r["error"][:40]
            print("   len=%2d -> %r%s" % (n, r.get("ret"), note))
        print()
    finally:
        eng.close()

    print("结论：引擎可加载、可初始化、可接受调用；")
    print("      但没有可直接「喂文本拿块结构」的导出入口。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
