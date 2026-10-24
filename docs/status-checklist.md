# 状态核对清单

> **第一次读这个仓库？先看 `docs/HANDOFF.md`。** 本文是逐模块明细，
> HANDOFF 是全景与陷阱说明。（实现 / 待做 / 待测）

> **用途**：逐项核对本仓库「已经写了什么、还没写什么、写了但没验什么」。
> 每完成一项就把状态改掉并提交，不让清单和代码脱节。
>
> **判定口径**
> - `DONE` = 代码在 `src/` 且有 `SRC` 类测试覆盖
> - `PARTIAL` = 代码在 `src/` 但覆盖不全，或只做了其中一层
> - `TODO` = 只有逆向结论（`analysis/`），`src/` 无代码
> - `N/A` = 明确决定不做
>
> 测试口径（`python -m pytest -q`（testpaths 已含 `tests` 与 `host/tests`），当前 **1690 passed / 15 skipped / 137 subtests**，共收集 1705 项）：
> - `SRC` 测试 = 断言 `src/green_dam/` 的行为
> - `HOST` 测试 = 断言 `host/` 的行为（含 B7/B8 在宿主层的否定性约束）
> - `LEGACY` 测试 = 断言原厂二进制的结构（为逆向取证，**不覆盖新实现**）
>
> 最后核对：**1690 passed / 15 skipped**。该数字由 `tests/test_docs.py::TestCountClaimTest`
> 自动校对（文档里的总数必须等于两个测试目录收集到的测试数），改代码后忘记改这里会红。
> **本次核对已清理全部过时条目**（见文末「修订记录」）。

---

## 0. 一页总览

| 模块 | 职责 | 状态 | src 文件 | SRC 测试 |
|---|---|---|---|---|
| M1 数据 | 载入 JSON、建索引、类别模型 | **DONE** | `wordlists.py` `namelist.py` `namelists.py` `legacy_words.py` `simpath.py` | 5 个测试文件 |
| M2 网络 | 四段拦截 | **PARTIAL** | `intercept.py` `transport.py` `replay.py` | `test_intercept` `test_transport` |
| M3 文本 | 分词、语境判定、双后端 | **DONE** | `chunker.py` `semantic.py` `backends.py` `chunkpath.py` `textpath.py` | 9 个测试文件 |
| M4 图像 | 不良/人脸分类 | **PARTIAL** | `imaging*.py`（4 个）+ `imagepath.py`（后端接口）；`host/imaging/`（ONNX 后端 + 评分服务） | 4 个测试文件 + `host/tests/test_imaging.py` `test_scoring.py` |
| M5 屏幕 | 屏幕文字提取 | **TODO** | — | — |
| M6 应用控制 | 禁止指定程序联网 | **PARTIAL** | `namelists.py`（名单 B 匹配）；`host/processes.py` `host/protection.py` | `test_evidence_paths` + `host/tests/test_processes.py` |
| M7 基础设施 | 服务、配置、日志、IPC | **PARTIAL** | `eventlog.py` `config.py` `secret.py` `journal.py` | 4 个测试文件 |
| M8 策略 | 汇总成 放行/阻断/告警 | **DONE** | `policy.py` `sysdat.py` `schedule.py` | `test_policy` `test_sysdat` `test_schedule` |
| — 跨层汇总 | 网络 + 文本证据合成 | **DONE** | `policy.py` (`assess`) | `test_policy::AssessTest` |
| — 门面 | 配置+名单+判定+日志串联 | **DONE** | `filter.py` `gate.py` | `test_filter`（端到端） |
| — 四路一致 | 网络/文本/图像/进程统一 | **DONE** | `filter.py` | `test_evidence_paths`（19 项） |
| M9 控制台 | 父界面、日志、调度 | **PARTIAL（只读）** | `host/ui/`（数据层 + 5 个视图 + 三路回退）；**无写操作、无托盘、无改密码页** | `host/tests/test_ui.py` |
| — 装配层 | 把代理/评分/控制台拉起来 | **TODO** | **无 `host/supervisor.py`、无 `host/__main__.py`** | — |
| — 服务宿主 | Windows 服务 + ACL + 恢复动作 | **TODO** | **无 `host/service.py`** | — |
| — 安装包 | exe 入口 + 安装程序 + 卸载执行 | **TODO** | **无** | — |
| — 宿主骨架 | 能力探测、路径布局、引擎装配 | **DONE** | `host/capabilities.py` `host/paths.py` `host/engine.py` | `host/tests/test_capabilities.py` |
| — 宿主契约 | 宿主层的 B7/B8 等否定性约束 | **DONE** | — | `host/tests/test_contracts.py` |
| — 评分服务 | 扩展 POST `/score` 的对接端 | **DONE** | `host/imaging/service.py` `host/imaging/__main__.py` | `host/tests/test_scoring.py`（28 项） |

