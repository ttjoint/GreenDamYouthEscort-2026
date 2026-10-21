# 04 — ENTRYPOINTS（入口 / 出口 / 接口参数）

一行 = 一个函数。`evidence` 列必须指向 `probe/` 下可重跑命令或 raw 文件行号。

---

## A. `IPGate.dll` — 已完整还原（`method=DISASM`，CLOSED）

**全部证据来自**：`probe/p03_exports.py` 导出表 + 直接反汇编（见本文附录命令）。

### 导出接口

| 导出 | RVA | 签名（由反汇编推断） | 证据 |
|---|---|---|---|
| `Start` | `0x21e0` | `int Start(int i)`，`ret 4` | CLOSED |
| `SetParam` | `0x2130` | `void SetParam(void* p9dwords)`，`ret 4` | CLOSED |
| `GetLogBuf` | `0x2150` | `void GetLogBuf(char* out)`，`ret 4` | CLOSED |

### `Start` @ `0x100021e0` 逐指令

```
0x100021e0  call 0x10002180            ; 幂等守卫（见下）
0x100021e5  test eax,eax
0x100021e7  jne  0x10002244            ; 已启动 → 返回
0x100021e9  mov  eax,[esp+4]           ; arg i
0x100021ef  mov  eax,[0x10015000]      ; g_hHook
0x100021f4  je   0x1000222f            ; i==0 分支
0x100021f6  test eax,eax
0x100021f8  jne  0x10002244            ; 已有 hook → 返回
0x100021fd  push 0x100021e0            ; ← hook 所在模块基址/自身
0x10002202  call 0x100010e0            ; = VirtualQuery 包装
0x1000220a  push eax                   ; hMod
0x1000220b  push 0x100010c0            ; ← lpfn 钩子过程
0x10002210  push 3                     ; WH_GETMESSAGE
0x10002212  call [0x10010118]           ; SetWindowsHookExA
0x10002218  mov  [0x10015000],eax      ; 保存句柄
0x1000221d  mov  ecx,0x14a
0x10002224  mov  edi,0x1000a000        ; 清 0x14a*4 = 1320 字节缓冲
0x10002229  rep stosd
0x1000222c  ret  4
```

**结论（CLOSED）**：
1. `Start` 装的是 `WH_GETMESSAGE(3)` 全局钩子，处理过程 `0x100010c0`。
2. 钩子模块句柄由 `VirtualQuery(0x100021e0)` 求得（自定位，非 `GetModuleHandle`）。
3. 启动后清零 `0x1000a000` 处 **1320 字节**（`0x14a` dwords）缓冲 —— 即日志缓冲。

### 钩子过程 `0x100010c0` —— 空钩子（CLOSED）

```
0x100010c0  mov eax,[esp+0xc]    ; lParam
0x100010c4  mov ecx,[esp+8]      ; wParam
0x100010c8  mov edx,[esp+4]      ; nCode
0x100010cc  push eax
0x100010cd  mov eax,[0x10015000] ; g_hHook
0x100010d2  push ecx
0x100010d3  push edx
0x100010d4  push eax
0x100010d5  call [0x10010138]    ; CallNextHookEx
0x100010db  ret 0xc
```

**纯透传。** 与旧文档一致，现为 `CLOSED`。→ 钩子**不参与**文字截获。
其唯一作用是**把 DLL 注入到目标进程**，真正工作在进程内的 inline hook。

### `SetParam` @ `0x10002130`（CLOSED）

```
0x10002130  push esi / mov esi,[esp+8]
0x10002136  mov ecx,9              ; ← 9 个 dword
0x1000213b  mov edi,0x1000a528
0x10002140  rep movsd              ; 拷贝 36 字节参数块
0x10002142  call 0x10001ea0
0x10002148  xor eax,eax / ret 4
```

**参数块 = 36 字节 / 9 dword**，落在 `0x1000a528`。
紧随其后 `0x1000a54c` 是 `GetLogBuf` 的源串 —— 两块首尾相接（`0x528 + 36 = 0x54c`）。

### `GetLogBuf` @ `0x10002150`（CLOSED）

`strlen(0x1000a54c)`（`repne scasb`）→ `memcpy(out, 0x1000a54c, len+1)`，`ret 4`。
即：**取日志文本缓冲**。

---

## B. `IPGate.dll` 内联 Hook 引擎（`method=DISASM`，CLOSED）

**这是本模块的真正工作机理，旧文档只说了结论没说机制。**

### 安装/卸载函数对

