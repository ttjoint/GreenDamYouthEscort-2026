# 07 — CONFLICTS（互斥断言与判定实验）

规则：**冲突不靠文笔解决。** 每行必须写「判定实验」，实验跑完才改 `status`。
`REFUTED` 行保留原文，禁止删除 —— 删除后再推导一遍会得出同样的错误。

---

## K-01 厂商归属：`XNet2.exe` / `gn.exe` / `XDaemon.exe` 是谁的？

| 方 | 断言 | 来源 |
|---|---|---|
| 旧 | 归 `kingsoft-hnc`（金惠单方） | `GDE/docs/module-inventory.md` §1 |
| 新 | `CompanyName` = `金惠科技---大正科技`（联合署名） | `02-MODULES-RAW.tsv` |

**判定实验**：`probe/p02_modules.py`，读 VS_VERSIONINFO `CompanyName`，UTF-8 解码。

**结果**：`gn.exe` = `金惠科技---大正科技`；`XNet2.exe` = `金惠科技----大正科技`；
`XDaemon.exe` = `金惠科技--大正科技`；`looklog.exe` = `金惠科技 ---大正科技`；
`lookpic.exe` = `金惠科技----大正科技`。

**裁决**：旧文档 `REFUTED`。主控端系列是**联合署名**，不是金惠单方。
注意各家连字符数量不同（`---` / `----` / `--` / ` ---`），说明是逐文件手填，非统一模板。

> 附带纠正：旧文档把 `dbServ.dll` / `dbfilter.dll` 也归 `kingsoft-hnc`。实测二者
> `CompanyName=金惠科技`（**单方**，无大正）。归属正确，但理由（"kingsoft-hnc" 这个
> 合并标签）掩盖了真实分工。

---

## K-02 `dbServ.dll` 是不是 LSP？

| 方 | 断言 | 来源 |
|---|---|---|
| 旧 | 「LSP 安装/配置」，导出 `DBFilterInstall/Isinstalled/Uninstall` | `GDE/docs/module-inventory.md` §2.2 |
| 新 | CxImage 库 + 3 个 DBFilter 薄绑定，**不在网络路径上** | 导出/导入集合实测 |

**判定实验**：
1. 取 `dbServ.dll` 与 `kwimage.dll` 导出名集合差集。
2. 读 `dbServ.dll` 导入表。

**结果**：
- `dbServ.dll` 92 导出，**89 个与 `kwimage.dll` 同名**（`CxFile`/`CxIOFile` 等 CxImage 类成员）。
- 差集仅 3 个：`DBFilterInstall` / `DBFilterIsinstalled` / `DBFilterUninstall`。
- 导入表：`ADVAPI32(4)` / `MPSvcDll.dll(3)` / `cximage.dll(17)` / `KERNEL32(54)`。
  **无 `SurfGd.dll`，无 `WS2_32.dll`。**

**裁决**：旧文档**部分错误**。`dbServ.dll` 确实导出三个 DBFilter 入口，但它：
(a) 主体是 CxImage；(b) 不导入 winsock；(c) 不导入 `SurfGd`。
→ **不承载网络数据路径。**

### ⚠️ 本行自身曾被写错，予以更正（保留记录）

本表初稿曾判 `dbServ.dll`「不是 LSP，不写协议目录」。**该判断过强，已撤回。**

**追加实验（`ARITH` 无关，`STR`+`DISASM`）**：反汇编 `0x10001f00`（`DBFilterInstall`）
与 `0x10002030`（`DBFilterIsinstalled`），并在 `get_memory_mapped_image()` 中检索关键串：

```
dbServ.dll 命中：
  SYSTEM\CurrentControlSet\Services\WinSock2\DBFILTER
dbServ.dll 命中：
  SYSTEM\CurrentControlSet\Services\WinSock2\Parameters\Protocol_Catalog9\Catalog_Entries
dbServ.dll 命中： PackedCatalogItem / DBFilterHook / DBFilterPathName

DBFilterIsinstalled @0x10002030:
  sub esp,0x104 / push 1 / push 0x1000a030 / push 0x80000002 (HKEY_LOCAL_MACHINE)
  / push 0x104 / push eax / push 0x1000a0dc / call 0x10001a90 (RegQueryValueExA)
  / neg eax / sbb eax,eax / neg eax      ← 返回 0/1

DBFilterUninstall @0x10002070:
  call 0x10002030 / test eax,eax / jne ...
```

