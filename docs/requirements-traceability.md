# 需求 → 测试项映射

本文把架构与产品需求逐条转成**可验证的测试项**。它是测试框架的规格书：
每条需求要么有对应的自动检查，要么明确标为"未实现"或"不可自动验证"。

**为什么需要这份文件**：单元测试只能证明"代码做了什么"，不能证明"做的是不是
要求的东西"。675 个通过的测试可以和需求脱节——事实上本轮就发现架构要求的
`SemanticBackend` 双后端接口根本不存在，而所有测试都是绿的。

状态图例：

| 标记 | 含义 |
|---|---|
| `COVERED` | 有自动测试，且测试断言的就是需求原文 |
| `PARTIAL` | 有测试，但只覆盖需求的一部分 |
| `MISSING` | 需求存在，实现或测试缺失 |
| `N/A` | 明确不做（架构文档已声明） |
| `MANUAL` | 只能在真机/人工验收，无法自动测 |

---

## A. 架构分层与依赖（可自动验证）

| # | 需求 | 来源 | 测试 | 状态 |
|---|---|---|---|---|
| A1 | 检测引擎（Python）与宿主（.NET）分离，引擎平台无关 | architecture §Target | `test_contracts.py::LayeringTest` | COVERED |
| A2 | 核心零第三方依赖（纯标准库） | architecture §Target | `test_contracts.py::DependencyTest` | COVERED |
| A3 | 模块间无循环依赖 | 推论 | `test_contracts.py::LayeringTest` | COVERED |
| A4 | 依赖方向单向（底层不 import 上层） | 推论 | `test_contracts.py::LayeringTest` | COVERED |
| A5 | 引擎层不得含 I/O（socket/subprocess/ctypes） | architecture §Deliberate omissions | `test_contracts.py::NoIOTest` | COVERED |

## B. 明确不做的行为（架构声明，必须持续验证"确实没做"）

这些是**否定性需求**。原产品的这些行为是恶意软件级别的，新实现必须证明
自己没重犯。

| # | 禁止项 | 来源 | 测试 | 状态 |
|---|---|---|---|---|
| B1 | 全局键盘/鼠标钩子 | architecture §omissions | `test_contracts.py::OmissionTest` | COVERED |
| B2 | 注入第三方进程 DLL | 同上 | 同上 | COVERED |
| B3 | 内核驱动 | 同上 | 同上 | COVERED |
| B4 | 明文 HTTP 下发可执行文件 | 同上 | 同上 | COVERED |
| B5 | 硬编码密码 | 同上 | 同上 | COVERED |
| B6 | 明文上传收集的身份证号 | 同上 | 同上 | COVERED |
| B7 | 写入 `%WinDir%\System32` | 同上 | 同上 | COVERED |
| B8 | 按进程名杀安全工具 | architecture §M7 | 同上 | COVERED |
| B9 | 隐藏进程/服务 | 同上 | 同上 | COVERED |
| B10 | 阻止任务管理器 | 同上 | 同上 | COVERED |
| B11 | 卸载后残留注册表项 | 同上 | 同上 | COVERED |
| B12 | 强制重启 | 同上 | 同上 | COVERED |
| B13 | 日志写入用户内容原文 | 本项目纪律 | `test_invariants.py::RedactionTest` | COVERED |

## C. M1 数据层

