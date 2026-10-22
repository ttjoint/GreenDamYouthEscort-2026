# 安装日志与卸载（I9/I10 已实现）

`src/green_dam/journal.py`。测试 `tests/test_journal.py` 37 项 +
`test_cli.py::UninstallCliTest` 8 项。

## 1. 放置位置的决策

| 候选 | 评价 |
|---|---|
| `config.py` | ❌ 配置是**策略数据**，清单是**变更记录** |
| `eventlog.py` | ❌ 那是**运行时判定**事件，清单是**安装期系统变更** |
| **新模块 `journal.py`** | ✅ 单一职责：记录变更以便逆序撤销 |

判断依据：清单有两个别处没有的性质——

| 性质 | 后果 |
|---|---|
| **顺序有语义** | 撤销必须逆序：后一个变更可能依赖前一个（引用服务账户的防火墙规则、先建的目录里的文件） |
| **可逆性契约** | 每条变更**必须声明如何撤销** |

用 `set` 存变更会丢掉顺序；用注释记撤销方法会腐烂。

## 2. 为什么是"边装边记"而不是"照着安装脚本推"

架构 §M7 明确把"卸载后残留注册表项"列为**不继承的行为**。保证严格逆操作的
唯一办法是**做的时候写下来**。

> 从安装脚本生成的清单是**承诺**；安装运行时写下的清单是**记录**。

## 3. 可逆性是构造时强制的

```python
Change(kind, target, inverse, detail)
```

`inverse` 是**必填**字段。一个没人想过怎么撤销的变更**无法被记录**——
构造时就抛 `JournalError`。

这比"卸载时再想办法"可靠：问题暴露在安装期，那时还能中止。

## 4. 逆序撤销

```
安装顺序：防火墙 → 证书 → 服务 → 注册表 → 文件 → 设置
撤销顺序：设置 → 文件 → 注册表 → 服务 → 证书 → 防火墙
```

`plan_uninstall()` 直接 `reversed(changes)`，并给每步编号。测试
`test_architecture_order_is_representable` 用架构文档列出的顺序做断言。

## 5. 无法撤销的，报告而不是丢弃

```python
plan.unreversible   # 元组，不是被静默跳过
plan.complete       # 只有全部可撤销才为 True
```

`--strict` 时改为抛 `UnreversibleChange`。

默认**报告而非中止**：拒绝运行的卸载器会把用户留在半安装状态，报告让他们
自己决定。

这与项目一贯的原则一致：**"没能撤销"不能看起来像"全都撤掉了"**。

## 6. 实际输出

```
$ green-dam uninstall plan --manifest journal.json
would perform 4 step(s), in this order:
   1. delete-files file C:\Program Files\GreenDam
   2. delete-service service GreenDamSvc
   3. revoke-cert certificate ab12cd34
   4. remove-rule firewall_rule GreenDam block 80
no reboot will be requested
```

注意最后一行：架构要求 **"No forced reboot"**，所以 dry-run 明确说出来。

## 7. 两种输出，两种用途

| 输出 | 内容 | 用途 |
|---|---|---|
| `render()` | **列出具体目标** | 给管理员看，"到底会删什么" |
| `summary()` | **只有计数** | 机器读、进日志 |

`render()` 必须列出目标——那正是 dry-run 的意义。`summary()` 保持计数
（与项目其他地方的脱敏纪律一致），测试断言目标名不出现在 summary 里。

## 8. 卸载后保留清单

`mark_rolled_back()` 只把 `rolled_back` 置为 True，**不删除文件**。

理由：第二次运行时能回答"已经做过了"，而不是重复执行破坏性步骤。
测试 `test_second_plan_is_noop` 钉住。

## 9. 卸载需要家长密码

`rollback` 动作先 `secret.check()`，通过后才标记。无密码记录时返回 2 并
**拒绝标记**——不能因为"没设密码"就放行。

## 10. 验收（45 项）

| 组 | 覆盖 |
|---|---|
| Change | 必填 target/inverse、往返、repr 不显示 detail、缺字段/未知 kind/坏 detail |
| Manifest | 保序、kinds 只含计数、往返、拒绝异产品/错 schema/坏 JSON/非对象/非列表 |
| 存储 | 存读、无临时残留、缺失、覆盖 |
| **顺序** | 逆序执行、编号连续、架构顺序可表达 |
| **安全** | 空清单警告、已回滚是 no-op、**无法撤销被报告而非丢弃**、strict 抛错、**不请求重启** |
| 渲染 | 逆序列出、summary 只含计数、JSON 可序列化 |
| 回滚标记 | 持久化、二次计划为 no-op |
| 一致性 | 干净清单、错产品、错 schema |
| CLI | 逆序、说 no reboot、JSON 计数、verify、缺失文件、密码必需/错误/正确 |

## 11. 未做

| 项 | 说明 |
|---|---|
| 真正执行撤销 | 属 Windows 宿主（防火墙/证书/服务 API），需真机 |
| 安装期写清单 | 同上 |
| 部分失败的回滚 | 未定策略：一步失败后是否继续 |
| 清单签名/防篡改 | 未定，需先决定威胁模型 |
