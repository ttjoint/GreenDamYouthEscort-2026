# 33 个模块分别是什么

安装树里 33 个 PE 文件。本文按**产品线与职责**分组，每组给出判据。
判据只来自文件内部（版本资源、导出表、导入表、字符串），不靠文件名猜。

```bash
python tools/audit_modules.py --legacy ../LH-installed-tree \
    --json data/modules.json
```

## 1. 三方产品线 + 两个第三方库

版本资源的公司名把树切成了几块，这解释了为什么命名风格不一致
（`gn.exe` / `xcore.dll` / `MPSvcDll.dll` 三套体系并存）：

| 厂商 | 模块数 | 模块 |
|---|---|---|
| 金惠科技（`kingsoft-hnc`） | 9 | `CImage.dll`、`FImage.dll`、`XDaemon.exe`、`XNet2.exe`、`dbServ.dll`、`dbfilter.dll`、`gn.exe`、`looklog.exe`、`lookpic.exe` |
| KingWay（`kingway`） | 4 | `Surfgd.dll`、`Xcv.dll`、`Xtool.dll`、`xcore.dll` |
| hnc（`hnc-engine`） | 3 | `HASrv.dll`、`Handler.dll`、`InjLib32.dll` |
| AppleFace（第三方 UI） | 1 | `appface.dll` |
| cximage（第三方图像） | 2 | `cximage.dll`、`kwimage.dll` |
| Microsoft（第三方） | 1 | `RunAfterSetup.exe` |
| 无版本资源 | 12 | 见下 |

无版本资源的 12 个里，`MPSvc*` 三个和 `SentenceObj.dll`、`poppo.dll`、
`IPGate.dll`、`CardLib.dll`、`Update.exe`、`kwdata.exe` 与金惠侧的
`MPSvcDll.dll` 调用关系明确，按依赖归到金惠线。

**`kwselectinfopp.dll` 只有 672 字节，不是 PE 文件**（首 16 字节
`00000000 5151 0000...`）。它在该目录里但不参与加载。

## 2. 职责分组

### 2.1 用户界面（`gn.exe` 一家）

| 模块 | 职责 | 判据 |
|---|---|---|
| `gn.exe` | 总控界面 | 版本描述“绿坝·花季护航总控界面” |
| `appface.dll` | 皮肤引擎 | `Matinsoft` / `AppFace 2.9.2.3` |
| `goldlock.exe` | 硬件加密锁驱动客户端 | 字符串 `GoldLock Driver`、注册表底层 API（`ZwDeleteKey` 等）、PDB 路径 `F:\Develop\GoldLock\...` |
| `poppo.dll` | 弹窗 | 只导出 `SHOW`/`SHOW2` |

`gn.exe` 的 `%.urf` 皮肤路径就是喂给 `appface.dll` 的，见
`docs/legacy-control-files.md` §4。

**`goldlock.exe` 不是 AppFace 的示例程序**。版本资源里写 `APPFACE`
只因为它是用 AppFace 做的界面，实际身份是硬件加密锁驱动客户端：
字符串有 `GoldLock Driver`、`Install the key failed`，导入
`ZwCreateKey`/`ZwDeleteKey`/`ZwSetValueKey` 一整套注册表底层调用，
并带 `F:\Develop\GoldLock\...` 的 PDB 路径。`CardLib.dll`
（`ReadCard`/`ReadCardEx`、导入 `SetupDi*`）是它的读卡侧配套。

### 2.2 网络过滤（KingWay 的 LSP 套件）

| 模块 | 职责 | 判据 |
|---|---|---|
| `Surfgd.dll` | 过滤核心 | 导出 `SG_Control` + 8 个 `DBFP_*`，导入 winsock 全套 |
| `dbfilter.dll` | Winsock LSP 提供者 | 导出 `WSPStartup`、`DBFilterInstall` |
| `dbServ.dll` | LSP 安装/配置 | 导出 `DBFilterInstall/Isinstalled/Uninstall` |
| `IPGate.dll` | 端口拦截薄壳 | 只导出 `Start`/`SetParam`/`GetLogBuf`，只导入 `SG_Control` |