**修正后的事实（CLOSED）**：

- `dbServ.dll` **确实是 LSP 安装器**：它写 `Protocol_Catalog9\Catalog_Entries`，
  用 `PackedCatalogItem` 结构。三个 `DBFilter*` 导出是**真实实现**，非转发桩。
  
- 但它**不是 LSP 本体**：无 `WSPStartup` 导出，不导入 `SurfGd.dll`，不导入 `WS2_32`。

- 全树唯一引用 `SurfGd` 字符串的文件是 **`dbfilter.dll` 的 `rva 0x88ec`**；
  `dbServ.dll` 内部只出现自己的名字 `dbServ`（`rva 0x9350`）。

**结论（精确版）**：存在**两套并行**的 DBFilter 安装器 ——
`dbServ.dll`（CxImage 库附带的安装器）与 `dbfilter.dll`（自带 `WSPStartup` 的实现体
+ 相同安装器）。二者写同一注册表位置，但**只有 `dbfilter.dll` 是真正的分层服务提供者**。
谁在主流程里被调用 → 需 `XREF` 追 `RunAfterSetup.exe`（→ `04-ENTRYPOINTS`）。

**教训（写入方法论）**：本行初稿从「导入表无 SurfGd」推出「不在网络路径上」，
越过了证据能支撑的范围。导入表只能证明**进程内链接**，不能证明**注册表写入行为**。
今后凡涉及「某模块是否做 X」，必须**直接观察 X 的产物**（注册表串、文件写、API 序列），
不得从链路缺失反推行为缺失。

对比：`dbfilter.dll` 导入 `SurfGd.dll` 的 6 个 `DBFP_*`，自身导出 `WSPStartup@0x13b0`。
**真正的 LSP 只有 `dbfilter.dll`。**

**遗留问题**：`dbServ.dll` 与 `dbfilter.dll` 两套安装器的调用优先级 → `04-ENTRYPOINTS`。

---

## K-02b `dbServ.dll` vs `dbfilter.dll`：主流程到底调哪个？ ✅ 已结案

**判定实验**：反汇编 `RunAfterSetup.exe` 的 LSP 安装序列。证据：`probe/p06_lsp_install.txt`。

`RunAfterSetup.exe` **不静态导入** `dbfilter.dll` / `dbServ.dll`，而是 `LoadLibraryA`
动态加载。`0x4013a7`–`0x40142c`：

```
IAT[0x415220] = KERNEL32!LoadLibraryA
IAT[0x415228] = KERNEL32!GetProcAddress

0x4013af  push 0x41a1e4            ; "..\DBFilter.dll"
0x4013b6  call 0x410608            ; 路径拼接
0x4013bd  call [0x415220]          ; LoadLibraryA → esi
0x4013d1  jne  0x401409            ; 失败则走错误分支（打印 0x41a1c8）

0x40140c  mov  edi,[0x415228]      ; GetProcAddress
0x401418  call edi                ; GetProcAddress(h,"DBFilterInstall")    → ebx
0x401420  call edi                ; GetProcAddress(h,"DBFilterIsinstalled") → ebp
0x40142c  call edi                ; GetProcAddress(h,"DBFilterUninstall")   → edi

0x401480  push 0x41a184            ; "..\SurfGd.dll"  ← 作为参数传给 DBFilterInstall
```

**裁决（CLOSED）**：

- 主流程加载的是 **`dbfilter.dll`**（字符串 `\DBFilter.dll`），**不是 `dbServ.dll`**。
- `\SurfGd.dll` 是作为**路径参数**传给 `DBFilterInstall`（对应注册表 `DBFilterPathName`）。
- `dbServ.dll` 的 3 个同名导出虽为真实实现，但**不在自启链上**。
  它更可能是 `cximage` 库构建时被 MPSvcDll 平台代码一同带出的派生物，
  或供安装器另一分支使用 —— 此为 `PROBING`，不影响主链结论。

