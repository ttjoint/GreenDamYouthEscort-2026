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
| L4 更新表 `uFWORD/uTUrl/uFUrl/FiltUrl` | ❌ 缺 | — | — |

所以过滤层里**不存在「数据在、结构不明」的黑盒**。
剩下的缺口只有四张包内根本没带的更新表，复刻需要自行重建。

`.urf` 不在这张表里，因为它不是过滤层的一部分。