`dbfilter.dll` 是 **LSP**（`WSPStartup` 是分层服务提供者的标准入口），
它在 Winsock 栈里插一层，把流量转给 `Surfgd.dll` 的 `DBFP_*`。
`dbServ.dll` 负责把 LSP 注册进
`SYSTEM\CurrentControlSet\Services\WinSock2\DBFILTER`。

**`Surfgd.dll` 是 L3/L4 的真正实现者**，它同时引用
`TrustUrl.Dat`、`wfile.dat`、`filtport.Dat`、`filtpros.Dat`、
`FiltUrl.Dat`、`uTUrl.Dat`、`uFUrl.Dat`、`server.dat`、`image.Dat`
和 `%s\sysdir\%02d%02d%02d.dat`。之前把这几张表分散记在别处，
实际只有这一个模块读它们。

### 2.3 HNC 注入与语义（hnc 线）

| 模块 | 职责 | 判据 |
|---|---|---|
| `InjLib32.dll` | 往目标进程注入 | 只导出 `InjectLibrary` |
| `HASrv.dll` | API hook 引擎（无策略） | 见 `docs/api-hook-layer.md` |
| `Handler.dll` | 注入端，持有 hook 表 | 见 `docs/api-hook-layer.md` |
| `SentenceObj.dll` | HNC 句法引擎 | 72 个导出，`CFWordLib`/`CSentenceObj` |
| `HncEng.exe` | 包装 `SentenceObj.dll` 的服务 | 导入 `CreateHncTextGroup` |
| `HncEngPS.dll` | 上者的 RPC 代理/存根 | `NdrProxy*` 全套 |

**注入链**：`XNet2.exe` 内含 `InjectLibrary` 字符串 → `InjLib32.dll`
→ `Handler.dll`（`HookFuncHandler`）→ `HASrv.dll`（`SetApiHookList`）。

`SentenceObj.dll` 是唯一的语义引擎。`Handler.dll` 和 `HncEng.exe`
都静态导入它的 `CreateHncTextGroup`/`ReleaseHncTextGroup`。

### 2.4 图像识别（`CImage.dll` 一家）

| 模块 | 职责 | 判据 |
|---|---|---|
| `CImage.dll` | 图像判定 | 导出 `CHECK`、`CHECKEX`、`CHECKEX2..13x`、`CAPSCREEN`、`DES2GO` |
| `FImage.dll` | 人脸检测 | 只导出 `FaceDectect`1–4，导入 `cvHaarDetectObjects` |
| `xcore.dll` / `Xcv.dll` / `Xtool.dll` | 静态链接的 OpenCV | 导出 `CvImage`/`CvMatrix`、`cv*` 函数 |
| `cximage.dll` / `kwimage.dll` | 第三方图像解码 | `Pizzolato Davide` |

`FImage.dll` 导入 `cvHaarDetectObjects` —— **这是 OpenCV 的 Haar
级联人脸检测器**。`CHECKEX` 系列就是“看一眼这张图”，13 个变体大概
对应不同判定策略/尺寸。

`XNet2.exe` 导入 `CImage.dll` 的 `CAPSCREEN`、`CHECKEX2`、`DES2GO`
三个函数，所以截图判定这条路是**真的接在网络侧控制台上**。

### 2.5 主控与运维

| 模块 | 职责 | 判据 |
|---|---|---|
| `XNet2.exe` | 控制台主程序 | 版本描述“绿坝·花季护航控制台” |
| `XDaemon.exe` | 守护进程 | 导入 `MPSvcDll` 的 `WinExecImp` 等 |
| `MPSvcC.exe` | 服务控制 | 字符串 `MPSvcC.exe /removejh`、`hnceng.exe /remove` |
| `MPSvcDll.dll` | 运维工具集 | 19 个导出，见下 |
| `MPSvcPS.dll` | 上者的 RPC 代理 | `NdrProxy*` |
| `CardLib.dll` | 读卡器/硬件锁 | 导出 `ReadCard`，导入 `SetupDi*` |
| `goldlock.exe` | 加密锁驱动客户端 | 见 §2.1 |
| `looklog.exe` | 日志查看 | 版本描述“日志查看” |
| `lookpic.exe` | 截图查看 | 版本描述“日志查看器” |
| `Update.exe` / `kwdata.exe` | 更新程序 | 版本描述“更新程序” |
| `RunAfterSetup.exe` | 安装后置 | `Microsoft` 基础类应用 |
| `CardLib`/`goldlock` | 见上 | |

