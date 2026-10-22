# 结构审计（2026-XX，重构前基线）

本文件记录**一次性的结构体检结果**与处置决定。它不是进度表，是设计债清单：
每一项要么被修，要么明确接受并写理由。

审计方法：`ast` 静态分析 —— 模块依赖图、扇入/扇出、顶层定义规模、函数长度。
可复现，见 `tests/test_structure.py`（本次新增，把这些约束变成测试）。

---

## 1. 结论摘要

| 维度 | 结论 |
|---|---|
| 模块耦合 | **健康**。无循环依赖，最高扇入 4（`sysdat`），依赖方向单向 |
| 模块规模 | **健康**。20 个模块，5,753 行，平均 287 行，无巨型文件 |
| 类/函数规模 | **3 处超标**，见 §3 |
| 职责混杂 | **1 处明显**：`Filter` 承担 5 种职责 |
| 测试覆盖 | 每模块都有对应测试，但缺**结构性**约束 |

一句话：**分层是对的，但 `Filter` 内部是个"上帝对象"的雏形。**

---

## 2. 依赖图（审计时实测）

```
L0 叶子   sysdat  eventlog  namelist  imaging  hnc  l_concepts
          chunk_tags  wordlists
L1        config→sysdat            namelists→namelist
          policy→sysdat,wordlists  chunker→chunk_tags
          transport→intercept
L2        intercept→eventlog,namelists
          semantic→policy          backends→semantic
          similarity→chunker
L3        filter→(9 个)            replay→filter,transport
L4        cli→(9 个)
```

| 模块 | 扇入 | 扇出 |
|---|---|---|
| `sysdat` | **4** | 0 |
| `eventlog` | 3 | 0 |
| `intercept` | 3 | 2 |
| `filter` | 2 | **9** |
| `cli` | 0 | **9** |

**判定**：扇入 4 是健康的。`filter` 和 `cli` 扇出 9 是**聚合点的正常表现**
（编排层本来就该知道所有零件）。真正的风险不在扇出，而在 §3。

---

## 3. 超标项与处置

### 3.1 `filter.Filter` —— 5 种职责混杂（**必须拆**）

344 行，7 个字段，5 类职责：

| 职责 | 成员 | 处置 |
|---|---|---|
| 配置持有 | `legacy`, `policies` | 保留 |
| 门控决策 | `_gate`, 3 个 property | **抽出 `gate.py`** |
| 网络判定 | `check_request`, `check_process` | 保留 |
| 文本判定 | `_text_findings`, `_backend_findings`, `_text_verdict`, `_verdict_from_findings` | **抽出 `textpath.py`** |
| 图像判定 | `check_image` | 保留 |
| 编排 | `check()` 80 行 | **拆成子步骤** |

这是本次审计最主要的发现：`Filter` 同时是"配置对象"、"门控器"、"文本流水线"
和"编排器"。**文本判定那 4 个方法跟网络/图像毫无关系**，却被塞在一起。

### 3.2 `chunker.SentenceChunker` —— 600 行（**接受，标注**）

| 成员 | 行数 |
|---|---|
| `_chunk_clause` | 128 |
| `_chunk_main` | 85 |
| `_strip_zhe_companion` | 63 |

**处置：接受现状，但记录理由。**

这是逆向还原的中文分块器，其复杂度来自**语言规则本身的复杂度**，不是设计
问题。强行拆成多个类会引入共享状态传递，反而更难对照逆向结论核对。

缓解措施：`chunker.py` 已单独成文件（不与其他职责混），且有 `test_chunker.py`
覆盖。若将来要拆，边界应是"子句切分 / 主块判定 / 附属成分剥离"三段。

### 3.3 `namelists.NameList` 122 行 / `intercept.Interceptor` 100 行

**处置：接受。** 二者都是"一个概念 + 它的操作"，122/100 行属于合理范围。
`NameList` 的成员（增删查序列化）全部围绕同一不变量，拆开反而要暴露内部。

### 3.4 其他 >60 行的函数

