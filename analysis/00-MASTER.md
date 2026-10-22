# 00 — MASTER LEDGER

Green Dam Youth Escort 3.17 / HNC 过滤器 — 逆向复原总账。

> **本文件是唯一权威索引。** 其他任何文档（`REVERSE-ENGINEERING/`、
> `GreenDamYouthEscort-2026/docs/`）中的断言都是**待证线索**，不是事实。
> 事实只能来自本账中标记为 `CLOSED` 的行，且每行必须有可重跑的命令。

---

## 0. 为什么要重建

上一轮解析（`REVERSE-ENGINEERING/` 76 条结论 + `GreenDamYouthEscort-2026/docs/`
20 篇）积累了约 **106 处自我更正**（更正/误判/推翻/作废/refuted/wrong）。
这不是说它们全错，而是说：**结论层不可信，测量层大体可信，但两者混在同一个
文档里**，无法区分。

典型症状：

- `.urf` 在结论 12 判「加密规则库」，结论 26 判「皮肤包」，26b–31b 又改 6 次。
- 结论 41 判「过滤算法不存在」，结论 42 立即推翻。
- `FalunWord.lib` 两套文档给出**互斥**的结构，且各自都声称已证明。

**处置方式：不继承任何结论。所有旧断言降级为 `CLAIM`（假设），逐条重新挣取。**

---

## 1. 反幻觉纪律（硬规则）

### 1.1 方法标签 — 只有这些能升级状态

| 标签 | 含义 | 能否 CLOSED |
|---|---|---|
| `ARITH` | 精确闭合算术（偏移/字节数严丝合缝） | ✅ **黄金标准** |
| `HASH` | 密码学哈希直接比对 | ✅ |
| `PE-HDR` | PE 头字段原样读出 | ✅ |
| `BYTES` | 原始字节 dump（含偏移） | ✅ |
| `STR` | 字符串提取 + 偏移 | ⚠️ 仅作线索，单凭不可 CLOSED |
| `DISASM` | 反汇编读出 | ⚠️ 需第二证据（XREF/UNICORN） |
| `XREF` | 交叉引用闭合 | ⚠️ 需指明引用方与被引用方地址 |
| `UNICORN` | 原二进制沙箱执行比对 | ✅ |
| `DOC-CLAIM` | 旧文档断言 | ❌ **计零** |

### 1.2 状态机

```
CLAIM  →  PROBING  →  CLOSED
                  ↘  REFUTED
```

- `CLAIM`：只存在于旧文档，尚未复验。**不得引用，不得作为其他行依据。**
- `PROBING`：已开工，证据不全。
- `CLOSED`：有 `probe/` 下可重跑命令，输出与记录一致。
- `REFUTED`：复验推翻。**必须保留原文与推翻证据**（防止再被重新发现）。

### 1.3 三条禁令

1. **不得从结论推结论。** 每行必须自己站住。
2. **冲突不靠文笔解决。** 冲突进 `07-CONFLICTS.tsv`，写明「判定实验」，
   实验跑完才改状态。
3. **`REFUTED` 不删除。** 幻觉的最大来源是把错误结论删掉后重新推导一遍，
   再得出同样的错误。

### 1.4 核验器自身的四类已知误差（已踩过）

写在本账里，因为每一条都实实在在把「实际上正确」的断言误判成 `NOT-FOUND`：

| # | 错误类型 | 实例 | 后果 |
|---|---|---|---|
| E1 | **大小写** | `Handler.dll` 名单存为 `\Regmon.exe` `\Tcpview.exe` `\tdimon.EXE`；核验器只 lower 了 needle 未 lower 数据 | 3 条假 NOT-FOUND |
| E2 | **VA vs 文件偏移** | RE 用 VA `0x4690ec`，核验器拿文件偏移 `0x690ec` 比对 | 误判 RE#20 偏移不符 |
| E3 | **显示截断** | 字符串在报告中截断为 60 字符，后半段被当成不存在 | 2 条假 NOT-FOUND |
| E4 | **编码层** | 版本资源是 UTF-8 字节，按 GBK 解成乱码；反之亦然 | 厂商归属全错 |
| E5 | **证据链未走到底** | 追到 `new CFWordLib` 就断言「只构造 CFWordLib」，漏了构造器内又 `call ??0CSentenceObj` | 「CSentenceObj 未实例化」假结论 |
| E6 | **不同语义的数值并排对比** | 把 CFWordLib 分配尺寸 0x1004a0 与 GDE 运行时观测 0x101000 硬比，得出伪差异 2912 | 制造了不存在的分歧 |
| E7 | **引用方地址未经校准** | 用 bisect 查函数归属时未验证导出地址表是否严格有序/无间隔 | 函数归属可能错位（已在 p10b 用全名消歧） |

**铁则**：任何 `NOT-FOUND` 结案前，必须先用 `grep -a` / `strings` 独立复核一次，
确认不是核验器自身的问题。**误否定（false negative）比误肯定更危险** ——
它会把真事实永久标为“不存在”，而后人不会再去看。

---

## 2. 台账文件

