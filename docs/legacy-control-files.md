# 原厂控制文件分类与 `.urf` 定性

这份文档只讲**结构**，不复述任何条目原文。可复现命令：

```bash
python tools/audit_control_files.py --legacy ../LH-installed-tree \
    --json data/legacy-control-files.json
python tools/disasm_urf_loader.py ../LH-installed-tree/Windows/System32/XNet2.exe
python tools/disasm_urf_loader.py ../LH-installed-tree/Windows/System32/gn.exe
```

## 1. 一个容易踩的坑：`System32\` 里混了三种载荷

以前把 `System32\*.dat` 一律当成「混淆词表」，这是错的。
实际有三种完全不同的东西：

| 类别 | 文件 | 编码 | 判据 |
|---|---|---|---|
| A. 混淆词表/规则 | `adwapp` `adwfil` `iawfil` `lgwfil` `vgamfil` `wfile` `TrustUrl` | 64 符号 2:1 | 所有字节 ∈ `0x30..0x6F`（换行除外） |
| B. 裸 `uint32` 控制结构 | `sys.dat` `sysEx.dat` `xconfigs.dat` | **无编码** | `len % 4 == 0`，值都很小 |
| C. 明文策略表 | `filtport.dat` | **无编码** | 全 ASCII，含 `/` 与 `CRLF` |

### B 类的陷阱

`sys.dat`（268 字节 = **67** 个 little-endian `uint32`，最大值 14）
如果硬塞给 `legacy_wordlist_codec.decode_text`，**它不会报错**，
而是输出 134 个 `<` 字符。

这就是「解码成功 ≠ 是混淆文本」的直接反例，已经写进
`tests/test_legacy_control_files.py::test_codec_on_sys_dat_produces_pure_garbage`。

`sys.dat` 与 `xconfigs.dat` **逐字节相同**（同一份 67 槽策略表），
`sysEx.dat` 是 8 个 `uint32`，首字 `0x0004015E` 看着像版本号
(`1,4,350`)，后 6 个字全是 `1`，末字 `4`、`0`。

这解释了 `data/legacy-imported/policy.json` 为什么是 `67 slots` +
`sysEx.slot_count=8` —— 槽位来自 `sys.dat`，`sysEx` 是另一张 8 项表。

### C 类

`filtport.dat` 33 字节 / 2 行，格式固定三段：

```
名字 / 端口 / 协议
```

两行的「名字」都是第三方代理工具名，「协议」都是 `tcp`。
也就是说 **L4 层（Winsock 劫持）自带一份端口黑名单**，
它和网址表、词表完全无关。这两行被 `filtport.Dat`（大写 D）
这个文件名引用，加载点在 `Surfgd.dll`。

## 2. `Surfgd.dll` 声明了而包里没带的表

`Surfgd.dll` 的 `.data` 里有一组相邻的文件名：

```text
0x10017810  wfile.dat
0x10017838  TrustUrl.Dat
0x10017848  uFWORD.dat
0x10017854  uTUrl.Dat
0x10017860  uFUrl.Dat
0x1001786c  filtport.Dat
0x1001787c  filtpros.Dat
0x10017890  xconfigs.Dat
```

安装树里有的：`wfile.dat`、`TrustUrl.Dat`、`filtport.Dat`、`xconfigs.Dat`。
**没有的**：`uFWORD.dat`、`uTUrl.Dat`、`uFUrl.Dat`、`filtpros.Dat`。

另外 `FiltUrl.Dat` 和 `server.dat` 也是 `Surfgd.dll` 里出现、
包里缺席的文件名。

所以「缺文件」不是 HNC `.lib` 独有现象：**产品自己的网址过滤表也缺了
4 张**。这些文件很可能是安装后联网更新下发的
（对应 UI 里的 `Auto Report New Found bad Sites To KingWay's Data Center`），
出厂包里本来就不带。

### 2.1 `Surfgd.dll` 的加载点与类型码

`0x1000845a` 起是一串 `0x10007f77(name, type, flag_a, flag_b)` 调用。
`0x10007f77` 的动作是：`GetSystemDirectoryA` → `strcat("\\")` →
`strcat(name)` → `fopen(.., "rb")` → 分配缓冲 → 读。

| 顺序 | 文件 | 类型码 | `flag_a` | `flag_b` |
|---|---|---|---|---|
| 1 | `TrustUrl.Dat` | `0x65` | 1 | 0 |
| 2 | `uFWORD.dat` | `0x64` | 0 | 1 |
| 3 | `uTUrl.Dat` | `0x65` | 0 | 1 |
| 4 | `uFUrl.Dat` | `0x64` | — | — |
| 5 | `filtpros.Dat` | `0x64` | — | — |
| 6 | `xconfigs.Dat` | `0x64` | — | — |