`MPSvcDll.dll` 的 19 个导出分两类：

- **运维**：`CopyFileImp`、`DeleteFileImp`、`RemoveDirImp`、
  `SetRegSZ`、`SetRegDWORD`、`SetRegBin`、`DelRegKeyAndVal`、
  `WinExecImp`、`SetSystemTimeImp`、`SHFileOperationImp`、`ExitCom`
- **内容相关**：`FilterWords`、`UpdateWords`、`SnapShotScreen`、
  `WriteP2FileStr`、`VSDefStartSvc`

## 3. `MPSvcDll.dll` 的导出是一组 COM 转发

这一节值得单列，因为**只看静态导入表会得出完全相反的结论**。

`MPSvcDll.dll` 导出 `FilterWords`、`UpdateWords`、`SnapShotScreen`
等 19 个函数。全树没有任何模块**静态导入**它们，字符串 `FilterWords`
也只出现在 `MPSvcDll.dll` 自己的导出名表里。到此为止很容易判成
"死导出"——**这个判断是错的**。

反汇编 `0x10001440`（`FilterWords`）：

```
call [0x100050b0]        ; CoInitialize(0)
push 0x100050d0          ; CLSID
push 0x100050c0          ; IID
push 4                   ; CLSCTX_LOCAL_SERVER
call [0x100050b4]        ; CoCreateInstance(...)
...
mov  edx, [ecx]          ; vtable
call [edx + 0x34]        ; FilterWords 真正实现
call [ecx + 8]           ; Release
call [0x100050b8]        ; CoUninitialize(0)
```

**`MPSvcDll.dll` 里这 19 个导出是同一套 COM 接口的转发层**，
各自只差虚表偏移：

| 导出 | 虚表偏移 |
|---|---|
| `UpdateWords` | `[edx+0x30]` |
| `FilterWords` | `[edx+0x34]` |
| `SnapShotScreen` | 同一模式 |

CLSID 与 IID 就嵌在 `MPSvcDll.dll` 里：

- CLSID `{56835A03-3BAA-49F0-BA6E-8EEFD6D62991}`
- IID   `{56835A03-3BAA-49F0-BA6E-8EEFD6D62960}`

两者的 `BYTES_LE` 形式在 **`MPSvcC.exe`、`MPSvcDll.dll`、
`MPSvcPS.dll`** 三个模块里都能找到，别处没有。所以
`MPSvcDll.dll` 是唯一会用这个接口的客户端。

所以这条链是**活的**：

```
调用方 → MPSvcDll.dll!FilterWords (转发)
       → CoCreateInstance(CLSID)
       → MPSvcC.exe 的 COM 服务端
       → vtable+0x34
```

`MPSvcPS.dll` 是配套的 RPC 代理/存根（`NdrProxy*` 全套，
并自称 `MPSVCPROXY.dll`）。

**方法论教训**：`GetProcAddress` 和 COM 都不需要静态导入表。
全树有 27 个模块导入 `LoadLibraryA` + `GetProcAddress`，
"导入表里没有"不能推出"没有调用者"。反汇编函数体才作数。

### 3.1 `vtable+0x34` 在服务端落到哪里

`MPSvcDll.dll` 只说了"调 `[edx+0x34]`"，服务端在 `MPSvcC.exe`。
那个 `+0x34` 的实现是 `0x403a30`：

```
0x403a30  push 0x40f6c8
0x403a35  call [0x40c164]      ; EnterCriticalSection
0x403a3b  mov  ecx, 0x40f6b8   ; this
0x403a40  mov  eax, [esp+8]    ; push 之后读，等于入口的 [esp+4] = arg1
0x403a44  push eax
0x403a45  call 0x401e60
0x403a4a  push 0x40f6c8
0x403a4f  call [0x40c168]      ; LeaveCriticalSection
0x403a55  xor  eax, eax        ; 恒返回 S_OK
0x403a57  ret  8
```

两个必须点明的事实：

1. **返回值恒为 `S_OK`。** `MPSvcDll.dll` 那边是
   `test eax,eax` / `jl` → `esi=1`，所以 **`FilterWords` 恒返回 1**。
   它不表达"匹配到没有"，只是一个"调用完成"标志。
   拿它当过滤结果用会永远得到"命中"。