| 文件 | 内容 | 行单位 | 状态 |
|---|---|---|---|
| `01-FILES.tsv` | 全部文件：哈希/大小/类型 | 1 文件 | ✅ 117 行 |
| `02-MODULES.md` + `-RAW.tsv` | 全部 PE：机器/子系统/导出/导入/厂商 | 1 PE | ✅ 32 行 |
| `03-DATA-FORMATS.tsv` | 数据文件格式 + 闭合证明 | 1 格式 | ⬜ |
| `04-ENTRYPOINTS.md` | 函数级：地址/参数/调用方 | 1 函数 | 🟡 IPGate 完成 |
| `05-INTERFACES-RAW.txt` | 全量导出/导入（7,312 行） | 1 符号 | ✅ |
| `06-LINKS.tsv` | 模块间已证实调用/数据边 | 1 边 | ⬜ |
| `07-CONFLICTS.md` | 互斥断言 + 判定实验 | 1 冲突 | 🟡 K-01..K-05 |
| `08-CLAIMS-DEMOTED.tsv` | 旧断言全表 + 处置 | 1 断言 |
| `09-SPEC.md` | 复刻项目接口契约 spec | 工程规范 | ⬜ |

`probe/` 下每个脚本对应可重跑的验证命令，脚本编号与表行 `evidence` 列互指。

---

## 3. 当前进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 建账、定纪律（含核验器误差表 §1.4） | ✅ 完成 |
| P1 | 文件清单 + 哈希（`01-FILES.tsv`） | ✅ 117 行 |
| P2 | PE 模块表（`02-MODULES.md`） | ✅ 32 PE，含厂商/导出/导入 |
| P3 | 接口全量（`05-INTERFACES-RAW.txt`） | ✅ 7,312 行 |
| P4 | 入口还原（`04-ENTRYPOINTS.md`） | 🟡 IPGate 指令级完成 |
| P5 | 数据格式 + 闭合证明（`03-DATA-FORMATS.md`） | ✅ 8 格式，6 已闭合，`.urf` 冻结 |
| P6 | 跨模块调用边（`06-LINKS.tsv`）+ 间接边（`06b`） | ✅ 310 静态边；间接边 2/9 |
| P7 | 旧断言库存（`08-CLAIMS-DEMOTED.tsv`） | ✅ **99 条已落表** |
| P8 | 冲突裁定（`07-CONFLICTS.md`） | ✅ **K-01…K-10 结案；K-07 经 00c 修正** |
| P9 | 上轮结论自我复核（`00c-CORRECTIONS.md`） | ✅ **发现并纠正 3 处实质错误** |
| P10 | 统一机读事实表（`00b-FACTS.md`） | ✅ **52 行原子事实** |
| P11 | `.urf` 消费方定位（C-012） | ✅ **= appface.dll，UPX 已脱壳** |
| P14 | `sys.dat` 策略数据流（C-013） | ✅ **SG_Control(0x262) 链路闭合，映射代码证实** |
| P15 | MgTaki 驱动定性（C-014） | ✅ **注册表防护驱动，与 .urf 无关** |
| P16 | SG_Control 协议枚举（C-015） | ✅ **55 命令码 + 名单数组 + 策略下发** |
| P17 | DBFP_* 网络层契约（C-016） | ✅ **网络过滤机制闭合（层次见 C-017/C-018）** |
| P18 | 内容级 URL 过滤（C-017） | ✅ **五层过滤体系闭合** |
| P19 | 入站关键词过滤（C-018） | ✅ **双向过滤机制闭合** |
| P20 | 名单B语义确认（C-019） | ✅ **名单语义闭合（后增补为四名单 C-022）** |
| P21 | 关键词记录结构（C-020） | ✅ **规则语法与容量闭合** |
| P22 | 接口契约 spec（`09-SPEC.md`） | ✅ **工程规范已生成** |
| P23 | 图像过滤调用链（C-021） | ✅ **算法组合闭合** |
| P24 | 名单体系完整化（C-022） | ✅ **四名单 + 查询命令闭合** |
| P25 | UI照片交叉验证（C-023） | ✅ **16 项功能点全部对应，无盲区** |
| P12 | `.urf` 文件头精确（`p13_urf_header.txt`） | ✅ **10 字节前缀 + 固定块** |
| P13 | `.urf` 主体解包 | 🟡 **已明确是明文容器（GIF 可提取），元数据结构待解** |

### P7 库存结果（旧文档可信度画像）

| 状态 | 数量 | 含义 |
|---|---|---|
| `CLOSED` | **15** | 经本账独立复验为真 |
| `REFUTED` | **6** | 复验推翻 |
| `PARTIAL` | **4** | 方向对但措辞/计数/归属有误 |
| `CLAIM` | **74** | 未复验 —— **这就是剩余工作量** |

> 可用结论：旧文档不是「整体不可信」，而是**「已证实」与「未登记」混杂**。
> 已证实的 15 条集中在上游（文件存在、导出名、字符串、字节布局、清单命中）；
> 未复验的 74 条集中在**语义层与行为层**（HNC 引擎实际做了什么、`.urf` 解析、
> 三类截获路径的真实覆盖面）。
>
> **一个反直觉但重要的统计**：被推翻的 6 条里，4 条错在「计数/归属/范围」
> （50 vs 67 dword、33 vs 32 PE、XNet2 vs RunAfterSetup、只在该文件），
> 仅 2 条错在「结构本身」（FalunWord 布局）。
> **也就是说：旧文档的"测量"大多对，错的是"概括"。**