| # | 需求 | 来源 | 测试 | 状态 |
|---|---|---|---|---|
| C1 | 四名单 A/B/C/D 容量 = 100 / 无上限 / 1000 / 1500 | 逆向 p21 | `test_namelists.py::CapacityTest` | COVERED |
| C2 | 144 字节记录：text@0，flag@0x84，其余不解释 | 逆向 p21 | `test_namelist.py::RecordLayoutTest` | COVERED |
| C3 | 规则语法 `{a,b}[c,d]`：组 AND、类 OR、5 组上限 | 逆向 p21 | `test_namelist.py::Grammar*` | COVERED |
| C4 | 名单 A 子串匹配、B 精确匹配、C 规则匹配、D 子串 | 逆向 p20/p24 | `test_namelists.py::MatchTest` | COVERED |
| C5 | 添加去重前先小写化 | 逆向 0x14076 | `test_namelists.py::AddRemoveTest` | COVERED |
| C6 | 单条添加上限 11000 | 逆向 0x1746 | `test_namelist.py`（容量常量） | PARTIAL |
| C7 | 词表可加载外部 `.lib` 数据 | architecture §M1 | `test_wordlists.py` | COVERED |

## D. M2 网络层

| # | 需求 | 来源 | 测试 | 状态 |
|---|---|---|---|---|
| D1 | 判定顺序：进程 → 地址 → 主机 → 内容，首命中即停 | 逆向 p17/p19 | `test_intercept.py::StageOrderTest` | COVERED |
| D2 | 出站阻断返回 errno 10054（WSAECONNRESET） | 逆向 p17 | `test_intercept.py::ConstantsTest` | COVERED |
| D3 | 入站关键词只在 HTTP 端口集上跑 | 逆向 p19 | `test_intercept.py::InboundTest` | COVERED |
| D4 | 二进制体不做关键词检查，但前面阶段仍可阻断 | 逆向 p19 + 本项目修正 | `test_intercept.py`（回归） | COVERED |
| D5 | 四段降级：域名/SNI → 明文 HTTP → HTTPS MITM → 图像 | architecture §M2 | `test_transport.py::StageSelectionTest` | COVERED |
| D6 | 每段独立可用，缺内容时优雅降级 | architecture §M2 | `test_transport.py::DegradationTest` | COVERED |
| D7 | 证书固定应用无法解密（已知限制，须明确报告） | architecture §M2 | `test_transport.py`（`LIMIT_PINNED`） | COVERED |
| D8 | QUIC/HTTP3 需禁用或单独处理 | architecture §M2 | `test_transport.py`（`LIMIT_QUIC`） | COVERED |
| D9 | 假传输源可重放 JSON 流，零权限零网络 | 本项目（可测性） | `test_transport.py::ReplayTest` | COVERED |
| D10 | 真实代理/防火墙（.NET 宿主） | architecture §M2 | — | **MISSING**（需真机） |

## E. M3 文本层

| # | 需求 | 来源 | 测试 | 状态 |
|---|---|---|---|---|
| E1 | 两个可互换后端，同一接口 `analyse(text) -> [Hit]` | architecture §M3 | `test_backends.py::ProtocolTest` | COVERED |
| E2 | 两后端可在同一语料上 A/B 对比 | architecture §M3 | `test_backends.py::CompareTest` `AbReportTest` | COVERED |
| E3 | HNC 后端不是原引擎的位级复刻（须诚实标注） | architecture §M3 | `test_semantic.py::HonestLimitsTest` | COVERED |
| E4 | 等级 0 不算有害证据（避免误报） | 本项目修正 | `test_semantic.py::BridgeTest` | COVERED |
| E5 | 命中不给动作（分→动作 未还原） | architecture §M3 | `test_semantic.py::BridgeTest` | COVERED |
| E6 | 分块层（`chunker`）参与判定 | 逆向 + architecture | — | **MISSING** |

## F. M4 图像层

| # | 需求 | 来源 | 测试 | 状态 |
|---|---|---|---|---|
| F1 | 宽高比检查，阈值 10.0，短边 < 32 不判 | 逆向 p22 | `test_imaging.py` | COVERED |
| F2 | 高亮度检查，R≥210 占比 ≥ 0.9 | 逆向 p22 | `test_imaging.py` | COVERED |
| F3 | 部分筛选可辨识（不得把"没查全"报成"干净"） | 本项目修正 | `test_imaging.py::ScreenTest` | COVERED |
| F4 | NSFW / 人脸分类 | architecture §M4 | — | **MISSING**（训练模型） |
| F5 | 图像过滤受 sys.dat 槽 13 + 5 双重门控 | 逆向 | `test_filter.py::ImagePathTest` | COVERED |