`policy.assess` 74、`namelist.parse_rule` 76、`cli.build_parser` 75、
`hnc.classify` 112 等：均为**单一算法或单一声明块**，不涉及职责混杂。
接受。

---

## 4. 缺失的结构约束（本次补上）

审计发现：**没有任何测试约束"模块不该变得过大"**。`filter.py` 长到 424 行、
`check()` 长到 80 行，测试全绿。

新增 `tests/test_structure.py`：

| 约束 | 上限 | 理由 |
|---|---|---|
| 单模块行数 | 450 | 超过说明该拆 |
| 单类行数 | 350 | 超过说明职责过多 |
| 单函数行数 | 90 | 超过说明该抽子步骤 |
| 顶层定义数/模块 | 12 | 超过说明模块太杂 |
| 公共方法数/类 | 25 | 同上 |

**豁免机制**：`chunker.SentenceChunker` 等已审计接受的项目，在测试里显式
列为 `ALLOWED_OVERSIZE` 并附理由。新增超标必须**显式登记**，不能悄悄溜过。

这比"写进文档提醒自己"可靠：文档会过期，测试不会。

---

## 5. 处置清单

| # | 项 | 状态 |
|---|---|---|
| 1 | 抽出 `gate.py`（开关语义单一来源） | **DONE** |
| 2 | 抽出 `textpath.py`（文本证据流水线） | **DONE** |
| 3 | `check()` 拆子步骤 | **DONE**（80 → 40 行） |
| 4 | 新增 `test_structure.py` | **DONE**（15 项） |
| 5 | `chunker` 保持现状，登记豁免 | **DONE** |
| 6 | 更新分层契约测试的 `ALLOWED` | **DONE** |

## 5b. 重构结果

| 指标 | 重构前 | 重构后 |
|---|---|---|
| `filter.py` | 424 行 | **337 行** |
| `filter.Filter` | 344 行 | **272 行** |
| `filter.check()` | 80 行 | **40 行** |
| 超 90 行的函数 | 1（`check`） | **0**（新增项） |
| `Filter` 职责数 | 5 | **3**（配置 / 编排 / 各路径委托） |

新增两个模块，各有专属测试：

| 模块 | 行数 | 测试 |
|---|---|---|
| `gate.py` | 128 | `test_gate.py` 16 项 |
| `textpath.py` | 186 | `test_textpath.py` 25 项 |

### 重构中修掉的两个真实问题

**1. `parse_rule` 嵌套 6 层** —— 结构测试直接抓到。抽出
`_read_bracketed()` / `_read_bare()` 两个辅助函数后降到 4 层。

**2. `Finding.__repr__` 泄漏原文** —— 写 `test_textpath` 时发现。
`Finding.surface` 存了完整文本（`matched` 属性需要），而默认 `repr` 会把它
打出来。`repr` 输出会进 traceback、日志、bug 报告——那等于把脱敏纪律从后门
绕过去了。加了自定义 `__repr__`，只显示 span 不显示文本。

这个发现说明**结构重构本身能暴露行为缺陷**：把代码搬到新模块、给它写专属
测试，比在原来的大文件里加测试更容易发现问题。

---

## 6. 不做什么（避免过度设计）

| 想法 | 为什么不做 |
|---|---|
| 给每层建 `interfaces/` 抽象基类 | Python 的 Protocol 已够用；为 20 个模块建接口层是净负担 |
| 引入依赖注入容器 | 构造参数已经足够；容器会让 `Filter.from_config_dir` 这类便利方法无处安放 |
| 拆 `chunker` 为 3 个类 | 共享状态传递成本 > 收益，见 §3.2 |
| 按功能垂直切包（`net/` `text/` `image/`） | 现在 20 个模块平铺已清晰，垂直切包会加深导入路径而无实际收益 |

判断标准：**拆分应降低"改一处要理解多少东西"，而不是降低行数。**
`Filter` 该拆，是因为文本判定的 4 个方法和网络/图像无关；
`chunker` 不该拆，是因为它的 600 行都在服务同一个语言规则。
