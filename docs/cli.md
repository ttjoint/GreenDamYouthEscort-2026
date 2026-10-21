# 命令行工具（诊断用）

`src/green_dam/cli.py`，入口 `green-dam`。测试 `tests/test_cli.py`，21 项。

## 1. 定位

这是**诊断工具**，不是产品 UI。它回答关于配置与判定的问题，让库不必写临时
脚本就能跑起来。M9（托盘、调度、日志查看器）是另外的工程。

两条纪律，继承自库：

- **只打印计数与开关**，不打印名单条目、不打印词表词条
- **不打印命中内容**（日志存摘要，CLI 报告也如此）

## 2. 子命令

| 命令 | 用途 |
|---|---|
| `status` | 开关状态 + 四名单计数/容量 |
| `policy` | 解码后的策略数组（策略号 + 名称 + 值） |
| `check` | 跑一条请求/文本，输出动作、来源、原因、事件 |
| `validate` | 校验策略文件（`--strict` 加值域检查） |
| `log` | 回读 JSONL 事件日志 |
| `save-defaults` | 写出厂默认值到指定路径 |

所有子命令都支持 `--json`。

## 3. 示例

```bash
# 看配置状态（读真实安装树）
green-dam status --config /path/to/System32

# 看策略数组
green-dam policy --config /path/to/System32

# 跑一条请求
green-dam check --config . --host example.com --port 80

# 跑一段文本（无词表时会明确说 no_wordlists）
green-dam check --config . --text "some text"

# 校验并写出厂默认
green-dam validate xconfigs.dat --strict
green-dam save-defaults xconfigs.dat

# 回读日志
green-dam log events.jsonl
```

## 4. 实际输出（真实安装树）

```
$ green-dam policy --config .../System32
source: .../xconfigs.dat
policy  value  name
     4      3  log_retention
     5      2  image_filter_level
     6      1  image_filter_auto_blacklist
     8      1  block_programs_port
     9      1  block_programs_process
    14      1  whitelist_priority
    ...
```

注意出厂数据里**策略 13（允许图像过滤）为 0**，策略 14（白名单优先）为 1。
所以 `status` 显示 `image filter: False (level 2)` 是**正确的**：级别已设为
"中"，但总开关未开。这与"用户需手动启用图像过滤"的产品行为一致。

## 5. 写 CLI 时修掉的一个真实 bug

`check --text` 在无词表时最初报 `reason: no_evidence`，而不是
`no_wordlists`。原因有两层：

1. `policy.assess()` 在两侧都无动作时硬编码返回 `"no_evidence"`，丢掉了
   `Verdict.reason`
2. `Filter.check()` 在 `_engine is None` 时根本没构造 `Verdict`

两处都修了。这很重要：**"没在看"和"看了没问题"必须可区分**——这是整个
项目对原产品最核心的行为修正之一。测试 `test_text_without_wordlists` 钉住。

## 6. 打包

`pyproject.toml`：

- `requires-python = ">=3.10"`（用到 `X | None` 语法）
- `dependencies = []` —— **核心零依赖**，纯标准库
- `[project.scripts] green-dam = "green_dam.cli:main"`
- `[tool.pytest.ini_options] testpaths = ["tests"]`

M4 图像落地时会加自己的 optional extra，核心保持可裸装。

## 7. 未做

| 项 | 说明 |
|---|---|
| 配置文件写入（除 save-defaults） | 修改单个策略的 CLI 未做 |
| 词表加载 | `status` 显示 `wordlists: not loaded`，加载路径未定 |
| 日志轮转/过滤 | `log` 只做全量回读 |
| 交互式 UI | 属 M9 |
