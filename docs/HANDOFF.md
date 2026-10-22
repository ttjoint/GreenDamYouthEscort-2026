# 交接文档

> **如果你是第一次读这个仓库，从这里开始。**
> 本文回答四个问题：现在做到哪了、哪些能信、哪些别信、接下来做什么。

最后核对：**1,247 passed / 10 skipped / 137 subtests**，`pytest tests/` 约 14 秒。

---

## 0. 三分钟概览

这是绿坝·花季护航（2009）的**重写**，不是移植。目标是在现代 Windows 上实现
同等的家长控制效果，用受支持的技术，并**刻意去掉**原厂所有恶意/有害行为。

仓库分两部分，读的时候要分开看：

| 部分 | 位置 | 是什么 |
|---|---|---|
| **逆向分析** | `analysis/` | 原厂二进制里到底有什么（取证，不产出产品代码） |
| **重写实现** | `src/green_dam/` | 新引擎，纯 Python，零运行时依赖 |

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
| M2 网络 | PARTIAL | 判定链完成；四段降级**只做规划**，真实代理需管理员权限 |
| M4 图像 | PARTIAL | 后端接口完成；原厂两条判定路径已还原；**真实 ONNX 模型未接入** |
| M6 进程 | PARTIAL | 名单 B 匹配完成；**终止进程未做**（需进程权限） |
| M7 基础设施 | PARTIAL | 配置/日志/密码/卸载清单完成；**服务宿主未做** |
| M9 产品 UI | TODO | `cli*.py` 是**诊断工具**，不是产品界面 |

### 1.2 需求追溯

`docs/requirements-traceability.md` 是权威表：

| 状态 | 数量 |
|---|---|
| COVERED | 77 |
| PARTIAL | 1 |
| **MISSING** | **11**（全部需外部条件，见 §6） |

### 1.3 代码规模

```
src/green_dam/   37 个模块，9,099 行
tests/           57 个文件，12,391 行
analysis/probe/  10 个探针脚本 + 27 份结论
```

**测试行数多于实现行数**，这是有意的：这个项目的价值在于"结论可验证"。

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

### 3.2 两个反复犯过的错误

**错误一：从「某函数不是 X」推出「产品没有 X」**

p22 发现 `0x10008e70` 不是肤色检测，就下了「产品没有肤色检测」的结论。
P28 发现真肤色检测在 `0x100096c0`，用 HSV 而非直方图。

> **教训**：否证一个位置 ≠ 否证一个能力。

**错误二：只看到导入表就下结论**

`IMG07` 曾以"导入了 OpenCV 全套"标 CLOSED。P26 查证发现**导入了 ≠ 调用了**。
补证后结论成立，但证据链必须到"调用可达"这一层。

> **教训**：证据要覆盖结论的强度。

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
| F9 真实 ONNX 模型 | 模型文件 + onnxruntime | 需选型与授权评估 |
| D10 真实代理/防火墙 | 管理员权限 + 真机 | 需 Windows 环境 |
| G1/G3 UIA + OCR | Windows API | 需 Windows 环境 |
| H3 终止进程 | 进程权限 | 需 Windows 环境 |
| I7/I8 服务 ACL 与恢复 | 服务权限 | 需 Windows 环境 |
| I12 执行卸载动作 | 系统 API | 需 Windows 环境 |
| J8 NTP 同步 | 网络 I/O | 属宿主 |
| K3 家长 UI | GUI 框架 | 需选型 |

**纯 Python 引擎侧已无待做项。**

---

## 7. 代码结构

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

按价值排序：

| 优先级 | 项 | 说明 |
|---|---|---|
| 高 | **F9 真实 ONNX 模型** | M4 唯一实质缺口；需选可商用许可的模型 |
| 高 | **Windows 宿主层** | 服务、代理、UI；需真机 |
| 中 | M6 终止进程 | 相对独立，需进程权限 |
| 中 | M2 真实代理 | 四段规划已完成，只缺执行 |
| 低 | 剩余 15 个 `CHECKEX*` | 边际收益低（阈值变体） |
| 低 | 低优先级补证 | SG_Control 剩余命令码、144B 记录后段字段、`.urf` 元数据 |

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

1,247 项测试里，很多是**否定性断言**（"绝不产生 allow"、"绝不编造 level"、
"绝不打印词条"）。它们存在的理由不是防回归，而是**防未来的自己走捷径**。

如果你要改某条规则，先读 `docs/requirements-traceability.md` 里对应那条需求
的来源。如果来源是 `architecture.md` 的明文要求，改之前先想清楚；如果来源是
逆向结论，先跑探针确认。

**宁可留一个 OPEN，不要编一个数字。**