**至此 K-02 全部结案。** 教训保留：初稿「不在网络路径」仍为 `REFUTED`（见下）。

---

## K-03 API Hook 引擎归谁？`Handler.dll` 还是 `HASrv.dll`？

| 方 | 断言 | 来源 |
|---|---|---|
| 旧 | `Handler.dll` 提供 `InstallApiHook`/`SetApiHookList`/`UninstallApiHook` | `REVERSE-ENGINEERING/README.md` 结论 3 |
| 新 | 这三个名字在 **`HASrv.dll`** | `05-INTERFACES-RAW.txt` |

**判定实验**：`probe/p03_exports.py` 导出表全量提取。

**结果**：
```
HASrv.dll    ord1 0x0011c0 InstallApiHook
             ord2 0x001810 LoadClient
             ord3 0x001160 SetApiHookList
             ord4 0x001340 UninstallApiHook

Handler.dll  GetEnable / HookFuncHandler / SetCtrolFlag / SetEnable /
             SetMainHwnd / SetProtectProcessID / SetProtectRegKey /
             SetProtectRegValue / TransmitString
```

**裁决**：旧结论 3 **REFUTED**。Hook 安装引擎在 `HASrv.dll`；`Handler.dll` 提供的是
**状态与保护面控制**（开关、主窗口、受保护进程/注册表项、字符串上报）。

---

## K-04 `FilterWords` 在哪个模块？

| 方 | 断言 | 来源 |
|---|---|---|
| 旧 | `MPSvcDll` 的 `WriteFileBin` 被 `XNet2` 使用 | `RE` 结论 22 |
| 新 | `FilterWords` 是 **`MPSvcDll.dll`** 导出，`Handler.dll` 无此导出 | `05-INTERFACES-RAW.txt` |

**判定实验**：同上。

**结果**：`MPSvcDll.dll ord6 0x001440 FilterWords`。`Handler.dll` 的 9 个导出里没有它。

**裁决**：与 GDE 仓库提交 `c716a4d "The export named FilterWords is the one that does
not filter"` 方向一致 —— 名字误导，真实过滤路径在别处。`status=PROBING`，
需追 `FilterWords` 的**调用方**才能定性。

---

## K-05 `FalunWord.lib` 结构：两套互斥布局

| 方 | 断言 | 来源 |
|---|---|---|
| 旧 A | 16 字节定长记录 `[id][sense_count][0xFFFFFFFE][UCS-2]`，哨兵出现 171,883 次 | `RE/README.md` #49#50 |
| 旧 B | `CFWordLib::LoadLib` 六段 `fread`，16 字节链节点，`next` 为**字符路径** | `GDE/docs/falunword-format.md` |

**判定实验（`ARITH`，最强证据）**：`probe/p01_closure_falunword.txt`

```
文件大小              : 5,564,271
GDE 布局算术          : 4 + 272920*16 + 65535*16 + 4 + 4693*16 + 4 + 36943*2
                      = 5,564,266
残差                  : 5   == len("HNCIT")        ← 零剩余
文件末 5 字节          : b'HNCIT'                    ← 实测为真
RE 布局哨兵命中        : 0        (RE 自称 171,883)
```

**裁决**：GDE 布局 **CLOSED**；RE #49 **REFUTED**（哨兵在 16 字节步长 `+8` 上命中 0 次）。

**方法论意义（重要）**：RE 文档错在「对字节做模式匹配再断言」；GDE 文档对在
「反汇编消费者代码得出布局，再用算术闭合验证」。**这条经验应推广到全案其余格式。**

---

## K-06 `FalunWord.lib` 的「条目数」：112,378 还是 4,693？

