# 交接文档

> **如果你是第一次读这个仓库，从这里开始。**
> 本文回答四个问题：现在做到哪了、哪些能信、哪些别信、接下来做什么。

最后核对：**1679 passed / 15 skipped / 137 subtests**（`pytest`（testpaths 已含两个目录），共收集 1694 项），约 96 秒。
这个数字由 `tests/test_docs.py::TestCountClaimTest` 自动校对，不是手维护的。

---

## 0. 三分钟概览

这是绿坝·花季护航（2009）的**重写**，不是移植。目标是在现代 Windows 上实现
同等的家长控制效果，用受支持的技术，并**刻意去掉**原厂所有恶意/有害行为。

仓库分两部分，读的时候要分开看：

| 部分 | 位置 | 是什么 |
|---|---|---|
| **逆向分析** | `analysis/` | 原厂二进制里到底有什么（取证，不产出产品代码） |
| **引擎** | `src/green_dam/` | 判定逻辑，纯 Python，**零第三方依赖、禁止 I/O** |
| **宿主** | `host/` | Windows 集成：进程控制、服务、代理、托盘、UI |

引擎和宿主**都是 Python**。分界不是语言，是 **I/O**：引擎不碰
`socket`/`subprocess`/`ctypes`（契约测试强制），宿主负责全部系统调用。
理由与审计过程见 `docs/dependency-audit.md`。

**两者不是一回事。** 逆向结论是证据；实现是产品。实现**不要求**复刻原厂算法
——很多地方明确不复刻（见 §5）。

---

## 1. 现在做到哪了

### 1.1 模块状态

| 模块 | 状态 | 说明 |
|---|---|---|
| **M1 数据** | **DONE** | 四名单、144B 记录、规则语法、原厂 `.dat` 词库加载（9,143 条实测） |
| **M3 文本** | **DONE** | 分块、HNC 打分、双后端 A/B、句子相似度 |
| **M8 策略** | **DONE** | 67 槽策略、分→动作、7×24 时间网格 |
| M2 网络 | PARTIAL | 判定链完成；明文代理 + 入站响应体检查已做（`host/proxy/`），HTTPS 改浏览器扩展；**代理尚未接进服务** |
| M4 图像 | **DONE** | 后端接口 + Marqo 384 ONNX 后端 + 评分服务；**权重随包分发**（离线约束） |
| M6 进程 | PARTIAL | 名单 B 匹配 + 枚举/终止/保护已做（`host/processes.py`，默认 dry-run） |
| M7 基础设施 | PARTIAL | 配置/日志/密码/卸载清单完成；**服务宿主未做** |
| M9 产品 UI | PARTIAL | 控制台已有数据层 + 五个视图 + 回退窗口链（`host/ui/`）；**托盘与密码修改页未做** |

### 1.2 需求追溯

`docs/requirements-traceability.md` 是权威表：

| 状态 | 数量 |
|---|---|
| COVERED | 102 |
| PARTIAL | 2 |
| **MISSING** | **5**（全部需外部条件，见 §6） |
| **N/A** | **3**（明确决策不做，见 §汇总） |

### 1.3 代码规模

```
src/green_dam/   39 个模块，9,789 行
tests/           58 个文件，13,217 行
host/            25 个模块（含 proxy/ policy/ imaging/），5,300 行
host/tests/      12 个文件，3,881 行
tools/package.py 打包脚本，483 行
analysis/probe/  10 个探针脚本 + 27 份结论
```

**测试行数多于实现行数**，这是有意的：这个项目的价值在于"结论可验证"。
宿主层同样如此（1,821 行测试 vs 2,532 行实现，比例接近）。

---

## 2. 哪些能信

### 2.1 方法标签制

`analysis/` 里每条事实都带 `method` 字段。**可信度分级**：

| 标签 | 含义 | 可否单独定论 |
|---|---|---|
| `ARITH` | 算术推导 | ✅ |
| `HASH` | 哈希校验 | ✅ |
| `PE-HDR` | PE 头/导出表/导入表 | ✅ |
| `BYTES` | 字节级实测（含常量提取） | ✅ |
| `UNICORN` | 模拟执行 | ✅ |
| `CODEC-RT` | 编解码往返验证 | ✅ |
| `MANUAL` | 产品手册截图 | ✅（UI 是行为证据） |
| `DISASM` | 反汇编观察 | ⚠️ **需第二证据** |
| `XREF` | 交叉引用计数 | ⚠️ **需第二证据** |
| `DOC-CLAIM` | 旧文档的说法 | ❌ **计零** |

