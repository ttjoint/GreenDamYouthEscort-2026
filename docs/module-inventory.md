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

## 3. 两条重要的否定结论

### 3.1 `FilterWords` 没有任何调用者

`MPSvcDll.dll` 导出 `FilterWords` 和 `UpdateWords`，看起来像是
主控侧的过滤入口。但**全树 33 个模块，没有任何一个导入它们，
也没有任何一个模块的字符串里出现 `FilterWords`**（只有
`MPSvcDll.dll` 自己的导出名表里有）。

它是**死导出**。这条和 `SentenceObj.dll` 那 14 个裸 `.lib`
是同一个性质的证据：产品留着一个能力，但没接上。

### 3.2 `SentenceObj.dll` 里 14 个裸 `.lib` 不是加载路径

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

## 5. 对复刻的影响

- **必须复刻的只有三块**：词表判定（`FalunWord.lib`）、
  分类规则（五张 `*fil.dat`）、信任/端口表（`TrustUrl.Dat`、
  `filtport.dat`）。
- **可以不做的**：AppFace 皮肤、`cximage`/OpenCV 图像识别、
  运维工具集、LSP 本身（我们不需要插进 Winsock）。
- **必须刻意不做的**：`HASrv`/`Handler` 的 11 条自保护 hook。
- **不再追查的**：14 个裸 `.lib`、`FilterWords`、`uFWORD.dat` 家族
  ——已有足够证据说明它们不在出厂包里或根本是死代码。