---

## 4. 已确认（CLOSED）结论

只有本节可以当作事实使用。每条附证据编号。

### C-001 `FalunWord.lib` 结构 = GDE 文档版本，RE 文档版本为误

- **方法**：`ARITH`（闭合算术）
- **证据**：`probe/p01_closure_falunword.txt`
- **事实**：
  - 文件 5,564,271 字节，末 5 字节 = `HNCIT`。
  - GDE 布局：`4 + 272920*16 + 65535*16 + 4 + 4693*16 + 4 + 36943*2 = 5,564,266`
    → 差 **5** = `len("HNCIT")`，**零剩余**。
  - RE 文档 #49 布局（16 字节定长记录、哨兵 `0xFFFFFFFE` 在 `+8`）
    → 在 16 字节步长上扫描，哨兵命中 **0** 次（RE 自称 171,883 次）。
- **裁决**：GDE 布局成立；RE #49 判 `REFUTED`。
- **意义**：这单次实验中，GDE 文档的方法学（「从消费者解码，而非猜字节」）
  明显优于 RE 文档（「模式匹配 + 宏观断言」）。

### C-002 文件清单基线

- **方法**：`HASH`
- **证据**：`probe/p01_inventory.py` 输出
- **事实**：117 个文件入库（`01-FILES.tsv`）。类型分布：
  PE 38 / BLOB 41 / TEXT 20 / GIF 12 / HTTP-RESPONSE 2 / CHM 1。
  **旧文档所称「76 个 PE」属误计 —— 实际 PE 仅 38 个。**
  安装树内 PE = **32 个**（非 33），`kwselectinfopp.dll`（672 B）非 PE。

### C-003 版本资源编码是 UTF-8，不是 GBK

- **方法**：`BYTES` + `PE-HDR`
- **事实**：`VS_VERSIONINFO` 中的中文字符串是 **UTF-8 字节**。按 GBK 解码得到乱码
  （`閲戞儬绉戞妧`），按 UTF-8 解码得到正确文本（`金惠科技---大正科技`）。
- **意义**：旧文档的厂商分组建立在 GBK 解码/手工判读之上，因此出错。

### C-004 厂商归属 = 「金惠 + 大正」联合署名

- **方法**：`PE-HDR`（版本资源）
- **证据**：`02-MODULES-RAW.tsv` 的 `company` 列
- **事实**：主控端系列（`gn.exe` `XNet2.exe` `XDaemon.exe` `looklog.exe` `lookpic.exe`）
  均为联合署名（连字符数各不相同）；`CImage`/`FImage`/`dbServ`/`dbfilter` = `金惠科技` 单方；
  `HASrv`/`Handler`/`InjLib32` = `hnc`；`Surfgd` = `KingWay Corporation`；
  `Xcv`/`Xtool`/`xcore` = `KingWay Tec.`。

### C-005 `IPGate.dll` 工作机理完全还原

- **方法**：`DISASM`
- **证据**：`04-ENTRYPOINTS.md` §A–B
- **事实**：
  - `Start(int)` → `SetWindowsHookExA(WH_GETMESSAGE=3, proc=0x100010c0, hMod=VirtualQuery(0x100021e0))`
    → 句柄存 `0x10015000` → 清 `0x1000a000` 的 1320 B 缓冲。
  - 钩子过程 `0x100010c0` = **纯 `CallNextHookEx` 透传**，不参与文字截获，
    作用仅为把 DLL 注入目标进程。
  - 真正的 inline hook 引擎：`0x100023c0`（装）/ `0x10002440`（卸），
    序列 = `GetCurrentProcess` → `FlushInstructionCache` → `VirtualProtect(..,0x40,..)`。
  - `SetParam(void*)` 拷贝 **9 dword = 36 字节** 到 `0x1000a528`。
  - `GetLogBuf(char*)` = `strlen`+`memcpy` 从 `0x1000a54c` 取串（紧接参数块）。
  - 上报出口：`SurfGd.dll!SG_Control`，槽 `0x10010110`，以
    `jmp dword ptr [0x10010110]`（`0x10003144`）尾跳。
  - GDI 四函数（`TextOutA/W` `ExtTextOutA/W`）IAT 槽**只被 thunk 读取**
    （`0x10001050` / `0x10001080` = `mov eax,[IAT]; ret`），无函数调用点。
    → 「导入但不调用」的具体形态是「把 IAT 槽当**补丁目标地址来源**」。

---

### C-006 全部数据格式的闭合状态