**`REFUTED` 的记录永不删除**——被推翻的结论留在账本里，因为"为什么错"
本身是信息。

### 2.2 探针必须可复现

每个结论都有对应探针（`analysis/probe/pNN_*.py`），只读、可重跑：

```bash
cd LH-setup3.17
PYTHONIOENCODING=utf-8 python GreenDamYouthEscort-2026/analysis/probe/p27_checkex13x.py
```

**只信有探针的结论。** 没有探针的结论是待验证的假设。

---

## 3. 哪些别信（重要）

### 3.1 旧文档已被多次推翻

`analysis/probe/` 按时间累积，**后写的会推翻先写的**。阅读顺序表在
`analysis/README.md`，摘录：

| 主题 | 以哪个为准 | 已被推翻的旧说法 |
|---|---|---|
| 图像判定 | **p28 > p27 > p26 > p22** | 「肤色检测不存在」「3 种模式」「轮廓=主判定」 |
| 时间控制 | **p29** | 「槽 21 是时段数据」 |
| 名单容量 | p21（已修正） | 「A=1500 / B=100」 |
| 名单数量 | p24 | 「三名单」 |
| `.urf` | p13 | 「加密规则库」 |

### 3.2 三个反复犯过的错误

**错误一：从「某函数不是 X」推出「产品没有 X」**

p22 发现 `0x10008e70` 不是肤色检测，就下了「产品没有肤色检测」的结论。
P28 发现真肤色检测在 `0x100096c0`，用 HSV 而非直方图。

> **教训**：否证一个位置 ≠ 否证一个能力。

**错误二：只看到导入表就下结论**

`IMG07` 曾以"导入了 OpenCV 全套"标 CLOSED。P26 查证发现**导入了 ≠ 调用了**。
补证后结论成立，但证据链必须到"调用可达"这一层。

> **教训**：证据要覆盖结论的强度。

**错误三：每边都有测试，中间的接缝没人测**

2026 年 10 月发现：`background.js` 一直在往 `127.0.0.1:17871/score` POST
图片，而那个端口上**从未有过监听**。扩展的图片过滤因此完全空转。

它没被发现，因为两端各自都很健康：规则在 `test_extension.py`，模型在
`test_imaging.py`，引擎的阈值映射在 `test_imagepath.py`。缺的是它们之间那个
HTTP 端点，而它不属于任何一边。

同一形状的第二个洞在同一周找到：`Filter.image_backend` 字段从第一天就在，
但 `host.engine.open_filter_from` **没有任何参数能设置它**——宿主建出来的
Filter 永远答 `no_model`。引擎侧测试直接构造 `Filter` 所以通过，宿主侧测试
从不问图片所以也通过。

> **教训**：测试覆盖面不是模块的并集。两个都测过的模块之间的**接线**，默认
> 是零覆盖。看到“这个功能做完了”时，问一句：它的两端之间的那条线，谁在测？
>
> 两个洞都已补上（`host/imaging/service.py`、`open_filter_from(image_model=)`），
> 且都做了变异验证：删掉接线会让测试变红。

### 3.3 具体已作废的假设（25 条）

完整清单在 `analysis/00c-CORRECTIONS.md` 与对话记录。最常被误引的：

| 作废假设 | 真相 |
|---|---|
| `.urf` 是加密规则库 | 明文 GIF 容器，熵来自 LZW |
| `sys.dat` 是 50 dword | **67** |
| 名单 A 容量 1500 | **100**（A）/ 1000（C）/ 1500（D）/ B 无显式常量 |
| 宽高比有三组浮点阈值 | 只有一个 10.0（另有整数 8，属不同函数） |
| 颜色直方图 = 肤色检测 | 是**过曝/近白屏**检测 |
| HNC 全部命中都是证据 | 89% 是等级 0，会误报（`clea` 命中 clean） |
| `CSentenceObj` 从未实例化 | 内嵌在 `CFWordLib+0x10013c` |
| 三名单完整 | 存在第四名单 D |

---