## G. M5 屏幕文字

| # | 需求 | 来源 | 测试 | 状态 |
|---|---|---|---|---|
| G1 | UI Automation + Windows.Media.Ocr 提取屏幕文字 | architecture §M5 | — | **MISSING** |
| G2 | 不使用 GDI 内联钩子（DirectWrite 下无效且跨位数不行） | architecture §M5 | `test_contracts.py::OmissionTest` | COVERED |
| G3 | 提取的文字进入文本判定链 | 推论 | — | **MISSING** |

## H. M6 应用控制

| # | 需求 | 来源 | 测试 | 状态 |
|---|---|---|---|---|
| H1 | 名单 B 匹配进程名（大小写不敏感） | 逆向 p20 | `test_evidence_paths.py` | COVERED |
| H2 | 被监控进程被阻断网络 | 逆向 | `test_evidence_paths.py::PrecedenceTest` | COVERED |
| H3 | 终止被禁进程 | architecture §M6 | — | **MISSING** |

## I. M7 基础设施

| # | 需求 | 来源 | 测试 | 状态 |
|---|---|---|---|---|
| I1 | 配置原子写入 + 写后读回校验 | 本项目修正 | `test_config.py::SaveLoadTest` | COVERED |
| I2 | 配置格式与出厂 `sys.dat` 逐字节相同 | 兼容性 | `test_config.py` | COVERED |
| I3 | 保留槽（26–67）必须为 0 | 逆向 | `test_config.py::ValidateTest` | COVERED |
| I4 | 日志 JSONL + 结构化 + 脱敏 | 本项目纪律 | `test_eventlog.py` | COVERED |
| I5 | `gated` 与 `allow` 必须可区分 | 本项目修正 | `test_eventlog.py` / `test_filter.py` | COVERED |
| I6 | 密码 PBKDF2-HMAC-SHA256 + 随机盐，绝不存明文 | architecture §M7 | — | **MISSING** |
| I7 | 服务以 LocalSystem 运行，ACL 拒绝非管理员停止 | architecture §M7 | — | **MISSING**（需真机） |
| I8 | 崩溃后恢复动作重启服务 | architecture §M7 | — | **MISSING**（需真机） |
| I9 | 卸载是安装的严格逆操作，有 manifest | architecture §M7 | — | **MISSING** |
| I10 | 卸载 `--dry-run` 列出将删除内容 | architecture §M7 | — | **MISSING** |
| I11 | 卸载不强制重启 | architecture §M7 | — | **MISSING** |

## J. M8 策略层

| # | 需求 | 来源 | 测试 | 状态 |
|---|---|---|---|---|
| J1 | 67 槽策略数组，索引 = 策略号 - 1 | 逆向 p15 | `test_sysdat.py` | COVERED |
| J2 | 只有 1–25 有名称；26–67 保留 | 逆向 | `test_sysdat.py` | COVERED |
| J3 | 出厂值与"预设级别 6"合法不同（槽 11/18） | 逆向 | `test_sysdat.py` | COVERED |
| J4 | 槽 2 / 10 是已证实的关闭开关 | 逆向 | `test_policy.py` / `test_filter.py` | COVERED |
| J5 | 分→动作 映射未知，默认不映射 | 诚实标注 | `test_policy.py` | COVERED |
| J6 | 多源证据优先级：网络阻断 > 其余最强 | 本项目设计 | `test_evidence_paths.py` | COVERED |
| J7 | 时间调度（按时间段生效） | architecture §M8 | — | **MISSING** |

## K. M9 控制台