- **方法**：`ARITH` / `CODEC-RT` / `HASH`
- **证据**：`03-DATA-FORMATS.md`
- **事实**：
  - `sys.dat` ≡ `xconfigs.dat`，MD5 `b935aad6e250fd44fed3c76e965793ea`，
    **268 B = 67 dword**，值域 `{0,1,2,3,5,14}`，索引 23–66 全零。
    （RE 称「50 dword」→ REFUTED；GDE 称「67」→ 相符）
  - `kwselectinfopp.dll`：**672 / 84 = 8** 严丝合缝。记录 84 B，
    `+0x04` 进程名 / `+0x22` GBK 显示名。8 条已解出。
    （RE 早期「32 B 记录」→ REFUTED；现存「84 B」→ 相符）
  - `.dat` 词库编解码：**往返 5/5 通过**，`wfile.dat` 解出 5 行分类索引。
    确为无密钥可逆混淆。
  - `filtport.dat`：**33 B 完全明文**，2 条记录，无需解码。
- **`.urf` 仍为 CLAIM，全部旧结论冻结。** 旧文档在此文件上 7 次判定、6 次自我更正，
  不值得继续叠加。重建必须走「定位消费者代码 → 反推布局 → 算术闭合」路径。

### C-007 `MPSvcDll.dll` 存在真正的死导出

- **方法**：`IMP`（全树导入表）+ `BYTES`（全树字符串扫描）
- **证据**：`06-LINKS.tsv`；全树检索脚本输出
- **事实**：`FilterWords` / `UpdateWords` / `SnapShotScreen` / `WriteP2FileStr`
  四个导出**在全树零导入者**，且其名字串**只出现在 `MPSvcDll.dll` 自身**，
  连 `GetProcAddress` 的字符串字面量都没有。
- **意义**：这直接证实了 GDE 仓库那句「名字叫 FilterWords 的那个导出恰恰不干过滤」。
  **不能从导出名推断功能。**

### C-008 模块依赖图的机器推导

- **方法**：`IMP`（导入表 × 导出表 交叉）
- **证据**：`06-LINKS.tsv`（310 条边，全部 `CLOSED`），`probe/p04_links.py`
- **关键边**：
  - `dbfilter.dll → SurfGd.dll` × 6（`DBFP_Socket/Connect/Send/Recv/SendTo/RecvFrom`）
  - `IPGate.dll → SurfGd.dll!SG_Control`；`XNet2.exe → SurfGd.dll!SG_Control`
  - `Handler.dll → SentenceObj.dll` × 2；`HncEng.exe → SentenceObj.dll` × 2
  - `CImage.dll → kwimage.dll` × 26；`Xcv → xcore` × 145；`Xtool → xcore` × 31
  - `XNet2.exe → MPSvcDll.dll` × 12（全树最多）
- **结论**：`SurfGd.dll` 只有 **3 个消费者**（`dbfilter` / `IPGate` / `XNet2`）。
  这是网络层与屏幕层的唯一汇合点。

### C-009 HNC 句法子系统的真实状态（**已修正，见 00c-CORRECTIONS 错误1**）

- **方法**：`DISASM` + `XREF` + `BYTES` + `BYTESCAN`
- **证据**：`probe/p09_hnc_syntax_dead.txt`；`00c-CORRECTIONS.md`
- **修正后的正确事实**：
  1. **词条级链是活的**：`CorpGroup` 两个重载（`0x10002df0`/`0x10002e30`）
     与 `GetALC`（`0x10004e70`）体内直接 `call MakeALC`。（字节级 E8 扫描复核确认）
  2. **句法级方法无调用点**：`GetSentenceALC` / `MakeSentSC` / `CutWordMax` / `CheckVV`
     0 直接调用 + 0 指针引用（无内部调用者）。
     **但它们所属的 `CSentenceObj` 对象确实被实例化了**（见下）。
  3. **`CFWordLib` 构造函数内嵌构造 `CSentenceObj`**：
     `??0CFWordLib` @`0x10003e40`，在 `0x10003ec9` 处 `lea ecx,[esi+0x10013c]; call ??0CSentenceObj`。
     → **每次 `new CFWordLib` 都会自动构造位于 `+0x10013c` 的 `CSentenceObj` 子对象。**
  4. **`CreateHncTextGroup` 返回的子对象（`+0x124`）的 vtable 即 `IHncTextGroup` 接口**：
     `0x451dc` 的 slot5 = `CorpGroup`（词条入口）。
  5. **全树佐证**：句法函数名只在 `SentenceObj.dll` 自身出现；
     `Handler.dll`/`HncEng.exe` 连 `GetProcAddress` 字符串都没有。
- **意义**（修正后）：
  - `CSentenceObj` 对象**存在**（内嵌于 CFWordLib），但它的句法方法**从未被调用**。
  - 产品通过 `IHncTextGroup` 接口拿到的入口 = `CorpGroup` → `MakeALC`（词条级）。
  - 句法分析未接线，直接原因是句法层的 `Word.lib`/`rule.lib` 数据文件缺失。
- **附带互证**：`+0x124` 子对象与 GDE harness 观测的 `g-0x124` 完全吻合。

### C-012 `.urf` 的消费方是 `appface.dll`（皮肤库），不是 `SurfGd.dll`