`0x64` 与 `0x65` 是**同一套加载例程的两个分支**，不是两个不同的解析器。
`flag` 两个参数控制"读失败时是否静默"和"表为空时是否仍保留槽位"，
但我们还没有把这两个开关的行为差异量化，所以这里只记名字不记语义。

注意 `0x10008414` 处还用到格式串 `"%d %s %s"`
（`0x1022282c`）。这是**带参数的记录行**格式，说明这些 `.dat`
里有至少一种横式（同一行三个字段）。哪张表用它，**没查**。

## 2.2 同一个文件名，两处不同的目录

`Handler.dll` 找的是 `<Windows>\hnclib\`，`Surfgd.dll` 找的是
`<Windows>\sysdir\`。两边都提到 `TrustUrl.Dat` 和 `uFWORD.dat`，
但**不是同一个文件**：

| 宿主 | 目录 | 文件 | 用途 |
|---|---|---|---|
| `Surfgd.dll` | `<Windows>\sysdir\` | `TrustUrl.Dat` | 网址过滤（出厂自带，28,636 B） |
| `Handler.dll` | `<Windows>\hnclib\` | `uFWORD.dat` → 失败时退到 `TrustUrl.Dat` | 标题/窗口白名单 |

`hnclib\` 目录在出厂包里只有 `FalunWord.lib`（`ls hnclib/` 只有一项），
所以 `Handler.dll` 的这条加载**必然失败**，两个列表都是空的。
`0x31007c00` 拿着两个空列表做匹配，恒返回 0 —— 也就是
"没有任何标题被信任放行"。

这对复现很重要：原厂这条白名单通路在默认安装下是**失效**的。

## 2.3 两个匹配函数的真实语义

`Handler.dll` 里有三个长得像字符串比较的例程。判它们的语义要分两层，
混在一起就会得出自相矛盾的结论：

| 层 | 问题 | 答案 |
|---|---|---|
| 函数行为 | `0x310082f0` 返回什么？ | `haystack` 里的**匹配位置指针**，找不到返回 0 |
| 调用者用法 | 调用点拿返回值做什么？ | 只 `test` 非零，**没有一处解引用** |

所以正确的说法是：**内层做子串查找，外层折成命中/不命中。**
单说"整串比较"是错的；单说"只判相等"在调用者层不算错但不完整，
因为决定语义的是子串而非相等。

### `0x310082f0` 是 `strstr`

```c
char *sub_310082f0(char *haystack, char *needle);  // __cdecl
```

参数读法：入口 `esp+4` 是 haystack、`esp+8` 是 needle
（`0x310082f0` 的 `mov ecx,[esp+8]` 取 needle；`0x310082f9` 三次 `push` 后
`[esp+0x10]` = `esp+4` = haystack）。

**定性的关键证据是空 needle 的行为**：`0x310082ff je 0x3100836a`，
而 `0x3100836a` 是 `mov eax,edi; ret`——直接把 haystack 指针返回。
实测 `strstr("hello","")` 返回 `DATA` 基址本身。

`strcmp` 在空 needle 时只会返回"不相等"的常数，绝不会返回 haystack 内部指针。
这条单独就把函数类别定死了，不依赖任何推断。

其余路径：

| 位置 | 条件 | 返回 |
|---|---|---|
| `0x3100836a` | needle 为空 | haystack 起点 |
| `0x31008363` | 找到匹配 | `edi-1`，即匹配起点指针 |
| `0x31008357` | needle 单字符 | 转 `strchr`（`0x3100a6e6`） |
| `0x31008327` | 未找到 | 0 |

`0x3100a6e6` 是 `strchr` 的字长优化实现（`0x7efefeff` / `0x81010100`
是逐字检测零字节的位技巧），找到时 `0x3100a6d0 lea eax,[edx-1]; ret`
同样返回**指针**而不是布尔。

### `0x31007c00` — 两条列表，任一子串命中即返回 1

```c
int sub_31007c00(HandlerCtx *this, char *text);
```

- `text` 为空 → 返回 0；
- 先扫 `[this+0x14]`，计数 `[this+0x18]`（`0x31007c1c`）；
- 再扫 `[this+0xc]`，计数 `[this+0x10]`（`0x31007c53`）；
- 任一列表项是 `text` 的子串 → 返回 1（常量 1，不是指针）。

`TrustUrl.Dat` 写的是 `[+0xc]` / `[+0x10]`（`0x3100789f`），
正好就是这里扫的第二条列表。`uFWORD.dat` 写的是 `[+4]`，
而这里扫的第一条列表在 `[+0x14]` / `[+0x18]`，两处偏移不同名，
**中间可能还有一次搬移，没查到**。所以只能确定
"`TrustUrl.Dat` 就是第二条列表"，第一条列表的内容来源
只能说是"`uFWORD.dat` 读出来的东西"，不能断言偏移对应关系。

### `0x31007b70` — 单条列表，外加协议前缀排除

```c
int sub_31007b70(HandlerCtx *this, char *text);
```

- `text` 为空 → 返回 0（`0x31007b7c`）；
- 读 `[this+8]` 作计数、`[this+4]` 作数组（`0x31007b8c` / `0x31007b95`）；
- 每个非空项：
  - `strstr(text, "http://")` 命中 → **返回 0**（`0x31007bab`）
  - `strstr(text, "ftp://")` 命中 → **返回 0**（`0x31007bbd`）
  - `strstr(text, 项)` 命中 → **返回 1**（`0x31007bd7`）
- 循环结束 → 返回 0。

两个协议检查写在循环体内（`0x31007b9d` 判空之后），所以
**列表为空时整段跳过、直接返回 0**。这一点对默认安装很关键：
`hnclib\` 没有表，这个函数恒返回 0。

### 复刻时的实际差别

子串匹配和整串相等在短词条上差别很大：列表项只有两三个字符时，
子串匹配会命中任何**包含**它的更长串。按整串相等复刻，
同样的表会宽松得多。这是语义差异，不是措辞问题。

### 两个调用点的作用不同

| 调用点 | 传入 | 非 0（信任命中） | 0（不信任） |
|---|---|---|---|
| `0x31004865` | `[ebx+0x10]` | 跳到 `0x3100492b` | 转宽字符后问 `0x31007c00` |
| `0x31004e00` | `[esi+0x10]` | 跳到 `0x31004e37`，**绕过 `CorpGroup`** | 调 `CorpGroup` 做词库扫描 |

`0x31004e00` 这条是 §1.6 那条判定链的入口。所以完整顺序是：

```
前台进程 PID 匹配
  -> 0x31007b70   信任标题？ 是 -> 直接进拦截流程，不查词库
                             否 -> 0x31007340 -> CorpGroup
                                   -> 返回 0 -> 摘链释放（放行）
                                   -> 返回 1/2 -> 进拦截流程
