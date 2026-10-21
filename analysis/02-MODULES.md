# 02 — MODULES（PE 模块表）

**生成方式**：`probe/p02_modules.py`（机械提取）+ `probe/p03_imports.py`（导入表）+ 人工裁定行。
原始数据见 `02-MODULES-RAW.tsv`（禁止手改，重跑脚本覆盖）。

**计数校正**：安装树内 **32 个 PE**（非旧文档所称 33）。`kwselectinfopp.dll`（672 B）
非 PE。范围外另有 6 个 PE（2 个安装包 + 4 个驱动 blob）。

---

## 图例

- `method`：`PE-HDR` 头字段 / `IMP` 导入表 / `EXP` 导出表 / `VER` 版本资源 / `ARITH` 闭合算术 / `DOC-CLAIM` 旧文档断言（计零）
- `status`：`CLOSED` / `PROBING` / `CLAIM` / `REFUTED`
- `vendor` 全部来自 VS_VERSIONINFO 的 `CompanyName`，**UTF-8 解码**（该字段是 UTF-8 字节，按 GBK 读会乱码 —— 这是旧文档 vendor 分组出错的根源）

---

## A. 厂商归属（`method=VER`，全部 CLOSED）

| CompanyName（原文） | 数量 | 模块 |
|---|---|---|
| `金惠科技` | 4 | `CImage.dll` `FImage.dll` `dbServ.dll` `dbfilter.dll` |
| `金惠科技---大正科技` | 1 | `gn.exe` |
| `金惠科技----大正科技` | 2 | `XNet2.exe` `lookpic.exe` |
| `金惠科技--大正科技` | 1 | `XDaemon.exe` |
| `金惠科技 ---大正科技` | 1 | `looklog.exe` |
| `绿坝花季护航` | 1 | `MPSvcC.exe` |
| `hnc` | 3 | `HASrv.dll` `Handler.dll` `InjLib32.dll` |
| `KingWay Tec.` | 3 | `Xcv.dll` `Xtool.dll` `xcore.dll` |
| `KingWay Corporation` | 1 | `Surfgd.dll` |
| `Matinsoft Inc.` | 1 | `appface.dll` |
| `Pizzolato Davide - www.xdp.it` | 2 | `cximage.dll` `kwimage.dll` |
| `Microsoft` | 1 | `RunAfterSetup.exe` |
| `APPFACE` | 1 | `goldlock.exe` |
| `(空)` | 2 | `HncEng.exe` `HncEngPS.dll` |
| `(无资源)` | 8 | `CardLib.dll` `MPSvcDll.dll` `MPSvcPS.dll` `SentenceObj.dll` `IPGate.dll` `Update.exe` `kwdata.exe` `poppo.dll` |

### ⚠️ 与旧文档冲突（见 `07-CONFLICTS.tsv` #K-01）

`GDE/docs/module-inventory.md` 把 `XNet2.exe` / `gn.exe` / `XDaemon.exe` / `looklog.exe` /
`lookpic.exe` / `dbServ.dll` / `dbfilter.dll` 归为 `kingsoft-hnc`（金惠单方）。
**版本资源实测为「金惠 + 大正」联合署名**。`dbServ.dll` / `dbfilter.dll` 确为金惠单方
（`金惠科技`），但主控端系列是联合署名。

该文档还称 `looklog.exe` / `lookpic.exe` 属金惠 —— 实测为联合署名。

---

## B. 网络过滤链（`method=IMP` + `EXP`，CLOSED）

导入关系是**可直接读出的机器事实**，不依赖反汇编：

```
dbfilter.dll  ──imports──▶  SurfGd.dll!DBFP_{Connect,Socket,SendTo,RecvFrom,...}  (6)
Surfgd.dll    ──imports──▶  WS2_32.dll (13)
```

### 导出表实测

| 模块 | 导出 | RVA |
|---|---|---|
| `dbfilter.dll` | `DBFilterInstall` | `0x1bb0` |
| | `DBFilterIsinstalled` | `0x1d10` |
| | `DBFilterUninstall` | `0x1d50` |
| | `WSPStartup` | `0x13b0` |
| `Surfgd.dll` | `DBFP_Socket` | `0x5458` |
| | `DBFP_CloseSocket` | `0x55c5` |
| | `DBFP_Connect` | `0x55cc` |
| | `DBFP_Accept` | `0x55e0` |
| | `DBFP_Send` | `0x5919` |
| | `DBFP_Recv` | `0x6184` |
| | `DBFP_SendTo` | `0x6480` |
| | `DBFP_RecvFrom` | `0x6606` |
| | `SG_Control` | `0x6ffa` |