**有 CLI（`green-dam`）、有 `python -m green_dam`、有打包（`pyproject.toml`，
零运行时依赖）。** CLI 是**诊断工具**，不是架构里的 M9 产品界面。

### 测试分层（1705 项）

| 层 | 文件 | 项数 | 作用 |
|---|---|---|---|
| 单元 | 各模块 | ~700 | 单个函数/类的行为 |
| **文档** | `test_docs.py` | 14 | 文档里的数字还准吗 |
| **结构** | `test_structure.py` | 15 | 模块/类/函数规模、嵌套深度 |
| **契约** | `test_contracts.py` | 14 | 分层、零依赖、无 IO、不做清单 |
| **不变量** | `test_invariants.py` | 23 | 门控语义、单条日志、脱敏 |
| 端到端 | `transport` `evidence_paths` `backends` | 75 | 子系统串起来 |
| 逆向取证 | `test_hnc_engine` 等（LEGACY） | 若干 | 断言原厂二进制结构 |

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
| ~~词库加载器接真实 `.dat`~~ | **DONE**：`legacy_words.py`，实测 5 文件 9,143 条 | — |
| 类别模型（`WordHit.category`）填充 | 字段存在，未从语料映射 | 低 |

**已完成（曾列为待做）**：

| 项 | 位置 | 测试 |
|---|---|---|
| 四名单（A/B/C/D）运行时模型 | `namelists.py` | `test_namelists.py` 24 项 |
| 144B 记录编解码 | `namelist.py` | `test_namelist.py` 29 项 |
| 规则语法引擎 `{组}[类]` | `namelist.py` | 同上 |
| 名单容量约束（A=100/C=1000/D=1500，单条上限 11000） | `namelists.py` | 同上 |
| 句子相似度 | `simpath.py` | `test_simpath.py` 35 项 |

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
| 真实代理实现（第 1–3 段） | `transport.py` 只做**规划**（`Plan`），不做 I/O；执行需平台 API | 高 |
| 入站"替换响应体"动作 | 判定已给 block，替换属传输层 | 中 |
| 连接跟踪表 | 原厂 32 槽×16B；新实现用字典即可 | 低 |

**已完成（曾列为待做）**：

| 项 | 位置 | 说明 |
|---|---|---|
| 四段降级骨架（`Stage`/`Flow`/`Plan`） | `transport.py` | 30 项测试，零网络零权限 |
| 流文件重放 | `replay.py` | `test_transport.py` |

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
| 真正的分词后端 | 架构文档写的是 `JiebaBackend`；本项目用**纯标准库正向最大匹配**（零依赖契约），已按实际命名 `SegmentationBackend` | — |
| 语义相似度参与判定 | `simpath.py` 已实现，未接进 `Filter`（因原厂 分→动作 映射未知） | 中 |

**已完成（曾列为待做）**：

| 项 | 位置 | 测试 |
|---|---|---|
| 第二后端 + A/B 对比 | `backends.py` | `test_backends.py` 43 项 |
| 分块层接入 | `chunkpath.py` | `test_chunkpath.py` 27 项 |
| 句子相似度 | `simpath.py` | `test_simpath.py` 35 项 |

### 待测

| 项 | 说明 |
|---|---|
| ~~HNC 接线~~ | **DONE**：9 项测试，含等级 0 误命中回归 |
| ~~分块层端到端~~ | **DONE**：`test_chunkpath.py` 含 Filter 集成 |
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
| 宽高比检查（`0x10008e20`，浮点 10.0） | `imaging.py` | DONE |
| 高亮度检查（`0x10008e70`，阈值 0.9） | `imaging.py` | DONE |
| BGR 平面提取 | `imaging.py` | DONE |
| 部分筛选可辨识（`checks_run`/`fully_screened`） | `imaging.py` | DONE |
| 接入 `assess` + `Filter.check_image` | `policy.py` `filter.py` | DONE |
| **`CHECKEX13x`**：整数宽高比 8、灰度 0.11/0.30/0.59、中心 8×8 标准差 1.4、8 档纹理分级、`9*g1+3*g2+g3` 打包 | `imaging_checkex.py` | DONE（41 项） |
| **`CHECKEX8x`**：门槛 160、ratio 0.08/0.15/0.6、特殊门控返回 10 | `imaging_skin.py` | DONE（24 项） |
| **`CHECKEX8x` 肤色**：`R>G>B` + 饱和度 >0.09 + 10 个色相桶，HSV | `imaging_skin_hsv.py` | DONE（26 项） |