- **方法**：`STR` + `IMP` + `PE-HDR` + `BYTES`
- **证据**：`probe/p12_urf_consumer.txt`
- **事实**：
  1. **`.urf` 前 10 字节共同前缀 = `a1 a0 ae ab bc a8 aa a8 a0 a8`**，7 个文件一致；
     另有 `0x0c-0x0d`（`25 9b`）与 `0x13-0x2e`（28 字节）亦是固定块（结构化头）。
     第 11 字节（`0x0a`）起才有变化字段。（详见 `probe/p13_urf_header.txt`，已修正早前「6 字节魔数」误记）
  2. `%s\%d.urf` 格式串在 `gn.exe`（`0x44d3c0`），由 `0x40b545` 的
     `sprintf(buf, "%s\%d.urf", dir, index)` 使用，index 被钳到 **1..8**。
     然后 `call [esi+0xc8](buf, "1PPRNFQ5YPKZI0UYV4U5GB5RKHDYNEKJ", 1, 0, 0)`。
  3. `gn.exe` 从 `AppFace.dll` 只导入 **ord3 = SkinWindowSet**。
  4. **`appface.dll` 是 UPX 加壳**（全树唯一加壳模块）：`UPX! @0x3e0`，
     段 `UPX0(RawSize=0)` + `UPX1` + `.rsrc`，导出全指向 UPX0 空区。
  5. `uRFG` 字符串出现在 `Surfgd/gn/XNet2/CardLib/IPGate/...` 等十余个模块，
     上下文完全相同的**同一段内联 strnicmp 代码**（不是 urf 解析），
     而 `appface.dll` 里的 `uRFG` 是数据区高熵上下文（不同东西）。
  6. **`.urf` 是明文容器，不是加密文件**（重大认知修正）：
     - `1.urf` 内 `0x6ccb`/`0x7a15` 两处是明文 `GIF89a` 图片资源
     - `_urf_images/` 里旧文档提取的 12 张 GIF 全部真实可复现（独立验证 6 偏移）
     - 熵 7.962 来自 GIF LZW 压缩数据，**≠ 加密**
     - 前 10 字节固定头，头→首个 GIF 之间是含 279 个 `0xa8` 分隔符的二进制元数据区
- **意义（推翻/确认旧断言）**：
  - 旧文档「`.urf` 解析器在 Surfgd.dll 或不在静态文件里」→ **都错**，解析器在 `appface.dll`，被 UPX 藏住。
  - 旧文档「AppFace 皮肤包」→ **对**（实锤）。
  - 旧文档 28b「提取 12 张 GIF、`a8` 分隔符、高熵=加密是误判」→ **对**（有实物证据，本轮复验）。
  - 旧文档「加密规则库」→ **错**（非规则库、非加密）。
  - `.urf` 之谜十轮缠绕的**根因** = 唯一加壳模块恰是它的消费方。
- **待办**：脱壳后静态分析 `SkinStart`/`SkinWindowSet` 对 `.urf` 头 10 字节、
  `0xa8` 分隔符、以及 GIF 资源的读取逻辑，直至算术闭合。

### C-013 `sys.dat` 策略数组数据流与下标映射（P0 完成）

- **方法**：`DISASM` + `IMP` + `STR`
- **证据**：`probe/p15_sysdat_flow.txt`
- **事实**：
  1. **`sys.dat` 没有任何 PE 模块静态读**（名字只在 `_MAPPING.tsv`/`xstring.s2g` 构建清单）。
     → 之前「sys.dat 由 XNet2 读取」的假设**不成立**。
  2. **`xconfigs.dat` 是 XNet2 写出的持久化副本**：`WriteFileBin("xconfigs.dat", 268B, "wb")`。
  3. **`sysEx.dat`（32B）也是 XNet2 写出的**（36 字节），非配置读入。
  4. **策略数组数据流**：`XNet2 全局@0x46f1e0 (67 dword)`
     → `SG_Control(0x262, 268, &array)`（命令码 610）
     → `SurfGd` 内部全局 `@0x10228008`（`EnterCriticalSection` + `rep movsd` 67 dword）。
  5. **`下标 = 策略号 − 1` 映射成立**（`XNet2 0x40bbd0` 初始化代码：
     `[esp+0x40]`=索引0=策略1，`[esp+0x88]`=索引18=策略19=抓屏保留时间14天）。
  6. **`SG_Control` 是 XNet2↔SurfGd 控制协议**：命令码分发表
     （字节表 `@0x7d18` + 跳转表 `@0x7ce0`），命令码 `0x262` = 接收策略数组。
- **意义**：
  - `sys.dat`/`xconfigs.dat` 内容相同（MD5 相同）——同一策略数组的两个副本；
    `sys.dat` 是安装模板，`xconfigs.dat` 是运行时持久化。
  - 策略语义与数据流已**算术级闭合**，可用于复刻项目的配置层。

### C-014 MgTaki 驱动 = 注册表防护驱动，与 `.urf` 无关（P1 完成）