```

信任列表为空时，第一步恒为"否"，所以**每次都会走到 `CorpGroup`**。
`0x310049bc` 那处对 `0x31007c00` 的调用同理，恒返回 0。

## 3. `.urf` 加载链：两个宿主，同一套调用约定

以前只知道 `XNet2.exe` 有个 `EnsureUrfReady`（0x421D00）。
本轮新增证据：**`gn.exe` 里也有一份同构的加载器**，而且两者参数顺序、
名字串、标志位完全一致。

### `XNet2.exe` @ 0x421D00

```text
0x421d2d  mov  eax, [0x46f058]        ; g_urfIndex
0x421d32  cmp  eax, 1   / jl  -> 1
0x421d37  cmp  eax, 9   / jle -> keep
0x421d3c  mov  eax, 1
0x421d4a  lea  edx, [esp+0x114]       ; path buffer
0x421d51  push 0x46a8fc               ; "%s\\%d.urf"
0x421d57  call 0x42cd2a               ; sprintf
0x421d5c  mov  eax, [ebx+0xc8]        ; AppFace.dll!SkinStart
0x421d69  push 0                      ; reserved
0x421d6b  push 0                      ; out
0x421d6d  push 1                      ; flag
0x421d6f  push 0x469080               ; 32-letter skin password
0x421d7b  push 0                      ; ctx
0x421d7d  push ecx                    ; path
0x421d7e  call eax
```

### `gn.exe` @ 0x40B4F0

```text
0x40b50c  call [0x43e338]             ; GetWindowsDirectoryA(buf512)
0x40b519  call 0x40b580               ; 读 PARAM, 默认 10, 钳 10..30
0x40b51e  mov  eax, [0x451d10]        ; index
0x40b523  cmp  eax, 1   / jl  -> 1
0x40b528  cmp  eax, 8   / jle -> keep
0x40b52d  mov  eax, 1
0x40b53f  push 0x44d3c0               ; "%s\\%d.urf"
0x40b545  call 0x41978e               ; sprintf
0x40b54a  mov  eax, [esi+0xc8]        ; AppFace.dll!SkinStart
0x40b559  push 0
0x40b55b  push 0
0x40b55d  push 1
0x40b55f  push 0x44d39c               ; 32-letter skin password
0x40b56b  push 0
0x40b56d  push ecx
0x40b56e  call eax
```

### 归纳出来的签名（两个宿主一致）

```c
int __cdecl SkinStart(void *ctx,         // 总是 0
                      const char *path,  // "%s\\%d.urf"
                      int flag,          // 总是 1
                      const char *name,  // 32 字母皮肤口令
                      void *out,         // 总是 0
                      int reserved);     // 总是 0