| 方 | 断言 | 来源 |
|---|---|---|
| RE#50 | 解码出 **112,378 个有效词条**（另 59,505 空槽） | `RE/README.md` #50 |
| GDE | trie = **272,920 字符节点**；打分表 = **4,693 条**；两层叠加 | `GDE/docs/hnc-filter-query-chain.md` |

**判定实验**：`ARITH` + 交叉核对 `CFWordLib::LoadLib` 的 `fread` 序列。

**结果**：
- 本账 C-001 已用算术闭合确认 `n1 = 272,920`（链节点数）、`n2 = 4,693`（词义记录数）、
  `n3 = 36,943`（字符池字数）。**GDE 的三个数字与算术闭合完全一致。**
- RE 的 112,378 来自已被推翻的 #49 布局（16 字节定长记录），该布局哨兵命中 0 次。

**裁决**：RE#50 `REFUTED`（依赖已推翻布局）；GDE 版 `CLOSED`（与算术闭合互证）。

**重要的结构性认识**（两条独立来源互证，可信）：
```
FalunWord.lib
 ├─ 第1层 trie    272,920 个 16B 链节点   ← 逐字符下降，出原子词
 └─ 第2层 打分表    4,693 条 16B 词义记录 ← 每条 (fan_dong, hong_hei)
      ↑ SearchWord(word,len,&out) 的 out 即第2层下标；
        out>=0 命中 / -1 在trie无分 / -2 trie里没有
```

> **这一点必须记为全案最重要的结构事实**：272,920 这个数字在本账的算术闭合
> （C-001）与 GDE 的实测（直调 DLL）中**独立出现且一致**。
> 两条互不相干的证据链指到同一个值，可信度极高。

---

## K-07 HNC 句类分析：产品到底用没用？

| 方 | 断言 | 来源 |
|---|---|---|
| RE#42 | 算法「完整存在于 SentenceObj.dll 与 HncEng.exe」 | `RE/README.md` #42 |
| GDE | 句类分析 **零调用点**（call / 绝对地址 / vtable 三查皆无）；产品只用词条过滤 | `GDE/docs/replication-status.md` |

**两者并不矛盾，但表述容易误导。** 需要拆成两个问题：

1. **`SentenceObj.dll` 里有没有句类分析代码？** → 有（72 导出含 `MakeALC`/`MakeSentSC` 等）。
2. **产品运行时会调用它吗？** → 这是争议点。

**已确证的部分（本账）**：
- `Handler.dll` 与 `HncEng.exe` 都**只导入 2 个** `SentenceObj` 函数：
  `CreateHncTextGroup` / `ReleaseHncTextGroup`（`p08_claims.txt`，RE#45 CLOSED）。
- 它们**都没有**导入 `GetSentenceALC` 之类的句法入口 ——
  因为那是 `SentenceObj` 的导出，**不在导入表里**。

**判定实验（待做）**：解开 vtable 后，`CreateHncTextGroup` 返回对象的 `+0x10`/`+0x14`
等槽位是否落到句法函数。RE#48 称 `HncEng.exe 0x4046F0` 有 `vtable[+0x10](1000,1,3)`。

### 判定实验（已完成，`probe/p09_hnc_syntax_dead.txt`）

三步，全部纯静态可重跑：

**① 句法函数的 vtable / 指针引用数**

| 函数 | 直接调用 | 指针引用 |
|---|---|---|
| `MakeALC` | **3** | 0 |
| `MakeObjFromSri` | 4 | 0 |
| `MakeObjFromJK` | 1 | 0 |
| `MakeObjList` | 3 | 0 |
| `CutWordMax` | **0** | 0 |
| `CheckVV` ×2 | **0** | 0 |
| `MakeSentSC` | **0** | 0 |
| `GetSentenceALC` | **0** | 0 |

`MakeALC` 的 3 个调用点分别落在 `CorpGroup` 的两个重载（`0x10002df0`/`0x10002e30`）
和 `GetALC`（`0x10004e70`）**体内** —— 词条级链是活的。

**② `CreateHncTextGroup` 到底构造了什么**