| 函数 | RVA | 作用 |
|---|---|---|
| 安装 | `0x100023c0` | 保存原字节 → `VirtualProtect(0x40)` |
| 卸载/还原 | `0x10002440` | 恢复原字节 |

```
0x100023c0  install(ctx, target, patchBuf):
  ctx[+4]=target ; ctx[+8]=patchBuf ; ctx[+0xc]=0
  call [GetCurrentProcess]                    ; 0x10010054
  ctx[+0]=<进程句柄>
  if (ctx[+4]==0 || ctx[+8]==0) return
  call [FlushInstructionCache](proc, target, len)   ; 0x10010050
  if (!eax) return
  call [VirtualProtect](target, len, 0x40, &ctx[+0xc])  ; 0x1001004c
```

`0x40` = `PAGE_EXECUTE_READWRITE`。这是**教科书式 inline patch 安装器**
（与 Microsoft Detours / MinHook 同构，但为手写）。

### 目标函数如何取得

GDI 四函数**不是通过 `FF 15` 直接调用**，而是以三种形态出现：

| 形态 | RVA | 代码 |
|---|---|---|
| 取原址 thunk | `0x10001050` | `mov eax,[0x10010004]; ret`（`ExtTextOutW` 槽） |
| 取原址 thunk | `0x10001080` | `mov eax,[0x10010000]; ret`（`TextOutA` 槽） |
| 导入尾跳 | `0x10003144` | `jmp dword ptr [0x10010110]`（`SG_Control` 槽） |

**这正是「导入但不调用」的真实形态**：`TextOutA/W`、`ExtTextOutA/W` 的 IAT 槽只被
**thunk 读取**（为了拿到原函数地址送去 `VirtualProtect` 打补丁），没有任何一处把它们
当函数调用。旧文档结论方向正确，但「全文件零调用」这一表述会让人误以为 IAT 无用 ——
实际 IAT 槽是**补丁目标地址的来源**。

### 上报出口（CLOSED）

`IPGate.dll` 导入 `SurfGd.dll!SG_Control`，IAT 槽 `0x10010110`，
以 `jmp dword ptr [0x10010110]`（`0x10003144`）尾跳调用。
→ 与 `06-LINKS` 的 `IPGate → SurfGd` 边互相印证。

### 导入表给出的完整能力面（`method=IMP`，CLOSED）

- 进程操作：`OpenProcess` `TerminateProcess` `GetWindowThreadProcessId` `EnumThreadWindows`
- 窗口识别：`GetClassNameA` `FindWindowExA` `GetParent` `SendMessageA`
- 代码补丁：`VirtualProtect` `FlushInstructionCache` `VirtualQuery` `LoadLibraryA` `GetProcAddress`
- 编码转换：`WideCharToMultiByte` `MultiByteToWideChar` `LCMapStringA/W` `GetStringTypeA/W`
- 日志落盘：`CreateFileA` `WriteFile` `SetFilePointer` `SetEndOfFile` `FlushFileBuffers`
- 计时：`WINMM!timeGetTime`

**编码转换 + 字符串分类 API 的存在**，支持「截获文本后做字符级判定」的定性。

---

## C. 待办

| 模块 | 目标 | 方法 |
|---|---|---|
| `dbfilter.dll` | `WSPStartup@0x13b0` 参数与 SPI 表结构 | DISASM |
| `dbfilter.dll` | `DBFilterInstall@0x1bb0` 注册表写入序列 | DISASM |
| `Surfgd.dll` | `DBFP_*` 8 个函数的参数 | DISASM |
| `HSrv.dll` | `InstallApiHook@0x11c0` 与 IPGate 引擎是否同源 | DISASM 对比 |
| `Handler.dll` | `TransmitString@0x3380` 参数 | DISASM |
| `MPSvcDll.dll` | `FilterWords@0x1440` 参数与真实行为 | DISASM |

---

## 附录：本文证据的生成命令

```bash
# 导出/导入全表
PYTHONIOENCODING=utf-8 python ANALYSIS/probe/p03_exports.py > ANALYSIS/05-INTERFACES-RAW.txt

# 单个函数反汇编（示例）
PYTHONIOENCODING=utf-8 python -c "
import pefile
from capstone import *
pe=pefile.PE('LH-installed-tree/Windows/System32/IPGate.dll')
data=pe.get_memory_mapped_image(); ib=pe.OPTIONAL_HEADER.ImageBase
for i in Cs(CS_ARCH_X86,CS_MODE_32).disasm(data[0x21e0:0x2230], ib+0x21e0):
    print(hex(i.address), i.mnemonic, i.op_str)
"
```