### 待做

| 项 | 说明 | 优先级 |
|---|---|---|
| Haar 人脸检测 | 需 OpenCV 或等价级联求值器 + 模型 | 低（非逻辑） |
| ~~NSFW 分类器接口~~ | **DONE**：`imagepath.py`，宿主注入，引擎零依赖 | — |
| 真实 ONNX 模型 | 需 `onnxruntime` + 模型文件，**属宿主** | 高（P4） |
| 矩形分析（`0x10006ee6`） | 0.54/252 语义未明，**代码里抛 `NotReversed`** | 低 |
| 其余 15 个 `CHECKEX*` 语义 | 已还原 `CHECKEX13x`/`CHECKEX8x`，剩余多为阈值变体 | 低（对目标无益） |
| 抓屏（`CAPSCREEN`） | 平台 API，属 M5 | 中 |
| 自动加黑名单（写名单 D） | 依赖 M1 运行时容器接线 | 中 |

### 待测

| 项 | 说明 |
|---|---|
| ~~检测器单元测试~~ | **DONE**：全部用算术生成的合成图，无素材依赖 |
| 灵敏度三档差异 | **未做**：实测两个函数各只有一个阈值；`0x10004a60` 三模式未能定位 |
| 真实图片端到端 | 需解码器，属输入层 |

---

## 5. M5 屏幕文字 —— TODO

逆向已闭合（`analysis/04-ENTRYPOINTS.md`，IPGate 完整还原），`src/` 无代码。

| 待做项 | 逆向依据 | 说明 |
|---|---|---|
| 取词机制 | `04-ENTRYPOINTS.md` | 原厂用 GDI 文本 API hook；新实现应改无障碍/OCR（架构已定不 hook） |
| 抓屏与保留策略 | p15 + `sysdat.py` | 槽 17 允许、槽 18 间隔、槽 19 保留天数（已可读） |

---

## 6. M6 应用控制 —— PARTIAL

逆向已闭合（`analysis/probe/p20`，C-019 名单 B；`kwselectinfopp.dll` 8 条记录）。
引擎侧（名单 B 匹配）在 `namelists.py`；宿主侧在 `host/processes.py`。

| 项 | 位置 | 状态 | 测试 |
|---|---|---|---|
| 进程名单匹配 | `filter.py` `check_process` + `namelists.py` | DONE | `test_evidence_paths` |
| 进程枚举 | `host/processes.py` `list_processes`（psutil） | DONE | `host/tests/test_processes.py` |
| 终止动作（默认 dry-run） | `host/processes.py` `sweep` | DONE | 同上 |
| **终止保护（B8）** | `host/protection.py` | DONE | 同上（critical / security / self 三类） |
| 静态初始名单 | F-003 | **TODO**：`kwselectinfopp.dll` 84B × 8 记录格式已闭合，未迁移 | — |

### 设计约束（不是待做项）

- **默认 dry-run**：`sweep(apply=False)`。会杀进程的路径全在 `apply=True` 后面。
- **保护名单先于任何动作**：Windows 关键进程、安全软件、本产品自身进程树，
  即使家长把它们加进名单 B 也不会被终止，且会以 `Refusal` 报告——
  “家长要求阻断 csrss” 是配置错误，不该静默失效。
- **原厂 22 条终止名单内容不迁移**，只迁移机制（那 22 条就是安全软件）。

---

## 7. M7 基础设施 —— PARTIAL

### 已实现

| 项 | 位置 | 状态 | 测试 |
|---|---|---|---|
| 配置持久化（原子写 + 往返自检 + 旁挂元信息） | `config.py` | DONE | 26 项 |
| 事件日志（JSONL，每条 flush，内容摘要） | `eventlog.py` | DONE | 22 项 |
| 日志接线（判定 → 单条记录） | `filter.py` | DONE | `test_invariants` 钉住 |
| 家长密码（PBKDF2 600k + 随机盐 + 常量时序） | `secret.py` | DONE | 33 项 |
| 安装日志与卸载计划（逆序 + 可逆性契约） | `journal.py` | DONE | 37 项 |