```
?CreateHncTextGroup@@YAPAVIHncTextGroup@@PAD@Z  @0x100050a0
  mov  eax,[esp+4]        ; path
  push 3 / push 2 / push eax
  call 0x10004fa0         ; ?OutputObjListIni@@YAHPADHH@Z
  cmp  eax,1
  mov  eax,[0x100b31e8]   ; 全局单例
  add  eax,0x124          ; ← 返回 主对象+0x124
  ret  4

?OutputObjListIni@0x10004fa0
  若 [0x100b31e8] 非空 → call ??1CFWordLib  (析构旧的)
  push 0x1004a0 → call operator new
  call ??0CFWordLib                        (构造)
  mov  [0x100b31e8],eax
  call ?LoadLib@CFWordLib                  ← 加载 FalunWord.lib
  成功 → [obj+0x100468]=2, [obj+0x100470]=3
```

**③ 全树检索确证**

`GetSentenceALC` / `MakeSentSC` / `CutWordMax` / `MakeObjFromJK` / `MakeObjFromSri` /
`MakeALC` / `CorpGroup` 这些名字，**只出现在 `SentenceObj.dll` 自身**（作为导出名表）。
`Handler.dll` 与 `HncEng.exe` 连 `GetProcAddress` 字符串都没有。

### 裁决：**GDE 结论成立，RE#42 表述误导**（`CLOSED`，**已修正**）

| 问题 | 答案 |
|---|---|
| `SentenceObj.dll` 里有句法代码吗？ | **有**，且内部互相调用形成闭环 |
| 产品会调用句法方法吗？ | **不会**（0 直接调用 + 0 指针引用） |
| `CSentenceObj` 对象被构造过吗？ | **会** —— `??0CFWordLib` 在 `+0x10013c` 处内嵌构造它 |
| 产品实际用到什么能力？ | **词库查询**（`CFWordLib` → `CorpGroup` → `MakeALC`） |

> **⚠️ 修正声明**：本行此前的版本有一条「`CSentenceObj` 被实例化过吗？静态证据显示没有」
> 是**错误**的。正确结论是「对象被构造，但其句法方法从未被调用」。
> 详见 `00c-CORRECTIONS.md` 错误 1。原因：我上轮追到 `new CFWordLib` 就停下，
> 没继续追踪 `??0CFWordLib` 构造器内部又一次 `call ??0CSentenceObj`。

RE#42 说「算法完整存在于 SentenceObj.dll 中」—— **代码确实在，对象也确实被构造**，
但它把「代码存在」当成了「算法被使用」。GDE 说「零调用点」—— **对句法方法而言正确**。

**K-07 结案（修正后）。** 附带收获：`CreateHncTextGroup` 返回的子对象 vtable=`0x451dc`
即 `IHncTextGroup` 接口（slot5=CorpGroup），与 GDE harness 观测的「子对象 `g`，
主对象在 `g-0x124`」完全吻合 —— 两个独立来源互证。

### 遗留（已撤销，见 00c-CORRECTIONS 错误2）

~~GDE harness 记 0x101000，本账读到 0x1004a0，差 2912 B~~ —— **这是把两个不同语义的
尺寸硬对比造成的伪差异**：`0x1004a0` 是 `CFWordLib` 本体的分配尺寸，`0x101000` 是
GDE 运行时观测值（含 CRT 对齐/cookie），另有 `0x4fc280` 是 `CSentenceObj::Ini` 的另一对象。
三者不是同一个东西，不应并排比对。本遗留**作废**。

---

## K-08 策略下标 17 是「允许自动关机」还是「允许自动抓屏」？

| 方 | 断言 | 来源 |
|---|---|---|
| RE#67 | 日志实证 `17.允许自动关机` | `RE/README.md` #67 |
| 实测 | `XNet2.exe` 第 17 项 = `17.允许自动抓屏` | `probe/p10_policy_table.txt` |

**判定实验**：GBK 编码检索 `XNet2.exe` 全文件。

**结果**：
```
'允许自动抓屏'    ->  YES @0x68e53
'允许自动关机'    ->  NOT FOUND
'17.允许自动关机' ->  NOT FOUND
'17.允许自动抓屏' ->  YES @0x68e50
```