2. **`arg2` 完全没用到。** 进 `0x401e60` 的只有 `arg1`
   （`0x403a40` 在 `push` 之后读，等于入口的 `[esp+4]`）。
   `ret 8` 却按两个参数清栈。

`0x401e60` 做的事是**加载一张表**，不是匹配：

```
arg 为空 → GetWindowsDirectoryA(buf,0x104) 之后拼 hnclib 下的 uFWORD.dat
        非空 → 直接用 arg 当路径
fopen(path, "rb")          -- 失败返回 0（0x402010）
第一遍  fscanf(fp,"%s",row) 数行，上限 0x190 = 400
fseek(fp, 0, SEEK_SET)
malloc(行数 * 4)           -- 指针数组
第二遍  每行 malloc(0x32=50)、拷贝，超 50 字节截断
fclose
返回 1                     -- 0x401ffe
```

对照 `docs/legacy-control-files.md` §2.2 里 `Handler.dll` 的
`0x310075d0`：同一个文件、同样 `"rb"`、同样 400 行上限、
同样 3–50 长度区间。**两条链读的是同一份文件**，
只是各自实现了一遍解析。出厂包里这个文件不存在，
所以两边拿到的都是空表。

### 3.2 `MPSvcC.exe` 里有两套虚表，别搞混

`.rdata` 里有两个 16 项虚表，都以 `AddRef` / `Release` 收尾，
但顺序不同：

| 地址 | `[0]` | `[1]` | `[2]` | `+0x34` |
|---|---|---|---|---|
| `0x40c260` | `0x402cc0` | `0x403d30`(Release) | `0x402c80` | `0x403780` |
| `0x40c278` | `0x402c60` | `0x403d50`(QI) | `0x403d30` | **`0x403a30`** |

`0x403d50` 是标准 `QueryInterface`：先比 `0x40c2e8`（IID_IUnknown），
再比 `0x40e800`，命中就 `call [edx+4]` 加引用并返回 0，
否则返回 `0x80004002`（`E_NOINTERFACE`）。

`FilterWords` 走的是 `0x40c278` 那张。`0x40c260` 是另一个对象
（`+0x34` 落在 `0x403780`），两个表在 `.text` 里都有独立引用点。

## 3.5 `SentenceObj.dll` 里 14 个裸 `.lib` 不是加载路径

`SentenceObj.dll` 里出现的 `.lib` 分两种形态，差别是决定性的：