### ⚠️ `dbServ.dll` 的定位 —— 旧文档对，本表初稿错（见 `07-CONFLICTS.tsv` #K-02）

`GDE/docs/module-inventory.md` 称 `dbServ.dll` 为「LSP 安装/配置」。
**本表初稿曾判其「不在网络路径上」，该判断已被自己的实验推翻。**

**实测（`EXP` + `IMP` + `DISASM` + `STR`）**：

- 92 个导出中 **89 个与 `kwimage.dll` 同名**（CxImage 类成员），额外 3 个：
  `DBFilterInstall` / `DBFilterUninstall` / `DBFilterIsinstalled`。
- 导入表：`ADVAPI32(4)` / `MPSvcDll.dll(3)` / `cximage.dll(17)` / `KERNEL32(54)`。
  **不导入 `SurfGd.dll`，不导入 `WS2_32`。**
- 但 `.rdata` 含 `...\WinSock2\Parameters\Protocol_Catalog9\Catalog_Entries`、
  `PackedCatalogItem`、`DBFilterHook`、`DBFilterPathName` —— **它真的写协议目录**。
- `DBFilterIsinstalled @0x10002030` 有完整 `RegQueryValueExA` 序列，非空桩。

**精确结论**：两套并行安装器 —— `dbServ.dll`（CxImage 库附带）与 `dbfilter.dll`
（`WSPStartup` 实现体 + 同样安装器）。只有 `dbfilter.dll` 是**分层服务提供者**；
`dbServ.dll` 只是**安装器**。谁是主流程调用者 → `04-ENTRYPOINTS`。

---

## C. 图像链（`method=EXP`，CLOSED）

| 模块 | 导出数 | 判据 |
|---|---|---|
| `xcore.dll` | 377 | 全为 C++ mangled name |
| `Xcv.dll` | 302 | 同上 |
| `Xtool.dll` | 124 | 同上 |
| `kwimage.dll` | 366 | `CxFile` 等 CxImage 类成员 |
| `cximage.dll` | 366 | 同上（**与 kwimage 非同一文件**：MD5 不同） |
| `dbServ.dll` | 92 | CxImage 子集 + 3 DBFilter |
| `CImage.dll` | 107 | 金惠自有封装 |
| `FImage.dll` | 4 | 薄封装 |

`kwimage.dll` 与 `cximage.dll`：导出数 / 导入数 / 入口 RVA（`0x86f8f`）全部相同，
但 **MD5 不同**（`284565a8…` vs `dd0cba81…`）。→ 同源不同构建，非同一文件。

---

## D. 语义链（`method=EXP` + `IMP`，CLOSED）

| 模块 | 导出 | 导入 | 备注 |
|---|---|---|---|
| `SentenceObj.dll` | **72** | 仅 `KERNEL32`(76) | 无版本资源 |
| `HncEng.exe` | 0（EXE） | 6 DLL / 110 函数 | |
| `HncEngPS.dll` | 4 | 3 / 96 | |
| `Handler.dll` | 9 | 7 / 118 | |
| `InjLib32.dll` | **1** | 2 / 76 | |
| `HASrv.dll` | 4 | 1 / 64 | 像基址 `0x37000000` —— 全树唯一 |
| `poppo.dll` | 2 | 6 / 221 | |

`HASrv.dll` 的 `ImageBase=0x37000000` 是**全树唯一非默认基址**（其余 DLL 均 `0x10000000`，
EXE 均 `0x400000`）。这是个可用的指纹。

---

## E. 未决 / 待证

| 模块 | 待证问题 | 下一步 |
|---|---|---|
| `IPGate.dll` | 3 导出已取：`Start` / `SetParam` / `GetLogBuf` | 待证：是否 GDI 文本钩子 |
| `CardLib.dll` | 2 导出：`ReadCard` / `ReadCardEx` | 待证：真实功能 |
| `MPSvcDll.dll` | 19 导出已取全 | ✅ 完成 |
| `HncEngPS.dll` | 4 导出 = 标准 COM 四件套 | ✅ 完成 |
| `Update.exe` | 无版本资源，11 导入 | 待定 |

---

## 进度

- [x] 32 个 PE 机器事实入表
- [x] 厂商归属（VER，UTF-8 解码）
- [x] 网络链导入/导出闭合
- [x] `dbServ` 定位（含自身纠错记录）
- [x] 全部导出名（`05-INTERFACES-RAW.txt`，7,312 行）
- [ ] 职责判定（需 DISASM + XREF 双证）
- [ ] 跨模块调用边（`06-LINKS`）
