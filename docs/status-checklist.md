# 状态核对清单（实现 / 待做 / 待测）

> **用途**：逐项核对本仓库「已经写了什么、还没写什么、写了但没验什么」。
> 每完成一项就把状态改掉并提交，不让清单和代码脱节。
>
> **判定口径**
> - `DONE` = 代码在 `src/` 且有 `SRC` 类测试覆盖
> - `PARTIAL` = 代码在 `src/` 但覆盖不全，或只做了其中一层
> - `TODO` = 只有逆向结论（`analysis/`），`src/` 无代码
> - `N/A` = 明确决定不做
>
> 测试口径（`python -m pytest tests/ -q`，当前 416 passed / 9 skipped / 137 subtests）：
> - `SRC` 测试 = 断言 `src/green_dam/` 的行为
> - `LEGACY` 测试 = 断言原厂二进制的结构（为逆向取证，**不覆盖新实现**）
>
> 最后核对：M3 HNC 接线完成时（653 passed）。

---

## 0. 一页总览

| 模块 | 职责 | 状态 | src 文件 | SRC 测试 |
|---|---|---|---|---|
| M1 数据 | 载入 JSON、建索引、类别模型 | **PARTIAL** | `wordlists.py` `namelist.py` `namelists.py` | `test_wordlists` `test_namelist` `test_namelists` |
| M2 网络 | 四段拦截 | **PARTIAL** | `intercept.py`（判定链） | `test_intercept` |
| M3 文本 | 分词、语境判定、双后端 | **PARTIAL** | `chunker.py` `hnc.py` `semantic.py` 等 | `test_chunker` 等 7 个 |
| M4 图像 | 不良/人脸分类 | **PARTIAL** | `imaging.py`（2 项实测检查） | `test_imaging` |
| M5 屏幕 | 屏幕文字提取 | **TODO** | — | — |
| M6 应用控制 | 禁止指定程序联网 | **TODO** | — | — |
| M7 基础设施 | 服务、配置、日志、IPC | **PARTIAL** | `eventlog.py` `config.py` | `test_eventlog` `test_config` |
| M8 策略 | 汇总成 放行/阻断/告警 | **DONE** | `policy.py` `sysdat.py` | `test_policy` `test_sysdat` |
| — 跨层汇总 | 网络 + 文本证据合成 | **DONE** | `policy.py` (`assess`) | `test_policy::AssessTest` |
| — 门面 | 配置+名单+判定+日志串联 | **DONE** | `filter.py` | `test_filter`（端到端） |
| M9 控制台 | 父界面、日志、调度 | **TODO** | `cli.py`（仅诊断 CLI） | `test_cli` |

**没有 CLI、没有 `__main__.py`、没有打包配置** —— 目前是一个纯库。

---

## 1. M1 数据层 —— PARTIAL

### 已实现

| 项 | 位置 | 状态 |
|---|---|---|
| 统一词表查询（legacy trie + 外部词库） | `src/green_dam/wordlists.py` | DONE |
| 144B 记录编解码（text@0, flag@0x84, 其余 opaque） | `src/green_dam/namelist.py` | DONE |
| 规则语法 `{a,b}[c,d]`（组=AND、类=OR、5 组上限） | `src/green_dam/namelist.py` | DONE |
| 四名单容器（容量/匹配/增删/序列化） | `src/green_dam/namelists.py` | DONE |
| 命中保留来源链（`WordHit.sources`） | 同上 | DONE |
| 词库 JSON 导入（`.dat` 可逆混淆解码） | `tools/legacy_wordlist_codec.py` | DONE |
| 语料库导入与回归 | `tests/test_corpus_regression.py` | DONE |

### 待做

| 项 | 说明 | 优先级 |
|---|---|---|
| 四名单（A/B/C/D）运行时模型 | 逆向已闭合（`analysis/probe/p20`、`p24`），`src/` 无对应结构 | 高 |
| 144B 记录编解码 | 格式已闭合（`p21`），无 `src` 实现 | 高 |
| 规则语法引擎 `{组}[类]` | 语法已闭合（`p21`：`{}`=AND、`[]`=OR、`,`=分隔），无 `src` 实现 | 高 |
| 名单容量约束（1500/100/1000/1500，单条上限 11000） | 已闭合，未编码 | 中 |

### 待测

| 项 | 说明 |
|---|---|
| ~~144B 记录往返~~ | **DONE**：`test_namelist.py` 29 项，含 opaque 字节往返 |
| ~~规则语法匹配正确性~~ | **DONE**：全部样本为虚构单字（甲乙丙丁），未使用任何原厂词条 |
| ~~名单容器增删/容量~~ | **DONE**：`test_namelists.py` 24 项 |

---

## 2. M2 网络层 —— PARTIAL