```

回到 `tools/disasm_urf_loader.py`，它输出的 `loader call prologue`
就是上面这六个 push 的机械化提取版本：

```text
$ python tools/disasm_urf_loader.py .../gn.exe --json /tmp/gn.json
loader call prologue: {"flag": {...1}, "name": {"sha1_8": "322b599b"}}
```

`XNet2.exe` 给出的 `sha1_8` 与 `gn.exe` **相同**，
也就是说这是同一把皮肤口令，不是各模块自己的常量。

### `call [esi+0xc8]` 的来源（`gn.exe` 构造函数 0x40a8b0）

```text
0x40a8de  call [0x43e32c]        ; LoadLibraryA
0x40a8f8  push 0x44d338          ; "SkinStart"
0x40a8fe  call edi               ; GetProcAddress
0x40a902  mov  [esi+0xc8], eax   ; ← SkinStart 存到 +0xc8
0x40a914  push 0x44d32c          ; "SkinStartW"    → +0xcc
0x40a930  push 0x44d320          ; "SkinRemove"    → +0xd0
0x40a94c  push 0x44d310          ; "SkinWindowSet" → +0xd4
0x40a964  push 0x44d304          ; "SkinThread"    → +0xd8
0x40a97c  push 0x44d2f8          ; "BkCreate"      → +0xdc
0x40a994  push 0x44d2f0          ; "BkDraw"        → +0xe0
0x40a9ac  push 0x44d2e4          ; "BkDelete"      → +0xe4
0x40a9be  mov  [esi+0xc0], 1     ; 初始化完成标志
```

### 三条可直接写进文档的结论

1. **加载器不是直接 `call`，是 `call [host+0xc8]`。**
   以前「全文搜不到 `call` 引用，所以没人调用」的推断是错的：
   函数指针存在宿主对象偏移 `0xc8` 上，靠虚化调用。
2. **两个宿主的索引钳制范围不同**：`XNet2.exe` 是 `1..9`，
   `gn.exe` 是 `1..8`。安装包只有 7 个 `.urf`，说明第 8、9 号是
   预留的皮肤方案槽位。
3. **`.urf` 的 magic `5599259b` 不在宿主里。**
   两个 EXE 全文都没有这 4 个字节 —— 解析逻辑在 `appface.dll` 内部，
   不在 UI/宿主进程里。

## 4. `.urf` 是什么：AppFace 皮肤资源包

**结论先行，不必再推导**：`1.urf`–`9.urf` 是 Matinsoft
**AppFace UI Development Kits 2.9.2.3** 的皮肤资源包。
它们**不是**过滤规则库，**不是**任何形式的加密文件，
与关键词、网址分类、词表全无关系。
这一条是定论，后续任何分析都不应再把 `.urf` 放进过滤层。

判定依据（每一条都可独立复核）：

1. `call [host+0xc8]` 的函数指针来自
   `LoadLibraryA("AppFace.dll")` + `GetProcAddress("SkinStart")`。
   `+0xc4` 存模块句柄，`+0xc8..+0xe4` 依次是
   `SkinStart / SkinStartW / SkinRemove / SkinWindowSet / SkinThread /
   BkCreate / BkDraw / BkDelete` —— 这是 AppFace SDK 的导出表。
2. 调用该指针时压入的六参数里，有一个 32 字母串
   （`gn.exe` @ `0x44d39c`、`XNet2.exe` @ `0x469080`，
   `sha1_8 = 322b599b`）。它是**皮肤口令参数**，不是解密密钥、
   不是注册表属性名。
3. 索引 `1..9` 是**皮肤方案编号**：`gn.exe` 钳到 `1..8`，
   `XNet2.exe` 钳到 `1..9`，由注册表 `SOFTWARE\KingWay\PARAM` 选择。
   出厂默认加载 1 号，其余为备选皮肤。
4. payload 里是**未加密的 GIF**（LZW 流，含 trailer），
   解出 12 张，渲染为 4 状态工具条按钮贴图、竖排按钮组、状态图标。
5. magic `5599259b` 不在两个宿主里 —— 解析逻辑在 `appface.dll` 内部。

> **复现路径**：提取脚本与产物都在仓库**外**的上一级目录：
> `../tools/extract_urf_images.py`、`../_urf_images/`（12 张 GIF）。
> 它们不随仓库分发，因为 `.urf` 与复刻无关。

## 5. 这对复刻意味着什么

| 层 | 数据在不在 | 结构清楚吗 | 能复刻吗 |
|---|---|---|---|
| L1 词表 `FalunWord.lib` | ✅ | ✅ 完整 | ✅ 已复刻 |
| L2 分类规则 `*fil.dat` | ✅ | ✅ 完整 | ✅ 已复刻 |
| L3 信任表 `TrustUrl.dat` | ✅ | ✅ 完整 | ✅ 已导入 |
| L4 端口表 `filtport.dat` | ✅ 2 条 | ✅ 明文 | ✅ 已审计 |
| L4 进程表 `filtpros.Dat` | ❌ 缺 | — | ❌ |
| L4 更新表 `uFWORD/uTUrl/uFUrl/FiltUrl` | ❌ 缺 | 部分（加载器已解，见 §2.3） | ❌ |

所以过滤层里**不存在「数据在、结构不明」的黑盒**。
剩下的缺口只有四张包内根本没带的更新表，复刻需要自行重建。

`.urf` 不在这张表里，因为它不是过滤层的一部分。

## 6. 复现：怎么验证 `0x310082f0` 是 `strstr`

读字节只能看出"像"，要定论得实际跑。用 unicorn 装载
`Handler.dll` 的 `.text`，按 `__cdecl` 摆好栈再模拟：

```python
import pefile
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UcError
from unicorn.x86_const import UC_X86_REG_ESP, UC_X86_REG_EAX

