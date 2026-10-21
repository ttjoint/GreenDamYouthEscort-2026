# 门面层：把各层串成一条调用链（已实现）

`src/green_dam/filter.py`。测试 `tests/test_filter.py`，21 项端到端。

## 1. 为什么需要这一层

各层都是刻意独立的：`sysdat` 只解码、`namelists` 只存储、`intercept` 只判定、
`policy` 只权衡、`eventlog` 只记录。这保证了每一块都能单独测——但也意味着
**没有任何东西回答调用方唯一关心的问题**：

> 给定这个请求，我该怎么做，以及会记下什么？

`Filter` 就是这个答案。它持有配置、名单、策略表、日志，暴露两个方法：

```python
f = Filter.from_config_dir(Path("./config"))
f.check_request(Request("outbound", host="x", port=80))   # → Assessment
f.check_text("some text")                                  # → Verdict
f.check(request=..., text=...)                             # → Assessment（合并）
```

## 2. 三个方法的分工

| 方法 | 输入 | 输出 | 日志 |
|---|---|---|---|
| `check_request` | `Request` | `Assessment` | 1 条（network） |
| `check_text` | `str` | `Verdict` | 1 条（text） |
| `check` | 两者 | `Assessment` | **1 条**（合并后） |

`check` 只记一条。两次记录会让每个计数翻倍，测试
`test_one_log_record_per_check` 钉住这点。

## 3. 日志只有一个所有者

`Interceptor` 本身支持挂日志（`test_eventlog.py` 里有），但 `Filter` 内部
创建的 `Interceptor` **不带日志**（`log=None`）。理由：如果两处都能记，迟早
会不一致。`Filter` 是唯一记录者，且它知道 `direction` 和合并后的结果。

## 4. 开关只在一处检查

sys.dat 槽 2（关闭过滤）、槽 10（关闭关键字过滤）。这两个开关**不在各阶段里
分散检查**，而是 `Filter._gate()` 统一处理，并记 `gated` 事件。

这样日志能区分两件完全不同的事：

| 情况 | 日志 | 对用户意味着 |
|---|---|---|
| 查了，没发现 | `allow` / `no_action` | 内容干净 |
| 被告知别查 | `gated` | **没在看** |

原产品把这两者混为一谈，用户看到"没拦"就以为"没问题"。这是复刻时明确要
修正的行为。

`test_gate_never_reports_clean` 断言：被 gate 时 `reason` 绝不能是
`no_stage_matched`，`action` 绝不能是 `allow`。

## 5. 默认什么都不映射

`Filter` 构造时 `level_actions={}`、`attested=False`。词表命中会产出 `Finding`
（证据），但 `action=None`——因为原厂"分 → 动作"的映射仍未还原。

只有显式设置 `f.policies.level_actions[2] = "block"` 才会有动作。测试
`test_wordlist_hit_with_unmapped_level_has_no_action` 钉住默认行为。

## 6. 出厂默认来自 CD

`Filter.from_config_dir()` 找不到配置文件时，用
`config.default_for_fresh_install()`——即 CD 上 `sys.dat` 的原始值，不是
"预设级别 6"分支。两者在槽 11/18 上不同，都已记录。

## 7. 验收（21 项）

| 组 | 覆盖 |
|---|---|
| 构造 | 新装用出厂默认、读现有 xconfigs、status 不含条目 |
| 网络路径 | 阻断记 1 条、放行记 allow、阻断带 stage |
| 开关 | 槽 2 同时管网络与文本、槽 10 只管关键字、**gate 绝不报干净** |
| 文本路径 | 无词表不报错但记日志、未映射无动作、映射后有动作 |
| 合并 | 网络优先、网络干净时用文本、**每次 check 只记 1 条**、单路调用 |
| 不泄漏 | 端到端日志不含主机名/规则/正文 |
| 持久化集成 | 改配置 → 存 → 重载 → 行为改变；文件仍 268 字节 |

## 8. 仍未接的层

| 层 | 现状 |
|---|---|
| M2 传输（代理/防火墙） | `check_request` 已就绪，但没人给它真实流量 |
| M3 语义后端 | `check_text` 只用词表，未接 `semantic.py` |
| M4 图像 | 未接 `assess` |
| M5 屏幕文字 | 未做 |
| M6 进程控制 | 名单 B 已在判定链里，但"终止进程"动作未做 |
| M9 控制台 | 未做，无 CLI |

也就是说：**判定与记录已闭环，输入与执行还没有**。