| 形态 | 文件 | 引用 |
|---|---|---|
| 带 `%s\` 前缀 | `%s\FalunWord.lib`、`%s\FMean.lib` | 各 1 处 `push`，在同一个函数里 |
| 裸文件名 | `Word.lib`、`JiBenJL.lib`、`TYPE.LIB`、`XU.LIB`、`rule.lib`、`SENTEN.LIB`、`ElTest.lib`、`EgPerc.lib`、`UnifyPerc.lib`、`DefIndex.lib`、`Defl0Index.lib`、`Defl0Data.lib`、`ActiveMode.lib`、`AttribE.lib` | 落在 `.data` 尾部 |

按 NUL 边界数，裸 `.lib` 名共 16 个；减去 `FalunWord.lib` 与
`UserWord.lib` 这两个**也带前缀**的重复副本，剩下 14 个纯裸名。
`UserWord.lib` 只在 `0x1004528c` 出现一次，两处 `push` 引用
（`0x376b`、`0x38dc`），是用户自定义词的接口——它同样不在树上。

**带 `%s\` 的那两个是真正加载的**，`0x10004cd2`–`0x10004ce0`
可以读到完整序列：先 `%s\FMean.tmp`，再 `%s\FAlunWord.lib`，
失败则 `or eax,-1` 返回。

裸文件名那 14 个集中在 `0x10045778`–`0x10045d0c` 和
`0x1007fac8`–`0x1007fb60` 两块，后一块甚至落在 `.data` 的
未初始化区（`VSize` 0x9b57c > `RawSize` 0x36000）。它们是
调试/日志输出用的固定字符串，**不是加载路径**。

安装树里 `HNCLIB/` 只有 `FalunWord.lib` 一个文件。所以：

- **能加载的只有 `FalunWord.lib`**（外加一个同样不存在的 `FMean.lib`）；
- 14 个裸名字对应的规则/词库文件从来就没随产品发出去过。

这与 `docs/replication-status.md` 的结论一致，现在有了更直接的证据：
不是“我们没找到”，是**它们不在出厂包里，而且引用它们的代码路径
要么是调试输出，要么会失败返回 -1**。

## 4. 缺失文件清单

`tools/audit_modules.py` 会把“被引用但树上不存在”的文件全列出来。
按读者分组：

| 文件 | 谁引用 | 性质 |
|---|---|---|
| `uFWORD.dat`、`uTUrl.Dat`、`uFUrl.Dat`、`filtpros.Dat`、`FiltUrl.Dat`、`server.dat` | `Surfgd.dll`、`XNet2.exe` | 联网更新下发的表 |
| `XWORDH.DAT`、`XWORDM.DAT`、`XWORDL.DAT` | `IPGate.dll` | 端口侧三张分级词表，字符串在 `0xc194`/`0xc1a0`/`0xc1ac`，未随包发出 |
| `HncStdRun.ini` | `HncEng.exe` | HNC 运行配置 |
| `settings.ini` | `Handler.dll` | 运行时由安装器写 `c:\settings.ini` |
| `index.dat`、`lupdate.dat`、`kwupdate.dat` | `XNet2.exe` | 更新索引 |
| 14 个裸 `.lib` | `SentenceObj.dll` | 调试字符串，非加载路径 |

`%s\sysdir\%02d%02d%02d.dat` 是运行期按日期拼的路径，不是固定文件名。

## 5. 复刻现状

按"有没有实证支撑"分，不按我的取舍偏好分。

### 5.1 已复刻或有充分实证

| 能力 | 载体 | 状态 |
|---|---|---|
| 词表判定 | `FalunWord.lib` | 已复刻 |
| 分类规则 | `adwapp`/`adwfil`/`iawfil`/`lgwfil`/`vgamfil` | 已解析 |
| 信任表 | `TrustUrl.dat`、`wfile.dat` | 已解析，未接入 |
| 端口表 | `filtport.dat` | 已解析 |
| 策略层 | `src/green_dam/policy.py` | 已实现，缺规则接线 |

### 5.2 原厂确实在做，我们还没做

| 能力 | 载体 | 现状 |
|---|---|---|
| **图像识别** | `CImage.dll` + `FImage.dll` + OpenCV | **未复刻**，见 `docs/image-recognition.md` |
| **屏幕文字拦截** | `HASrv`/`Handler` 的 4 条 GDI hook | 未复刻 |
| **端口/协议过滤** | `Surfgd.dll` LSP | 未复刻 |
| **COM 服务接口** | `MPSvcDll` → `MPSvcC.exe` | 接口已解，语义未解 |

图像识别这条**不是**"可以不做"。`XNet2.exe` 静态导入了
`CImage.dll` 的 `CAPSCREEN`、`CHECKEX2`、`DES2GO`，调用路径确凿存在。
它做的是全屏截图（`BitBlt` SRCCOPY）+ 肤色比例阈值 + Haar 人脸检测，
详见 `docs/image-recognition.md`。

而且它的依赖**是齐的**：`Windows\System32\XFimage.xml`（858,866 字节）
就是完整的 `haarcascade_frontalface_alt2` 分类器（20 stage / 1,047 tree）。
在所有未复刻模块里，图像识别的数据条件最好。

### 5.3 证据不足，不能说死代码

| 模块/导出 | 已知 | 未知 |
|---|---|---|
| `FilterWords`/`UpdateWords` | COM 转发，CLSID/IID 已解 | 服务端方法体的判定语义 |
| `uFWORD.dat` 家族 6 个文件 | 被 4 个模块引用 | 是否由更新下发；本地无样本 |

这些不再写成"死代码"或"死导出"。正确表述是
**"我们还没解出它的语义"**。

### 5.4 明确不做

只有一项有充分理由，且是**道德理由不是技术理由**：
`HASrv`/`Handler` 那 11 条自保护 hook（改注册表、`OpenProcess`、
`OpenService`）——它们是对抗卸载，不属于过滤能力。