## 4. 设计原则（改代码前必读）

### 4.1 判不了 ≠ 判为干净

这是全项目最重要的一条。具体体现：

| 场景 | 正确 | 错误 |
|---|---|---|
| 门控关闭 | `reason="gated"` | `allow` |
| 没有词表 | `reason="no_wordlists"` | `reason="no_evidence"` |
| 没有图像模型 | `assessed=False` | `nsfw=0.0` |
| 分块不可靠 | 拒绝评分 | 给个看似合理的分 |
| 卸载无法撤销 | 报告 `unreversible` | 静默跳过 |

测试 `test_invariants.py::GateSemanticsTest` 钉住这些。

### 4.2 不伪造未知的数值

| 场景 | 处理 |
|---|---|
| `SENTEN.LIB` 缺失 → 句类整数 | `to_frame_hint()` 返回 `None`，不编造 |
| 原厂 分→动作 映射未知 | `level_actions={}`，`attested=False` |
| 8 项权重表来源未知 | 记录为 OPEN，不解释成"有依据" |
| 矩形分析 0.54/252 语义未明 | `rect_analysis()` 抛 `NotReversed` |

### 4.3 测量与策略分离

`ScreenResult`、`HNC 命中`、`SimilarityScore` 都**不携带动作**。
动作由调用方传入。理由：原厂映射没还原，编一个等于假装知道。

### 4.4 每次调用只写一条日志

`Filter.check()` 曾有 bug：gated + text 记了 2 条。现在统一在 `_gate()` 收尾，
`test_invariants.py::SingleRecordInvariantTest` 钉住。

### 4.5 脱敏

- 日志只存**内容摘要**（截断 SHA-256），不存原文
- CLI 默认只打印计数，不打印词条
- `Finding.__repr__` / `PasswordRecord.__repr__` 都不显示敏感字段
- 测试样本**一律虚构**（甲乙丙丁 / example.com）

---

## 5. 刻意不做的（不是遗漏）

`test_contracts.py::OmissionTest` 逐条验证"确实没做"：

| 不做 | 理由 |
|---|---|
| 全局键鼠钩子 | UIPI/会话隔离下不可行，且是恶意软件行为 |
| DLL 注入 | 同上 |
| 内核驱动 | 驱动签名强制 |
| 明文 HTTP 下发可执行文件 | 原厂安全债 |
| 硬编码密码 | 原厂 `112233` 的教训 |
| 明文上传收集数据 | 原厂安全债 |
| 写 `%WinDir%\System32` | 原厂安全债 |
| 按进程名杀安全工具 | 恶意软件行为 |
| 隐藏进程/服务 | 同上 |
| 阻止任务管理器 | 同上 |
| 卸载残留注册表 | 原厂缺陷 |
| 强制重启 | 原厂缺陷 |
| **复刻原厂轮廓/NSFW 算法** | 架构目标是 ONNX 分类器，逆向是考古 |
| **继续逆向剩余 15 个 `CHECKEX*`** | 已还原两个最大的；剩余多为阈值变体 |

---

## 6. 剩余 MISSING（全部需外部条件）

| 项 | 需要什么 | 阻塞程度 |
|---|---|---|
| F7 其余 15 个 `CHECKEX*` | 大规模逆向 | 收益低，非目标 |
| I7/I8 服务 ACL 与恢复 | 服务权限 + 真机 | 需 Windows 环境 |
| I12 执行卸载动作 | 系统 API | 需 Windows 环境 |
| J8 NTP 同步 | 网络 I/O | 属宿主 |

已完成但需记录一下的（原先列在这里）：

| 项 | 现在在哪 |
|---|---|
| F9 ONNX 模型 | `host/imaging/`（Marqo 384）+ `host/imaging/service.py`（评分端点） |
| D10 代理/防火墙 | `host/proxy/` `host/firewall.py` |
| H3 终止进程 | `host/processes.py` + `host/protection.py` |
| K3 家长 UI | `host/ui/`（PARTIAL：托盘与密码页未做） |
| G1/G3 UIA + OCR | **N/A**：决策不做，由 F13 DOM 级过滤替代 |

**纯 Python 引擎侧已无待做项。**

---

## 7. 代码结构

### 7.0 为什么不是 .NET