- **方法**：`IMP` + `STR` + `PE-HDR`
- **证据**：`_drivers/` 三个 blob 的导入表 + 字符串
- **事实**：
  1. `IDR_DRIVER_SYS.bin`/`IDR_DRIVER_NT4.bin` = native PE 驱动（subsystem=NATIVE）；
     `IDR_DRIVER_VXD.bin` = VxD（LE 格式，含 `VtoolsD` Vireo 工具库字符串）。
  2. 驱动名 = **`mgtaki`**（PDB 路径 `emp\Taki\Free\mgtaki.pdb`、`Takint4`）。
  3. 完整导入 `Zw*` 注册表系列（`OpenKey`/`CreateKey`/`QueryValueKey`/`EnumerateKey`/
     `EnumerateValueKey`/`SetValueKey`/`DeleteValueKey`/`DeleteKey`/`Close`）
     + `IoCreateDevice`/`IoCreateSymbolicLink` + `IoRegisterShutdownNotification`。
  4. 驱动内**无 `urf`/`skin` 字符串** → 与 `.urf` 无关（RE#30b 断言成立）。
- **意义**：证实 RE#30b「MgTaki 是注册表防删改保护驱动，与 .urf 无关」正确。
  结合 C-012（.urf=appface 皮肤包），此前「驱动参与 .urf 解密」的早期假设彻底作废。

### C-015 SG_Control 控制协议完整枚举（P2 完成）

- **方法**：`DISASM` + `BYTESCAN`
- **证据**：`probe/p16_sg_control_protocol.txt`
- **事实**：
  1. `SG_Control` 是 **XNet2↔SurfGd 的核心控制协议**，命令码经 4 个区间 + 特定值的
     二级分发（字节表+跳转表），共 **55 个实现命令码**。
  2. **三个 144 字节记录数组**（每条记录首字段为字符串，小写化处理）：
     | 数组 | 基址 | 计数 | 设置命令码 |
     |---|---|---|---|
     | A | `0x1021bf18` | `0x10228120` | `0x192` |
     | B | `0x102186d8` | `0x10228124` | `0x1ba` |
     | C | `0x1003db80` | `0x1022811c` | `0x78` |
  3. **命令码 `0x32` = 单条添加记录**（上限 `0x2af8` = 11000 条）。
  4. **命令码 `0x262` = 接收 67 dword 策略数组**（配合 C-013）。
  5. XNet2 实际使用 **16 处调用**，覆盖约 12 个命令码（`0x32/0x78/0x118/0x1bb/
     `0x262/0x2bc/0x424/0xbc2/0xfa0/0xfa1/0x3e7` 等）。
  6. 全局标志区 `0x1022800c`–`0x10228034` 是单字节配置标志（成对命令码写/读）。
- **意义**：这是**配置层与规则层的核心接口契约**，可用于复刻项目的
  策略下发与动态名单管理。

### C-016 网络过滤层 DBFP_* 接口契约（P3 完成）

- **方法**：`DISASM` + `STR`
- **证据**：`probe/p17_dbfp_network.txt`
- **事实**：
  1. **LSP 链**：`dbfilter.dll`（`WSPStartup`）→ `SurfGd.dll`（`DBFP_*` 实际过滤）。
  2. `DBFP_CloseSocket`/`DBFP_Accept` 是**纯空操作**（返回 1）；`DBFP_Connect` 基本空操作。
     → 真正的过滤在 `DBFP_Socket`/`Send`/`Recv`/`SendTo`/`RecvFrom`。
  3. **过滤总开关** `0x1022800c == 2` 时全放行。
  4. **拦截方式**：返回 0 + 设置错误码 `0x2746`（10054 = `WSAECONNRESET`，连接重置）。
  5. **过滤层次**：进程名 → IP/端口 → 协议指纹 → URL/域名 → 内容URL提取。
  6. **进程判定**用 Toolhelp 快照（`CreateToolhelp32Snapshot` + `Process32First/Next` +
     `GetCurrentProcessId`，动态导入）。
  7. **IP 表**每项 7 字节（ip4+port2+flag1）`@0x1022806c`；
     **进程名/URL 名单** `@0x102221f0`（小写化 `strcmp` 匹配）。
- **意义**：这是复刻项目**网络过滤核心功能**的直接接口契约（过滤层次详见 C-017）。

### C-017 内容级 URL 过滤（P4 完成）

- **方法**：`DISASM` + `STR`
- **证据**：`probe/p18_dbfp_content.txt`
- **事实**：
  1. **`DBFP_Send` 做内容级 URL 过滤**（不只是 IP/端口）。
  2. **内容 URL 提取**：`strncmp(data+4, "http://"/"https:/", 7)` 识别 HTTP 请求，
     提取 URL 后查黑名单。
  3. **连接跟踪表** `0x10015200`（32 槽 × 16 字节，记录已过滤的 socket）。
  4. **协议头字符串**（`http://` 等）由 XNet2 下发（`SG_Control`），
     XNet2 侧确有 `http://`（0x599e0）/`GET`（0x699b8）/`POST`（0x599d8）。
  5. **名单 A 匹配**用 `strstr` 遍历 144 字节记录。
  6. **端口/协议指纹**：`strncmp(data+4, 运行时串, 4)`。
- **意义**：确认产品**过滤层次完整**：进程 → IP/端口 → 协议指纹 → URL/域名 →
  内容 URL 提取，是完整的网络+内容过滤体系。

