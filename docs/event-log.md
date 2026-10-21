# 事件日志（M7 的一部分，已实现）

`src/green_dam/eventlog.py`。测试 `tests/test_eventlog.py`，22 项。

## 1. 为什么不用标准 `logging`

三条要求 `logging` 单独满足不了：

| 要求 | 来源 | 做法 |
|---|---|---|
| 日志必须说明**为什么**被拦 | `architecture.md` | `stage` / `reason` / `source` 是**字段**，不塞进消息字符串 |
| 日志必须**可审计** | `architecture.md`（"journalled to a manifest"） | JSON Lines，可 `diff`、可归档、不用解析散文 |
| 日志**不得泄漏内容** | 原厂把收集的文本上传了，本项目明确不做 | 只记录"在哪命中"，不记录命中什么 |

第三条是本模块存在的核心理由。原产品最受诟病的行为之一就是收集并上传用户
文本。复刻时如果日志把命中的正文写进去，等于换了个地方重犯同样的错误。

## 2. 脱敏规则（只说一次）

**任何来自用户内容的字段，一律以「跨度 + 摘要」表示，不存原文。**

- `span_length`：命中片段长度
- `content_digest`：命中片段的截断 SHA-256（16 hex = 64 bit）

摘要的用途是**关联**："同一个东西被拦了两次"。管理员能看出重复，但日志里
永远没有那个东西本身。

`LogRecord.detail` 之类字段**不存** `Decision.detail` —— 后者可能含命中串。
`record_for_decision()` 只读 `stage`/`reason`，这是刻意的。

## 3. 数据结构

```python
LogRecord(event, source, stage, reason, direction,
          span_length=0, content_digest="", when="", seq=0)
```

| 字段 | 取值 |
|---|---|
| `event` | `block` / `allow` / `no_action` / `gated` |
| `source` | `network` / `text` / `policy` |
| `stage` | `process` / `address` / `host` / `content`（网络事件） |
| `reason` | 短机器 token，如 `host_listed`、`keyword_rule` |
| `direction` | `outbound` / `inbound` |
| `when` | UTC ISO-8601，秒级，带 `Z` |
| `seq` | 由写入方分配，单调递增 |

`gated` 是专门的事件类型：sys.dat 槽 2/10 把证据"静音"了。这**不是** allow，
必须单独可见 —— 否则用户会以为"没拦 = 没问题"。

## 4. 写入

```python
log = EventLog(path=Path("events.jsonl"))
log.append(record_for_decision(decision))
```

- 惰性打开 sink，**每条 flush**，崩溃最多丢一条
- 加锁（服务宿主会多线程调用）
- 目录自动创建
- `path=None` 时纯内存，供测试和嵌入式使用

## 4b. 接线（已实现）

`Interceptor` 有可选的 `log` 字段。接上就每条判定记一条，不接就是纯函数
（测试默认不接，保持判定链可单独验证）。

```python
log = EventLog(path=Path("events.jsonl"))
icpt = Interceptor(lists=lists, log=log)
icpt.decide(Request("outbound", process="x"))
```

日志放在 `Interceptor` 里而不是调用方，因为**只有它知道是哪个阶段命中的**，
而那正是日志要记的字段。

## 5. 验收（27 项）

| 组 | 覆盖 |
|---|---|
| digest | 截断长度、稳定性、区分度、空串 |
| 记录形状 | 必需键、空字段省略但锚点保留、单行 JSON |
| 由 Decision 构造 | block/allow、**detail 被忽略** |
| 由 Assessment 构造 | 网络阻断保留 stage、文本阻断无 stage、no_action、gated |
| **不泄漏内容** | 正文、主机名、进程名**三项分别断言**不出现在日志里 |
| sink | JSONL 往返、序号单调、补时间戳、上下文管理器、查询 |
| 接线 | 阻断/放行都记、方向正确、每条都记、无 log 时纯函数、接线后仍不泄漏 |

「不泄漏」那三项是回归测试：一旦有人给 `LogRecord` 加个 `matched` 字段并
顺手填上，这三项会立刻失败。

## 6. 未做（属 M7 其余部分）

| 项 | 说明 |
|---|---|
| 日志轮转 / 保留期 | 原厂 sys.dat 槽 4（日志保留时间）可读，轮转策略未定 |
| 变更日志（安装清单） | `architecture.md` 要求安装时记 manifest、卸载反向回滚；属卸载功能，未做 |
| 与 M9 控制台的日志查看器 | UI 未做 |
| 签名 / 防篡改 | 未定，需先决定威胁模型 |