架构文档最初写"宿主是 .NET 8"，**没有给出理由**。开始宿主层前做了逐模块
审计（`docs/dependency-audit.md`），结论是**零个模块需要 .NET**：

| 需求 | Python 方案 |
|---|---|
| Windows 服务 + ACL | `pywin32`（已验证） |
| MITM 证书 | `cryptography` |
| 防火墙规则 | `netsh` / COM |
| 进程枚举与终止 | `psutil` |
| UI | `pywebview` + HTML |
| ONNX 推理 | `onnxruntime` |

唯一降级是 **M5 的 WinRT 绑定由社区维护**，不如 .NET 官方绑定稳。
这条如实记录，不掩饰。

`test_contracts.py::SingleLanguageTest` 断言仓库里不存在 `.cs`/`.csproj`/`.sln`
等文件——防止有人"顺手加个 .NET 小工具"让这个决定悄悄失效。

### 7.1 分层（`test_contracts.py::LayeringTest.ALLOWED` 是权威表）

```
L0 叶子   sysdat eventlog namelist imaging* hnc l_concepts
          chunk_tags wordlists schedule secret journal legacy_words imagepath
L1        config gate namelists policy chunker transport
L2        intercept semantic backends similarity chunkpath simpath textpath
L3        filter replay
L4        cli cli_admin cli_filter cli_lifecycle
```

**无循环依赖，依赖单向。** 新增模块/新边必须显式更新 `ALLOWED`。

### 7.2 规模约束（`test_structure.py`）

| 项 | 上限 | 豁免 |
|---|---|---|
| 模块行数 | 450 | `chunker`（1,192，语言规则本身复杂） |
| 类行数 | 350 | `chunker.SentenceChunker` |
| 函数行数 | 90 | `chunker._chunk_clause`、`hnc.classify` |
| 嵌套深度 | 5 | — |
| 每模块顶层定义 | 14 | — |

**豁免必须附理由，且测试会验证被豁免的符号仍存在。**

### 7.3 测试分层

| 层 | 文件 | 作用 |
|---|---|---|
| 单元 | 各模块 | 单函数行为 |
| **结构** | `test_structure.py` | 代码还保持可维护吗 |
| **契约** | `test_contracts.py` | 分层/零依赖/无 IO/不做清单 |
| **不变量** | `test_invariants.py` | 门控语义/单条日志/脱敏 |
| 端到端 | `transport` `evidence_paths` `backends` | 子系统串起来 |

**契约测试必须做变异验证**——一个永远通过的契约测试等于没有。
（本项目验证过 5/5 全部能抓到：反向 import、`socket`、`numpy`、硬编码密码、
import 时 `open()`。）

---

## 8. 常见陷阱

### 8.1 环境

- 宿主是 **Git Bash (MSYS2)**，不是 Linux。`/tmp` = `D:\_Temp`，但
  **Python 不认这个映射**——给 Python 传路径要用 `D:/_Temp/...`
- 必须 `PYTHONIOENCODING=utf-8`，否则 GBK 输出乱码
- `pytest tests/` 全量约 14 秒

### 8.2 修改代码时

| 陷阱 | 后果 |
|---|---|
| 新增第三方 import | `DependencyTest` 失败 |
| 新增模块不登记 `ALLOWED` | `LayeringTest` 失败 |
| 函数超 90 行 | `StructureTest` 失败（**先想拆，不是先加豁免**） |
| 注释里出现 `System32`/`SetWindowsHookEx` 等 | `OmissionTest` 误报（改写措辞） |
| 测试打印真实词条 | 违反脱敏纪律 |

### 8.3 改逆向结论时

1. **先跑探针**确认，不要只读文档
2. 新结论写进 `analysis/probe/pNN_*.txt` + `.py`
3. 更新 `00b-FACTS.md` 的 `method`/`status`
4. 如果推翻了旧结论，**在 `analysis/README.md` 阅读顺序表里登记**
5. 旧结论标 `REFUTED`，**不删除**

---

## 9. 接下来做什么

宿主层（`host/`）已开工：H0 骨架、H1 进程控制、H2a 防火墙、H2b 明文代理与入站检查、
H4a 浏览器扩展、H5 图像模型、**H5b 评分服务**、H7 打包已完成，见
`docs/host-layer.md`。剩下的事按**是否卡在未决决策上**分组：

