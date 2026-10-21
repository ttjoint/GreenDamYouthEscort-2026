# 06b — INDIRECT LINKS（间接边：注册表 / 文件 / 进程 / COM）

`06-LINKS.tsv` 只含**导入表可枚举**的静态链接边（310 条，全部 CLOSED）。
本文件记录**间接边** —— 它们无法从导入表得到，必须逐条给证据。

方法标签同 `00-MASTER` §1.1。**间接边默认 `PROBING`**，除非有可重跑的产物证据。

---

## A. 自启 / 安装链（`method=BYTES` + `DISASM`，✅ CLOSED）

**证据**：`probe/p07_autostart_strings.txt`（`RunAfterSetup.exe` 字符串块 `0x1a100–0x1a5c0`）
+ `probe/p06_lsp_install.txt`（LSP 安装序列反汇编）

该模块把整条链的**命令行与路径**集中存放在一个字符串块里，逐条如下：

| 偏移 | 字符串 | 作用 |
|---|---|---|
| `0x1a13c` | `SoftDogSetup.dll` | 加密狗初始化 |
| `0x1a150` | `gnsoftinstall` | 导出名（`SoftDogSetup.dll` 内） |
| `0x1a184` | `\SurfGd.dll` | **LSP 的 SPI DLL 路径参数** |
| `0x1a190` | `DBFilterUninstall` | 动态 `GetProcAddress` 目标 |
| `0x1a1a4` | `DBFilterIsinstalled` | 同上 |
| `0x1a1b8` | `DBFilterInstall` | 同上 |
| `0x1a1e4` | `\DBFilter.dll` | **LSP 安装器 DLL（= `dbfilter.dll`）** |
| `0x1a1f8` | `...\Uninstall\KingWayKillPorn` | 卸载注册项 |
| `0x1a240` | `SOFTWARE\KingWay` | 配置根键 |
| `0x1a2d4` | `...\Uninstall\` | 卸载项前缀 |
| `0x1a32c` | `UninstallString` | 值名 |
| `0x1a33c` | `...\Uninstall\FilterDam` | 卸载注册项 |
| `0x1a384` | `SOFTWARE\Microsoft\Windows\CurrentVersion\Run` | **自启写入点** |
| `0x1a3e4` | `xnet2.exe` | 自启目标 |
| `0x1a3fe` | `112233` | **默认口令出现在本模块**（新发现） |
| `0x1a42c` | `\goldlock.exe -i` | 驱动安装命令行 |
| `0x1a440` | `\winXpspi.reg` | Winsock 目录还原（XP） |
| `0x1a450` | `\win2kspi.reg` | Winsock 目录还原（2K） |
| `0x1a460` | `\winvista.reg` | Winsock 目录还原（Vista） |
| `0x1a470` | `regedit.exe /s ` | 静默导入 reg |
| `0x1a48 / 249c` | `%s\sysdir` / `%s\snap` | 目录 |
| `0x1a4b4` | `XDaemon.exe` | 启动目标 |
| `0x1a4dc` | `netsh firewall add allowedprogram ` | **自我放行防火墙（新发现）** |
| `0x1a524` | `regsvr32.exe /s %s\MPSvcPS.dll` | COM 注册 |
| `0x1a544` | `%s\MPSvcC.exe -installjh` | 服务安装 |
| `0x1a560` | `regsvr32.exe /s %s\hncengps.dll` | COM 注册 |
| `0x1a580` | `%s\hnceng.exe -install` | 服务安装 |

### 由此确认的边

| 从 | 到 | 机制 | 状态 |
|---|---|---|---|
| `RunAfterSetup.exe` | `dbfilter.dll!DBFilterInstall/Isinstalled/Uninstall` | `LoadLibraryA` + `GetProcAddress` | ✅ CLOSED |
| `RunAfterSetup.exe` | 注册表 `...\CurrentVersion\Run` ← `xnet2.exe` | `RegSetValueExA` | ✅ CLOSED |
| `RunAfterSetup.exe` | `SoftDogSetup.dll!gnsoftinstall` | 动态加载 | 🟡 调用点未定位 |
| `RunAfterSetup.exe` | `goldlock.exe -i` | `WinExec` | 🟡 调用点未定位 |
| `RunAfterSetup.exe` | `MPSvcC.exe -installjh` | 命令行 | 🟡 同上 |
| `RunAfterSetup.exe` | `hnceng.exe -install` | 命令行 | 🟡 同上 |
| `RunAfterSetup.exe` | `regsvr32 /s MPSvcPS.dll` | 命令行 | 🟡 同上 |
| `RunAfterSetup.exe` | `regsvr32 /s hncengps.dll` | 命令行 | 🟡 同上 |

> **观察**：`RunAfterSetup.exe` 静态只导入 6 个 DLL。它对 `dbfilter.dll` 的调用是
> **唯一被反汇编证实**的动态调用；其余 7 条是从**字符串存在 + 模块导入 `WinExec`** 推出的，
> 因此只能是 `PROBING`。这正是本账纪律要防的那类「看起来显然」的推断。

---

## B. 文件级数据边（`method=STR` + `DISASM`）

| 从 | 到 | 证据 | 状态 |
|---|---|---|---|
| `Handler.dll` | `\hnclib\FalunWord.lib` | 字符串 `0x151ec`，紧邻 `HNCIT` / `HASrv.dll` / `Handler.dll` | ✅ CLOSED |
| `HncEng.exe` | `hnclib\` + `SentenceObj.dll` | 字符串存在（未逐址核验偏移） | 🟡 PROBING |
| `XNet2.exe` | `%s\%d.urf` | 路径拼接格式串 | 🟡 PROBING（解析方未定） |

**`Handler.dll` 字符串块实测**（`0x15199`–`0x151ec`）：

```
0x015199  nstallApiHook      ← "InstallApiHook" 的尾巴，说明本模块引用了该导出名
0x0151a8  HASrv.dll          ← 引用了 HASrv.dll 模块名
0x0151b4  Handler.dll
0x0151d8  \gn.exe
0x0151e0  HNCIT
0x0151ec  \hnclib\FalunWord.lib
```

→ `Handler.dll` **同时**引用 `HASrv.dll` 与 `InstallApiHook` 的字符串，
即它通过 `GetProcAddress` 调用 `HASrv.dll!InstallApiHook`，**不是静态导入**。
这补全了 K-03 反汇编结论的机制侧。

---

## C. 进程终止名单（`method=BYTES`，✅ CLOSED）

**证据**：`probe/p05_iocs.txt`（22/22 命中）

`Handler.dll` `0x15204`–`0x15353`，每条**带前导 `\`**，混合大小写：

```
\explorer.exe   \iexplore.exe  \mpsvcc.exe    \looklog.exe   \xnet2.exe
\p_shell.exe    \msoshell.exe  \hnceng.exe    \hncproxy.exe  \msfilter.exe
\tdimon.EXE     \Tcpview.exe   \procexp.exe   \Regmon.exe    \filemon.exe
\logview.exe    \dbgview.exe   \mmc.exe       \taskmgr.exe   \regedit.exe
\testtrack pro client.exe      \isdev.exe
```

**重要**：名单里包含**产品自身进程** —— `mpsvcc.exe` / `looklog.exe` / `xnet2.exe` /
`hnceng.exe` / `hncproxy.exe`。这不是「只杀监控工具」，
而是**一份统一的受限进程总表**，包含自监控目标与自身组件。

（旧表述「终止 dbgview/filemon/regmon/tcpview/procexp 等监控工具」方向对，
但把名单性质说小了。）

---

## D. 注入目标名单（`method=BYTES`，✅ CLOSED）

**证据**：`probe/p05_iocs.txt`（48/48 命中，`InjLib32.dll` `0x89e8`–`0x8c70`）

48 项按**类别连续排布**（偏移递增即可看出分组）：

| 偏移区间 | 类别 | 例 |
|---|---|---|
| `0x89e8`–`0x8a20` | 编辑器 | `editplus` `emeditor` `uedit32` `notepad` `wordpad` |
| `0x8a2c`–`0x8a70` | 办公 | `wps` `wpp` `excel` `powerpnt` `frontpg` `msaccess` `winword` |
| `0x8a7c`–`0x8ae0` | 邮件 | `outlook` `mailmagic` `popo` `qqmail` `imapp` `incmail` `msimn` |
| `0x8aec`–`0x8b04` | IM | `dm2005` `foxmail` `googletalk` |
| `0x8b14`–`0x8b80` | IM/工具 | `miranda32` `imu` `ypager` `tmshell` `start` `icqchatrobot` |
| `0x8b8c`–`0x8c6c` | 浏览器 | `greenbrowser` `touchnet` `theworld` `maxthon` `ttraveler` `netscp` `firefox` `opera` `netcaptor` `myie` `iexplore` |
| `0x8c34`–`0x8c6c` | 系统工具 | `mmc` `regedit` `taskmgr` `mpsvcc` `xdaemon` `xnet2` |

**注意最后一项**：注入名单里也有产品自身进程（`mpsvcc` `xdaemon` `xnet2`）。

---

## 进度

| 类别 | 已闭合 | 待办 |
|---|---|---|
| 自启/安装链 | 2 条边 | 5 条命令行边待反汇编定位 |
| 文件数据边 | 1 条（`Handler→FalunWord.lib`） | `HncEng` / `XNet2→.urf` |
| 进程终止名单 | 22/22 | — |
| 注入名单 | 48/48 | — |
| COM 注册 | 字符串已证 | `HncEngPS`/`MPSvcPS` 的 CLSID 待取 |