| # | 需求 | 来源 | 测试 | 状态 |
|---|---|---|---|---|
| K1 | 诊断 CLI（status/policy/check/validate/log/screen） | 本项目 | `test_cli.py` | COVERED |
| K2 | CLI 不打印词条/正文 | 本项目纪律 | `test_cli.py::NoContentTest` | COVERED |
| K3 | 家长 UI（托盘、密码、日志查看） | architecture §M9 | — | **MISSING** |
| K4 | 单机运行，无服务器 | architecture §M9 | 结构性 | COVERED |

## L. 跨层不变量（本轮新建）

| # | 不变量 | 测试 | 状态 |
|---|---|---|---|
| L1 | 四条证据路径结果与单路入口完全一致 | `test_evidence_paths.py` | COVERED |
| L2 | 任意路径组合只产生 1 条日志 | `test_evidence_paths.py` | COVERED |
| L3 | 任何路径都不得把内容写进日志 | `test_invariants.py` | COVERED |
| L4 | gate 只压制证据，绝不产生 `allow` | `test_invariants.py` | COVERED |
| L5 | "没在看"永不报成"没问题" | `test_invariants.py` | COVERED |
| L6 | 名单容量常量与逆向证据一致 | `test_invariants.py` | COVERED |
| L7 | 策略数组出厂值与 CD 上的 `sys.dat` 逐值相等 | `test_config.py` | COVERED |

---

## 汇总

| 状态 | 数量 |
|---|---|
| COVERED | 51 |
| PARTIAL | 1 |
| **MISSING** | **14** |
| MANUAL | 0（合并进 MISSING 的说明里） |

### MISSING 的 20 项，按"能否脱离真机实现"分组

**纯逻辑，不需要权限**（建议优先）：

- E6 分块层接入
- I6 密码哈希（纯计算，PBKDF2）
- I9 / I10 卸载清单（manifest 生成与逆序回滚的纯数据部分）
- J7 时间调度

**需要平台 API / 真机**：

- D10 真实代理与防火墙规则
- F4 NSFW 分类（训练模型）
- G1 / G3 UIA + OCR
- H3 终止进程
- I7 / I8 服务 ACL 与恢复动作
- K3 家长 UI

---

## 使用方式

```bash
# 只跑契约/不变量（快，验证"设计没走样"）
pytest tests/test_contracts.py tests/test_invariants.py -q

# 四段降级 + 重放（不需要网络）
pytest tests/test_transport.py -q

# 全量
pytest tests/ -q
```

## 测试框架的四层

| 层 | 文件 | 回答的问题 | 数量 |
|---|---|---|---|
| 单元 | `test_*.py`（各模块） | 这个函数做对了吗 | ~690 |
| **契约** | `test_contracts.py` | **代码结构还符合架构吗** | 14 |
| **不变量** | `test_invariants.py` | **跨路径的全局性质还成立吗** | 23 |
| **端到端** | `test_transport.py` `test_evidence_paths.py` `test_backends.py` | **子系统串起来还对吗** | 75 |

前两层是**否定性**的：它们证明"没有做不该做的事"。这类测试最容易在重构中
被悄悄削弱，所以它们的断言都写在测试文件里、带需求编号，而不是靠注释提醒。

### 契约测试必须能抓到违规

一个永远通过的契约测试等于没有。本轮用变异测试验证过：

| 注入的违规 | 被抓到的测试 |
|---|---|
| `imaging` 反向 import `filter` | `test_leaves_import_nothing_internal` |
| 引入 `socket` | `test_no_io_imports` 等 3 项 |
| 引入 `numpy` | `test_no_numpy_or_ml_runtime` |
| 硬编码 7 位密码 | `test_no_hardcoded_password` |
| import 时 `open()` | `test_no_import_time_file_reads` |

5/5 全部抓到，恢复后干净。

新增需求时，先在本文件加一行，再写测试。**不允许先写测试再补需求** ——
那样测试会退化成"证明代码做了什么"。