### C-018 入站关键词内容过滤（P5 完成）

- **方法**：`DISASM`
- **证据**：`probe/p19_dbfp_recv_content.txt`
- **事实**：
  1. **`DBFP_Recv` 做关键词内容过滤**（对 HTTP 响应体）。
  2. **HTTP 端口集**：`{80, 81, 8080, 8081, 8888, 3128}`。
  3. **名单 C（`0x1003db84`）= 入站关键词黑名单**（144B/条）。
  4. 关键词记录含 `[`（`0x5b`）分隔符（分类/多词规则格式）。
  5. **拦截方式**：`memcpy` 替换响应内容为预设拦截提示（非直接断连）。
  6. **方向差异**：出站（`DBFP_Send`）= 返回 0 + 错误码 10054；
     入站（`DBFP_Recv`）= 替换响应内容。
- **意义**：确认产品**入站内容过滤**完整，且与出站机制不同。
  至此五层过滤体系两个方向均已闭合。

### C-019 名单语义确认（P6 完成，后增补见 C-022）

- **方法**：`DISASM`
- **证据**：`probe/p20_listB_process.txt`
- **事实**：
  | 名单 | 基址 | 语义 | 使用方 |
  |---|---|---|---|
  | A | `0x1021bf18` | 出站 URL/协议指纹 | `DBFP_Send` |
  | B | `0x102186d8` | **监控目标进程名单** | `DBFP_Socket` |
  | C | `0x1003db84` | 入站关键词黑名单 | `DBFP_Recv` |
  
  名单 B 的匹配机制（`0x10003499`）：
  `CreateToolhelp32Snapshot` 遍历进程 → 进程名小写化 → `strstr` 匹配名单 B →
  命中 PID 存入结果数组 `0x10218548`（100×4B）→ `0x10003340` 判定当前进程是否命中。
- **意义**：名单 B 语义闭合。名单 B 与 `kwselectinfopp.dll`（8 个静态监控目标）
  呼应 —— 那是初始名单，名单 B 是运行时下发。
  至此**监控目标、出站 URL、入站关键词**三类名单已确认（另有名单 D，见 C-022）。

### C-020 关键词记录结构与规则语法（P7 完成）

- **方法**：`DISASM`
- **证据**：`probe/p21_keyword_record.txt`
- **事实**：
  1. **144 字节记录** = 规则字符串（`+0`）+ 有效标志（`+0x84`）。
  2. **规则语法**（Surfgd 匹配引擎 `0x1000219e`）：
     - `{}` 组（多组间 AND）
     - `[]` 字符类（同类多词 OR）
     - `,` 词分隔符
     - 例：`{词A,词B}[字C,字D]` = （含A或B）且（含C或D）
  3. **容量**（`cmp [计数],imm` 实测）：名单 A=**100**、名单 C=1000、名单 D=1500、
     名单 B 无显式常量；单条添加总上限 11000（`0x2af8`@`0x1746`）。
     > 更正：本条初版把 A 记作 1500、B 记作 100，实为把名单 D 的容量错记到 A 上。
  4. **匹配引擎**：5 组嵌套循环 + `strstr`，全部组命中才返回 1。
- **意义**：完整还原了**过滤规则的配置格式与匹配语义**，
  复刻项目可直接据此实现等价规则引擎。

### C-021 图像过滤调用链（P8 完成；P26 修订）

- **方法**：`DISASM` + `IMP` + `STR` + `PE-HDR`
- **证据**：`probe/p22_image_filter.txt`、**`probe/p26_image_checkex.txt`**
- **事实**：
  1. **图像过滤链**：`xnet2 → CImage.dll!CAPSCREEN(抓屏) → CHECKEX*(检测) → DES2GO(处理)`。
  2. **CHECKEX2 反调试**：首参数必须 = `0x7ab7`(31415)，否则 `Sleep(10s)` + 返回 100。
  3. **两个算术判据**（P22 实测，已复刻）：
     - 宽高比检查 `0x10008e20`：短边 <32 不判；长/短 ≥ **10.0** 判异常
     - 高亮度检查 `0x10008e70`：R≥210 桶占比 ≥ **0.9** 判异常（**非肤色**）
  4. **P26 修订**（原 C-021 的三处过强/错误表述）：
     - ❌「3 种模式不同浮点阈值」→ 那两个函数各只有**一个**阈值；
       `0x10004a60` 的三种模式**未能在代码中定位**，原说法是推断。
     - ❌「颜色直方图/肤色」→ 实为**过曝/近白屏检测**（R≥210 占比）。
     - ❌「OpenCV 轮廓…全套」仅到导入表 → **P26 补证**：17 个 OpenCV 函数
       全部有真实调用（无死导入），**15 个 `CHECK*` 函数 3 层内可达**其桩。
  5. **P26 新事实**：
     - `CImage.dll` 有 **17 个** `CHECK*` 导出（`CHECK`/`CHECKEX`/`CAPSCREEN`/
       `CHECKEX2`–`CHECKEX8x`/`10x`–`13x`，**无 9**），账本此前只记 `CHECKEX2`。
     - 主判定逻辑在 `CHECKEX8x`(845 指令) / `CHECKEX13x`(1363) /
       `CAPSCREEN`(1353)，**语义未逆向**。
     - Haar 人脸检测（`FImage.dll` + `XFimage.xml`）与轮廓路径并存。
