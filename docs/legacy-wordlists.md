# 原厂词库加载（M1 补齐）

`src/green_dam/legacy_words.py`。测试 `test_legacy_words.py` 38 项 +
`test_cli.py::LoadWordsCliTest` 4 项。

## 1. 为什么解码器要搬进 `src/`

`tools/legacy_wordlist_codec.py` 早就能解码，但 `tools/` 是**一次性分析区**：
不打包、引擎不可导入、不受依赖契约约束。

引擎需要在运行时加载真实词库，所以解码器移进 `src/`。`tools/` 保留自己那份
给既有分析脚本用；两份解码结果一致（测试同时跑两份比对）。

## 2. 格式（无密钥的 2:1 混淆，不是加密）

每个明文字节编成两个可打印符号（`0x30..0x6F`）：

```
V = (v0 << 6) | v1              v = symbol - 0x30
明文 bit 0 <- V[0]              明文 bit 4 <- V[6]
明文 bit 1 <- V[1]              明文 bit 5 <- V[7]
明文 bit 2 <- 1 - V[2]          明文 bit 6 <- 1 - V[8]
明文 bit 3 <- 1 - V[3]          明文 bit 7 <- 1 - V[9]
V[4], V[5], V[10], V[11] 是填充
```

CRLF 原样透传不编码；载荷是 GBK。

## 3. 分类索引 `wfile.dat`

`wfile.dat` **不是词表**，是索引：

```
1 成人/色情网站 adwfil.dat
1 成人/色情网站(严格) adwapp.dat
1 暴力游戏 vgamfil.dat
1 同性恋 lgwfil.dat
1 非法活动/毒品 iawfil.dat
```

注意**每行类别号都是 1**——那个数字在出厂文件里不构成区分，所以本模块
保留名称、**忽略数字**，不从中编造分类体系。

## 4. 真实加载结果（实测）

```
file              terms    bytes  label
adwapp.dat         6856   118692  成人/色情网站(严格)
adwfil.dat         2003    37820  成人/色情网站
iawfil.dat          113     2696  非法活动/毒品
lgwfil.dat           54     1432  同性恋
vgamfil.dat         117     3008  暴力游戏

lists       : 5
total terms : 9143
unique      : 9048
```

**9,143 条词 → 9,048 个唯一词**（跨文件有重复，去重正确）。

## 5. 三个刻意的决定

| 决定 | 理由 |
|---|---|
| 词条标 `attested=True` | 来自原厂自带数据，与从散文里解析的不同 |
| **不设 `level`** | `.dat` 格式**不带**每词严重度；编一个数字等于往引擎里塞没人测过的值 |
| 未列在索引里的 `*fil.dat` **也加载** | 原厂发出来的表就是原厂想生效的表；静默忽略会**漏过滤** |

## 6. `from_vendor_dir` 的语义

```python
Filter.from_vendor_dir(Path(".../System32"))
```

- 加载策略 blob + 解码全部 `.dat` 词库
- **一个词库都没解出来时**，`words` 保持 `None` 而不是空索引

理由：「没有词表」和「有词表但什么都没匹配」是不同状态，**只有前者该让证据沉默**。

- 只读原厂树，**从不写**。产品只写自己的配置目录。

## 7. 纯文本也接受

`is_obfuscated()` 返回 False 时按 GBK 纯文本读。手写的替换词表不必为了能用
而重新混淆。

> 这里修过一个真 bug：初版对纯文本仍调用 `decode_bytes`，导致
> `WordlistFormatError: symbol out of range`。测试 `test_plain_text_accepted` 钉住。

## 8. CLI

```bash
$ green-dam load-words ../LH-installed-tree/Windows/System32
file              terms    bytes  label
adwapp.dat         6856   118692  成人/色情网站(严格)
...
```

**只打印文件名、标签、计数，从不打印词条**。测试断言词条不出现
（注意：标签来自索引文件，是分类名，会正常显示）。

`python -m green_dam` 现在也可用（`__main__.py` 转发到 `cli.main`）。

## 9. 验收（42 项）

| 组 | 覆盖 |
|---|---|
| 编解码 | ASCII 往返、**全 256 字节往返**、CRLF 保留、空输入、混淆判定、尾部单符号报错、越界符号报错、GBK、行处理 |
| 索引 | 三字段解析、短行/非数字/空行跳过 |
| 单文件 | 混淆加载、`attested=True`、**不编造 level**、纯文本接受、空文件报错、复用索引、标签记录 |
| 目录 | 索引内文件、标签来自索引、**缺失文件上报**、**未列文件也加载**、无索引容错、summary 只含计数 |
| 真实树（5 项，无树则 skip） | 5 个文件、无缺失、全混淆、索引 5 条、名称唯一、词数合理、**索引小于总和**（去重）、summary 不泄漏 |
| CLI | 计数、**不打印词条**、JSON、目录不存在 |

## 10. 未做

| 项 | 说明 |
|---|---|
| 词条 → 类别映射 | 索引里类别号全是 1，无区分信息 |
| 词条 → 等级映射 | 格式不带 |
| 与 HNC 词库（`FalunWord.lib`）合并 | 两个来源等级归属规则不同，已在 `docs/` 说明 |
| 自动更新词库 | 原厂有自动更新（槽 20/21），需网络 |