### 待做

| 待做项 | 说明 | 优先级 |
|---|---|---|
| 服务宿主 | 架构要求 `LocalSystem` + ACL + 恢复动作 | 低（需真机） |
| IPC | 架构要求 | 低 |
| 执行卸载动作 | `journal.plan_uninstall` 只出计划，执行需平台 API | 中（I12） |
| 日志轮转 / 保留期 | 槽 4 可读，策略未定 | 中 |
| 密码失败锁定 / 强度策略 | 未定；原厂无此机制 | 低 |
| 加固/防卸载（rootkit 式） | **N/A** —— 架构明确不做 | N/A |

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
| ~~时间控制（J7）~~ | **DONE**：`schedule.py` 7×24 网格 + 槽 24 接线 + 5 分钟预警 | — |
| 白名单优先（槽 14 / 槽 15） | 槽位可读，优先级逻辑未做 | 中 |

### 待测

| 项 | 说明 |
|---|---|
| 时间控制窗口 | 未实现，无从测 |
| ~~与 M4 图像的汇总~~ | **DONE**：`image` + `image_action` 已接入 `assess` |

---

## 9. M9 控制台 —— TODO

架构要求：单机、无服务器、托盘 + 日志 + 调度。

**技术选型已定**（见 `docs/dependency-audit.md`）：

| 组件 | 方案 |
|---|---|
| 窗口 | `pywebview`（WebView2）+ 本地 HTML/CSS/JS |
| 降级 | `msedge --app=` → 默认浏览器 |
| 托盘 | `pystray` |
| 后端 | 与引擎同进程，或本地 HTTP（127.0.0.1 随机端口） |

`src/` 无产品 UI；`cli*.py` 是**诊断工具**（`green-dam`），不是 M9。
宿主层代码将放在 `host/`（不在 `src/`，因为 `src/` 受零 IO 契约约束）。

---

## 10. 横切项

| 项 | 状态 | 说明 |
|---|---|---|
| 打包（`pyproject.toml`） | **DONE** | 零运行时依赖，`>=3.10` |
| 结构约束（模块/类/函数规模、嵌套深度） | **DONE** | `test_structure.py` 15 项 |
| CLI 入口（`green-dam`） | **DONE** | 12 个子命令，全支持 `--json`；按权限等级分 3 个模块 |
| 端到端测试 | **DONE** | `test_filter.py` 21 项，配置→判定→日志全链 |
| 真实流量接入 | TODO | `check_request` 就绪，无传输层 |
| 不打印词条纪律 | DONE | 全部测试只断言结构与计数 |
| 文档与代码同步 | DONE | 关键差异均有测试钉住（槽 17、预设值差异） |

---

## 11. 下一步（按依赖排序）

> **详细顺序、依赖图与验收标准见 `docs/roadmap.md`。** 本节只记状态，不重写步骤。

### 11.1 当前阶段的一句话

**判定层完整，装配层空缺，交付层为零。**

| 层 | 状态 |
|---|---|
| 判定（`src/green_dam/`） | **DONE**，无待做项 |
| 宿主零件（`host/`） | 每个都能用，**但没有装配者** |
| 交付（安装包 / exe / 服务） | **零** |

### 11.2 装配层缺什么（已实测）

| 缺的东西 | 验证命令 | 结果 |
|---|---|---|
| 生产代码启动代理 | `grep -rn "host.proxy.server import" --include=*.py host/ \| grep -v tests` | 空 |
| `host/supervisor.py` | `ls host/supervisor.py` | 不存在 |
| `host/__main__.py` | `ls host/__main__.py` | 不存在 |
| `host/service.py`（H3） | `ls host/service.py` | 不存在 |
| 浏览器代理策略 | `grep -rn "ProxyMode" host/policy/` | 空 |
| 控制台写操作 | `grep -c read_only host/ui/server.py` | 1（只读） |
| 托盘 / 改密码页 / 向导 | `ls host/*tray*`、`grep password host/ui/web/` | 全无 |

### 11.3 已完成的宿主层