- **意义**：图像过滤的**算法组合**已知，但**主判定语义未逆向**。
  M4 因此只能复刻两个辅助判据，不能声称"等价检测"。
- **状态**：`PARTIAL` —— 见 `docs/image-layer.md` §5.2。

### C-022 名单体系完整化（P10 完成）

- **方法**：`DISASM`
- **证据**：`probe/p24_listD_and_queries.txt`
- **事实**：
  1. **存在第 4 个名单 D**（`0x10060e0c`，容量 1500，计数 `0x1022812c`），
     之前的三名单（A/B/C）并非全部。
  2. **名单 D 有完整的运行时增删接口**：添加 `@0x279b`（去重）、删除 `@0x2896`（memmove）。
  3. **命令码 `0xfa0` = 查询名单 D 命中**（strstr，0=命中/1=未命中）。
  4. **命令码 `0xbc2` = strlen**（标准优化实现，`0x7efefeff` 魔数）。
  5. XNet2 按状态码（100/300）查询 `0xfa0`/`0xfa1`。
- **意义**：名单体系从「三名单」修正为「四名单」，
  且补全了查询类命令码语义。

### C-023 UI 照片/手册交叉验证（P11 完成）

- **方法**：`CROSSCHECK`（用户手册截图 ↔ 逆向账本）
- **证据**：`probe/p25_ui_photo_crosscheck.txt`
- **事实**：
  1. 手册/照片的 **16 项 UI 功能点全部映射**到逆向机制路径，无缺口。
  2. 关键对应：
     - 「灵敏度 低/中/高」= `0x10004a60` 的 3 种检测模式
     - 「自动增加到黑名单」= 名单 D 运行时添加接口
     - 「语义分析」= HNC 词条链（SentenceObj + FalunWord.lib）
     - 「图像拦截」= CImage.dll CAPSCREEN/CHECKEX2（+ FImage Haar）
  3. 拦截提示有两套机制：网页小图替换（`image.dat` = HTTP 200 + 60×100 GIF）
     与全屏提示窗（gn.exe AppFace 皮肤弹窗）。
- **意义**：逆向账本**功能覆盖完整**，不存在「UI 有但逆向解释不了」的盲区，
  可作为重构项目的功能清单基线。

### C-011 数据文件名 → 函数归属（新增，解决 H010）

- **方法**：`DISASM` + `STR`（引用点反查）
- **证据**：`probe/p10b_datafile_refs.txt`
- **事实**：
  | 文件名 | 引用函数 | 层级 |
  |---|---|---|
  | `FalunWord.lib`（裸名） | `LoadLib` @0x22d0 | 词条层 |
  | `%s\FalunWord.lib` 等 | `BuildWordIndexFromFile` @0x49c0 | 文本索引 |
  | `Word.lib` / `rule.lib` | `SetSentBreak` @0x78e0 | **句法层（缺失）** |
  | `UserWord.lib` | `BuildWordMeanFromFile` | 用户词库（缺失） |
- **意义**：词条层与句法层用的是**不同的数据文件**；句法层的 `Word.lib`/`rule.lib`
  从未随包发布（`HNCLIB/` 目录实测仅 `FalunWord.lib` 一个文件）。

### C-010 `dbServ.dll` vs `dbfilter.dll`：主链只调 `dbfilter.dll`

- **方法**：`DISASM` + `STR`
- **证据**：`probe/p06_lsp_install.txt`；`06b-INDIRECT-LINKS.md` A
- **事实**：`RunAfterSetup.exe` `0x4013a7`–`0x40142c`：`LoadLibraryA(".\DBFilter.dll")`
  → 失败则报错 → 成功则 `GetProcAddress` 取 `DBFilterInstall`/`DBFilterIsinstalled`/
  `DBFilterUninstall` 三个 → 拼 `".\SurfGd.dll"` 作参数传给 `DBFilterInstall`。
- **裁决**：主链 = `dbfilter.dll`；`dbServ.dll` 的同名导出不在自启链上。

---

## 5. 下一步

1. **`.urf` 重建**（最高价值/最高风险）：必须先把「谁真正解析 `.urf`」追到函数级，
   然后做算术闭合。禁止任何字节猜测。
2. **`XNet2.exe` 策略数组 ↔ 策略项映射**：给 `sys.dat` 的 67 个下标钉语义。
3. **`06-LINKS.tsv` 补充间接边**：注册表写入（`Protocol_Catalog9`）、
   文件读取（`\hnclib\FalunWord.lib`）、COM 注册（`HncEngPS`/`MPSvcPS`）。
4. **`08-CLAIMS-DEMOTED.tsv`**：把 `RE` 的 76 条 + `GDE` 的 20 篇文档断言落行，
   逐条标 `CLAIM` / `CLOSED` / `REFUTED`。
5. **`00-MASTER` 状态同步**：每到一步就更新 §3 进度表。