判定链已实现（`src/green_dam/intercept.py`，29 项测试）。传输层未做。

### 已实现

| 项 | 位置 | 状态 |
|---|---|---|
| 出站判定链（进程→地址→主机→内容） | `intercept.py` | DONE |
| 入站判定链（进程→地址→主机→关键词） | `intercept.py` | DONE |
| 阶段顺序与 `Decision.stage` | `intercept.py` | DONE（顺序被测试钉住） |
| 拦截 errno = 10054 | `intercept.py` | DONE |
| HTTP 端口集 | `intercept.py` | DONE |
| 二进制嗅探（前 10 字节） | `intercept.py` | DONE |
| 出站 URL 提取 | `intercept.py` | DONE |
| 与文本证据汇总（`assess`） | `policy.py` | DONE（保留两侧出处） |

### 待做

| 项 | 说明 | 优先级 |
|---|---|---|
| 传输层第 1 段（域名/SNI + 防火墙） | 无需根 CA，最容易先做 | 高 |
| 传输层第 2 段（明文 HTTP 透明代理） | 需要代理实现 | 中 |
| 传输层第 3 段（HTTPS MITM） | 需要根 CA | 低 |
| 入站"替换响应体"动作 | 判定已给 block，替换属传输层 | 中 |
| 连接跟踪表 | 原厂 32 槽×16B；新实现用字典即可 | 低 |

### 待测

| 项 | 说明 |
|---|---|
| 端到端 | 传输层未做，无从测 |
| 真实流量 | 同上 |

---

## 3. M3 文本层 —— PARTIAL

### 已实现

| 项 | 位置 | 状态 |
|---|---|---|
| HNC 句类判定复刻 | `src/green_dam/hnc.py` | DONE（Unicorn 974 输入零失配） |
| 语义分块 | `src/green_dam/chunker.py`（1,192 行） | DONE |
| 块标记 / l 类概念 / 相似度 | `chunk_tags.py` `l_concepts.py` `similarity.py` | DONE |
| 语义判定 | `src/green_dam/semantic.py` | DONE |

### 待做

| 项 | 说明 | 优先级 |
|---|---|---|
| 第二后端（规则式） | 架构要求「两个可互换后端」，目前只有 HNC 后端 | 中 |
| ~~与 M8 的接线~~ | **DONE**：`semantic.analyse_findings` → `Filter.check_text` |

### 待测

| 项 | 说明 |
|---|---|
| ~~HNC 接线~~ | **DONE**：9 项测试，含等级 0 误命中回归 |
| 分块层端到端 | 目前测的是分块器本身，未测「文本 → 分块 → 判定 → Verdict」全链 |
| 双后端等价性 | 第二后端未存在，无从对比 |

### 明确的上限（不是 TODO）

`SENTEN.LIB` / `TYPE.LIB` / `XU.LIB` / `rule.lib` 从未随产品发布，句类**代码**可还原但**名称**不可。已在 `README.md` 与 `docs/replication-status.md` 说明，不作为待办。

---

## 4. M4 图像层 —— PARTIAL

两个能逐字节读懂的函数已复刻（`src/green_dam/imaging.py`，27 项测试）。
Haar 级联未做（训练模型，非逻辑），已在 `docs/image-layer.md` §5 说明。

### 已实现

| 项 | 位置 | 状态 |
|---|---|---|
| 宽高比检查（`0x10008e20`，阈值 10.0） | `imaging.py` | DONE |
| 高亮度检查（`0x10008e70`，阈值 0.9） | `imaging.py` | DONE |
| BGR 平面提取 | `imaging.py` | DONE |
| 部分筛选可辨识（`checks_run`/`fully_screened`） | `imaging.py` | DONE |
| 接入 `assess` + `Filter.check_image` | `policy.py` `filter.py` | DONE |

### 待做

| 项 | 说明 | 优先级 |
|---|---|---|
| Haar 人脸检测 | 需 OpenCV 或等价级联求值器 + 模型 | 低（非逻辑） |
| 轮廓/形态学判定路径 | 已 import 但未找到判定调用 | 低 |
| 抓屏（`CAPSCREEN`） | 平台 API，属 M5 | 中 |
| 自动加黑名单（写名单 D） | 依赖 M1 运行时容器接线 | 中 |

### 待测

| 项 | 说明 |
|---|---|
| ~~检测器单元测试~~ | **DONE**：全部用算术生成的合成图，无素材依赖 |
| 灵敏度三档差异 | **未做**：实测两个函数各只有一个阈值，原厂"三档"未找到对应代码 |
| 真实图片端到端 | 需解码器，属输入层 |

---

## 5. M5 屏幕文字 —— TODO

逆向已闭合（`analysis/04-ENTRYPOINTS.md`，IPGate 完整还原），`src/` 无代码。