| 项 | 位置 |
|---|---|
| H0 骨架 + 路径 | `host/__init__.py` `host/paths.py` |
| H1 进程控制 + 保护 | `host/processes.py` `host/protection.py` |
| H2a 防火墙 | `host/firewall.py` |
| H2b 明文代理 + 入站检查 | `host/proxy/` |
| H4a 浏览器扩展 | `host/extension/` |
| H5 图像模型（随包分发） | `host/imaging/` |
| H5b 评分服务 | `host/imaging/service.py` |
| H7 打包 | `host/packaging.py` `tools/package.py` |
| M9 控制台（**只读**） | `host/ui/` |

### 11.4 路线图阶段摘要

| 阶段 | 内容 | 为什么在这个位置 |
|---|---|---|
| 0 | `supervisor` + 代理接线 + `host/__main__` | 把三个库变成一个产品 |
| 1 | 浏览器代理策略 + 优雅关闭 | 没策略没人用代理；没关闭会**断网** |
| 2 | 控制台写框架 + 改密码/词库/策略页 + 托盘 + 向导 | GUI 现在只能看不能改 |
| 3 | 服务宿主 + 专用账号 ACL + 恢复动作 | 需要先有 supervisor |
| 4 | exe 入口 + Inno Setup + 便携 + 卸载 | 交付形态 |
| 5 | 全系统验收（真机 / 降级 / 权限矩阵） | **从未做过** |

### 11.5 纯 Python 引擎侧

**已无「不需要平台 API」的待做项。** 历史记录：

| 项 | 提交 |
|---|---|
| M1 规则语法 + 144B 记录 + 四名单 | `f4b780a` `5745505` |
| M2 判定链 + 四段降级骨架 | `5bf7025` `7140f2b` |
| M8 与 M3/M4 汇总接线 | `8295025` |
| M7 日志 / 配置 / 密码 / 卸载 | `7475f5c` `6c303a7` `7e8f670` `36d64a0` |
| 打包 + CLI | `3e59380` |
| M3 双后端 + 分块 + 相似度 | `0813952` `59becca` `cdedf34` |
| 测试框架（契约/不变量/结构） | `7140f2b` `420befa` |
| M4 图像（4 个模块） | `ec2c067` `cd24d8f` `652b3a1` |
| M8 时间调度（J7） | `schedule.py` |

---

## 12. 已知不做（避免反复讨论）

| 项 | 理由 |
|---|---|
| HNC 句法分析（`GetSentenceALC` 线） | 原厂零调用，凑齐 `.lib` 也无用（`docs/engine-call-graph.md`） |
| 复刻原厂轮廓/NSFW 判定算法 | 架构 §M4 目标是 ONNX 分类器，不是复刻（`docs/image-layer.md`） |
| 继续逆向剩余 15 个 `CHECKEX*` | 已还原两个最大的；剩余多为阈值变体，对目标无益 |
| 矩形分析 `0x10006ee6` | 0.54/252 语义未明；代码抛 `NotReversed` |
| 原厂打分表当通用词库 | 是某份文档的原文片段，非规范化词表（`docs/replication-status.md`） |
| `.urf` 元数据区 | 熵 7.98 加密，GIF 已明文可提取（`analysis/probe/p23`） |
| GDI/API hook 实现 | 架构明确改代理 + 无障碍/OCR（`docs/architecture.md`） |
| 防卸载/加固 | 架构明确不做 |

---

## 13. 修订记录（防误读）

本清单曾长期停留在 M8 完成时的状态，导致多处「待做」其实是「早已完成」。
2026 年这次核对逐条比对 `src/` 与 `tests/` 后修正。记录在此，避免再犯。

| 曾被写成「待做」 | 实际状态 | 何时完成 |
|---|---|---|
| 四名单运行时模型 | DONE `namelists.py` | `5745505` |
| 144B 记录编解码 | DONE `namelist.py` | `f4b780a` |
| 规则语法引擎 | DONE `namelist.py` | `f4b780a` |
| 第二后端 | DONE `backends.py` | `0813952` |
| 传输层四段 | DONE（骨架）`transport.py` | `7140f2b` |
| 配置持久化 | DONE `config.py` | `6c303a7` |
| 日志 | DONE `eventlog.py` | `7475f5c` |
| 打包 + CLI | DONE `pyproject.toml` `cli*.py` | `3e59380` |
| 「没有 CLI、没有打包配置」 | **错误**，两者都有 | `3e59380` |

**教训**：清单是给未来的自己看的。写完代码不更新清单，等于把进度藏起来。
以后每次提交若改变清单里的任何一行，必须同一次提交内改掉。