**不卡在决策上，可以接着做**：

| 优先级 | 项 | 说明 |
|---|---|---|
| 高 | **H3 服务宿主** | 把代理、评分服务、进程监控装成一个 Windows 服务（ACL + 恢复动作 + IPC）。今天这些都要手工各自启动 |
| 高 | **H4 托盘 + 密码 UI** | 控制台已有数据层与五个视图（K3 PARTIAL），缺托盘图标与改密码页 |
| 中 | **M2 连接归属（进程名）** | Windows 连接表查询，属代理与 M6 的交界；`ProxyConfig.process` 已留位 |
| 低 | 剩余 15 个 `CHECKEX*` | 边际收益低（阈值变体） |
| 低 | 低优先级补证 | SG_Control 剩余命令码、144B 记录后段字段、`.urf` 元数据 |

**待拍板的只有两件**（其余已落定）：

| 项 | 卡在哪 | 影响 |
|---|---|---|
| H4 打包形态 | **Program Files 还是便携目录** | 已写入 `host-layer.md` §2.13 的四种组合对比；默认建议 Program Files |
| H3 服务账号 | **LocalSystem 还是专用账号** | 同上；默认建议专用账号 |

已落定的决策，不要再当未决项：

| 决策 | 结论 |
|---|---|
| F9 模型选型 | Marqo 384 ONNX（Apache-2.0），已实测，见 `image-backend.md` 附录 |
| H5 模型分发 | **随包分发**。产品离线，"装完再下载" 在离线机器上等于功能不存在（`host-layer.md` §2.12） |
| 词库分发 | **默认不带，用户下载后 `green-dam words import` 导入**。理由见 §2.8：原厂词表实测无效 |
| 程序更新 | **用户下载新安装包手工覆盖**，无自动更新、无 `update_url` |
| H2c HTTPS MITM | **不做**，改浏览器扩展（`host-layer.md` §2.6） |
| H6 M5 屏幕 OCR | **不做**，全屏 OCR 等于录屏（`host-layer.md` §2.7） |

---

## 10. 文档地图

### 先读

| 文档 | 内容 |
|---|---|
| **本文** | 全景、可信度、陷阱 |
| `docs/architecture.md` | 模块职责、目标、不做清单 |
| `docs/status-checklist.md` | 逐模块 DONE/PARTIAL/TODO |
| `docs/requirements-traceability.md` | 需求 → 测试映射（权威） |
| `analysis/README.md` | **探针阅读顺序（防误读）** |

### 按模块

| 模块 | 文档 |
|---|---|
| M1 | `legacy-wordlists.md` `falunword-format.md` `legacy-data-import.md` |
| M2 | `network-layer.md` |
| M3 | `text-layer.md` `backends.md` `chunk-layer.md` `similarity-layer.md` `verb-table.md` |
| M4 | `image-layer.md` `image-backend.md` `image-recognition.md` |
| M7 | `config-persistence.md` `event-log.md` `password.md` `uninstall.md` |
| M8 | `policy-layer.md` |
| 门面 | `facade.md` |
| **宿主** | **`host-layer.md`** |
| CLI | `cli.md` |
| 结构 | `structure-audit.md` |

### 逆向专项

| 文档 | 内容 |
|---|---|
| `engine-call-graph.md` | 原厂到底做不做句法分析（结论：不做） |
| `replication-status.md` | HNC 复刻成熟度 |
| `module-inventory.md` | 33 个原厂模块分别是什么 |
| `legacy-engine-manual.md` | 原厂算法完整留档 |
| `hnc-theory.md` | HNC 理论与代码的映射 |
| `legacy-control-files.md` | 控制文件分类与 `.urf` 定性 |

---

## 11. 一条给后来者的建议

这个项目最大的资产不是代码，是**记录下来的"为什么"**。

1694 项测试里，很多是**否定性断言**（"绝不产生 allow"、"绝不编造 level"、
"绝不打印词条"）。它们存在的理由不是防回归，而是**防未来的自己走捷径**。

如果你要改某条规则，先读 `docs/requirements-traceability.md` 里对应那条需求
的来源。如果来源是 `architecture.md` 的明文要求，改之前先想清楚；如果来源是
逆向结论，先跑探针确认。

**宁可留一个 OPEN，不要编一个数字。**