| 待做项 | 逆向依据 | 说明 |
|---|---|---|
| 取词机制 | `04-ENTRYPOINTS.md` | 原厂用 GDI 文本 API hook；新实现应改无障碍/OCR（架构已定不 hook） |
| 抓屏与保留策略 | p15 + `sysdat.py` | 槽 17 允许、槽 18 间隔、槽 19 保留天数（已可读） |

---

## 6. M6 应用控制 —— TODO

逆向已闭合（`analysis/probe/p20`，C-019 名单 B；`kwselectinfopp.dll` 8 条记录），`src/` 无代码。

| 待做项 | 逆向依据 | 说明 |
|---|---|---|
| 进程名单匹配 | p20 | Toolhelp 遍历 + 名称小写化 + 匹配 |
| 静态初始名单 | F-003 | `kwselectinfopp.dll` 84B × 8 记录格式已闭合 |
| 进程终止动作 | C-007 | 原厂 22 条终止名单（**不迁移名单内容**，只迁移机制） |

---

## 7. M7 基础设施 —— TODO

| 待做项 | 说明 | 优先级 |
|---|---|---|
| 配置持久化 | 读写 `sys.dat` 形状（`sysdat.encode()` 已备） | 高 |
| 日志（含变更日志） | 架构要求 | 中 |
| 服务宿主 | 架构要求 | 低（先做库） |
| IPC | 架构要求 | 低 |
| 加固/防卸载 | **N/A** —— 架构明确不做 | N/A |

---

## 8. M8 策略层 —— DONE

| 项 | 位置 | 状态 |
|---|---|---|
| 证据 → 建议动作 | `policy.py` | DONE |
| 67 槽策略数组解码 | `sysdat.py` | DONE |
| 槽位名（1–25）与 `index=slot_no-1` | `sysdat.py` | DONE（`CLOSED` 证据） |
| 门控（槽 2 / 槽 10 关闭过滤） | `policy.py` | DONE |
| 预设值 vs 出厂值区分 | `sysdat.py` `PRESET_LEVEL_6` | DONE（差异被测试钉住） |

### 待做

| 项 | 说明 | 优先级 |
|---|---|---|
| 原厂分 → 动作映射 | 语义未知（`HASrv.dll` 未查），**保持 `attested=False`** | 阻塞于逆向 |
| 时间控制（槽 24 / 槽 21） | 槽位可读，调度逻辑未做 | 中 |
| 白名单优先（槽 14 / 槽 15） | 槽位可读，优先级逻辑未做 | 中 |

### 待测

| 项 | 说明 |
|---|---|
| 时间控制窗口 | 未实现，无从测 |
| 与 M4 图像的汇总 | 图像结果尚未接入 `assess` |

---

## 9. M9 控制台 —— TODO

架构要求：单机、无服务器、托盘 + 日志 + 调度。`src/` 无代码，无 CLI。

---

## 10. 横切项

| 项 | 状态 | 说明 |
|---|---|---|
| 打包（`pyproject.toml`） | **DONE** | 零运行时依赖，`>=3.10` |
| CLI 入口（`green-dam`） | **DONE** | 6 个子命令，全支持 `--json` |
| 端到端测试 | **DONE** | `test_filter.py` 21 项，配置→判定→日志全链 |
| 真实流量接入 | TODO | `check_request` 就绪，无传输层 |
| 不打印词条纪律 | DONE | 全部测试只断言结构与计数 |
| 文档与代码同步 | DONE | 关键差异均有测试钉住（槽 17、预设值差异） |

---

## 11. 下一步（按依赖排序）

1. ~~M1 规则语法引擎 + 144B 记录~~ —— **DONE**（提交 `f4b780a`）
2. ~~M1 四名单模型~~ —— **DONE**
3. **M2 出站/入站判定链** —— 契约最完整，可用假数据先跑通 ← **下一步**
4. **M8 与 M3/M4 汇总接线** —— 把已有检测结果变成 `Finding`
5. **M4 剩余**（Haar / 抓屏 / 自动加黑名单）
6. ~~M7 配置持久化~~ —— **DONE**
7. ~~打包 + CLI~~ —— **DONE**

---

## 12. 已知不做（避免反复讨论）

| 项 | 理由 |
|---|---|
| HNC 句法分析（`GetSentenceALC` 线） | 原厂零调用，凑齐 `.lib` 也无用（`docs/engine-call-graph.md`） |
| 原厂打分表当通用词库 | 是某份文档的原文片段，非规范化词表（`docs/replication-status.md`） |
| `.urf` 元数据区 | 熵 7.98 加密，GIF 已明文可提取（`analysis/probe/p23`） |
| GDI/API hook 实现 | 架构明确改代理 + 无障碍/OCR（`docs/architecture.md`） |
| 防卸载/加固 | 架构明确不做 |