raw = open("LH-installed-tree/Windows/Handler.dll", "rb").read()
ib = pefile.PE(data=raw).OPTIONAL_HEADER.ImageBase

mu = Uc(UC_ARCH_X86, UC_MODE_32)
mu.mem_map(ib, 0x200000)
mu.mem_write(ib, raw[:0x200000])
STACK, DATA = 0x33000000, 0x34000000
mu.mem_map(STACK, 0x10000)
mu.mem_map(DATA, 0x10000)

def call(haystack: bytes, needle: bytes):
    mu.mem_write(DATA, haystack + b"\x00")
    mu.mem_write(DATA + 0x100, needle + b"\x00")
    esp = STACK + 0x8000
    # 栈布局：[esp]=ret, [esp+4]=haystack, [esp+8]=needle
    for v in (DATA + 0x100, DATA, 0x22222222):
        esp -= 4
        mu.mem_write(esp, v.to_bytes(4, "little"))
    mu.reg_write(UC_X86_REG_ESP, esp)
    mu.emu_start(ib + 0x82F0, 0x22222222, count=50000)
    return mu.reg_read(UC_X86_REG_EAX)

# 全部应判为"命中"
assert call(b"visit http://evil.com now", b"http://")
assert call(b"http://evil.com", b"http://")
assert call(b"plain text", b"pl")
assert call(b"ZZplain", b"pl")
# 全部应判为"不命中"
assert not call(b"visit ftp://evil.com now", b"http://")
assert not call(b"abc", b"http://")
assert not call(b"plain text", b"zz")
```

七例全过，才能说它是 `strstr` 而不是 `strcmp`。
反例很直接：若为相等比较，`"visit http://evil.com now"` 对
needle `"http://"` 应当不命中，而实测命中。

栈偏移之所以是 `[esp+4]=haystack`、`[esp+8]=needle`，
是因为 `0x310082f0` 入口处 `mov ecx,[esp+8]`（取 needle），
而 `0x310082f9` 三次 `push` 后的 `[esp+0x10]` 等于原 `esp+4`（取 haystack）。
第一次摆栈时我把顺序弄反了，`ecx` 拿到的是返回地址，报
`UC_ERR_READ_UNMAPPED`——这本身也是个有用的校验：**顺序错就跑不通**。

两个容易踩的地方：

1. `emu_start(begin, until)` 的 `begin` 是**绝对地址**。写成
   `ib + 0x310082f0` 会得到 `0x620082f0`，报 `UC_ERR_FETCH_UNMAPPED`，
   错误信息里那个 `EIP` 就是线索。
2. `until` 设成未映射的哨兵值，函数 `ret` 撞上去会抛
   `UC_ERR_FETCH_UNMAPPED`。这是**预期结尾**，不是失败——捕获之后
   `EAX` 里已经是返回值。所以别在每个测试间复用同一个 `Uc` 实例：
   撞过一次异常的实例状态不可靠，要每次重建。