完整 1–25 表位于 `0x68dbc`–`0x68f78`（倒序排列）。

**裁决**：RE#67 `REFUTED`。`17` = **允许自动抓屏**。

**为什么容易错**：`XNet2.exe` 里有**三套**编号东西：

1. 策略项 `1.`–`25.`（`0x68dbc`）← **`sys.dat` 用这套**
2. 日志事件分类 `{1.URL过滤}`–`{8.文字分析自动升级黑名单}`（`0xa0ca5`）
3. 策略明细 `A2.`–`A7.`（`0xa0ca5` 附近，与 2 交错）

第 1 套与第 2/3 套**毫不相干**，混用即出错。

**同时校正 RE#33**：RE 称 `sys.dat`「索引 3–22 有值、23–66 全零」。
实测**下标 24 亦非零**，正确边界是「3–24 有非零，25–66 全零」。

---

## K-09 RE#23「7895123 只在 XNet2.exe」的量词问题

| 方 | 断言 | 实测 |
|---|---|---|
| RE#23 | 该口令**只**在 `XNet2.exe`，`gn.exe` 中无 | `gn.exe` `0x4d48c` **也存在** |

**实测上下文**：
```
gn.exe  0x4d430  %s\help\kw.chm
        0x4d450  %s\xnet2.exe
        0x4d48c  7895123        <- 与 112233 相邻
        0x4d4cc  112233
        0x4d4d4  %s\%s
        0x4d4dc  kwpwf.dll
```

`gn.exe` 中的这两个串紧邻 `kwpwf.dll`，形态是一份**口令/密码文件相关字符串块**，
而 `XNet2.exe` 中的是 `CString::Compare` 的**比较目标**（控制流已证，K-05 之外的 RE#20）。

**裁决**：RE#23 `REFUTED`（「只在该文件」不成立）。
但「**比较代码**只在 `XNet2.exe`」这一更强、更有意义的断言**仍未被推翻** ——
`gn.exe` 里是数据，`XNet2.exe` 里是比较逻辑。二者不是一回事。

**方法论教训**：`存在性` 断言与 `行为性` 断言必须分开写。
「X 在文件 F 中」≠「F 用 X 做判断」。本账一律要求行为断言附控制流证据。

---

## K-10 旧文档的 IOC 清单整体可信，但**归属**有错

**判定实验**：`probe/p05_verify_iocs.py`，逐条在声称的模块里做字节检索
（narrow / GBK / UTF-16LE 三种编码）。

| 清单 | 声称模块 | 命中 | 结论 |
|---|---|---|---|
| 注入目标进程 | `InjLib32.dll` | **48/48** | ✅ 清单真实 |
| 进程终止名单 | `Handler.dll` | **22/22** | ✅ 清单真实 |
| 硬编码 URL | `XNet2.exe` | **9/9** | ✅ 清单真实（实为 9 条，非 10） |
| 硬编码 IP | `XNet2.exe` | **12/12** | ✅ 清单真实 |
| 注册表路径 | `XNet2.exe` | **10/12** | ⚠️ **2 条归属错误** |

**归属错误详情**：`...\Uninstall\FilterDam` 与 `...\Uninstall\KingWayKillPorn`
**不在 `XNet2.exe`**，而在 **`RunAfterSetup.exe`** `0x1a33c` / `0x1a1f8`。

**裁决**：清单内容整体可信（85/87 命中），但「全部属于 XNet2.exe」的说法 `REFUTED`。
`RunAfterSetup.exe` 才是注册表/卸载项的写方。

> **重要副产品**：`RunAfterSetup.exe` `0x1a100`–`0x1a5c0` 是一个**集中的命令行/路径字符串块**，
> 把整条自启链（LSP 安装、服务安装、COM 注册、自启项、防火墙放行、驱动安装）
> 全部明文列出。详见 `06b-INDIRECT-LINKS.md` A 节。**这比逐条反汇编高效得多。**
