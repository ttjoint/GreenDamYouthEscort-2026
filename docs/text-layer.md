# M3 文本层接线（本轮完成）

`src/green_dam/semantic.py` 的 HNC 打分表已接入 `Filter.check_text()`，
与词表证据并列。

## 1. 两个证据源，不合并

| 源 | 类 | 数据 | 等级来源 |
|---|---|---|---|
| 外部词表 | `wordlists.WordlistIndex` | 外部 `.lib` 文件 | 条目自带的 level |
| HNC 打分表 | `semantic.LegacySemanticMatcher` | `falun-terms.json` | **链引用条目的 hong_hei** |

**为什么不合并成一个索引**：

- `WordlistIndex` 是通用多源词表，一条词可能有多个 sources
- HNC 表的等级归属规则不同：等级跟**链条目**走，不是跟池切片走。
  同一个池切片作为不同链条目的引用，等级可以完全不同。

把两者强行合并，会把其中一套等级归属规则强加给另一套数据——这正是
`semantic.py` docstring 里警告过的那个真实 bug（早期按池切片取等级，
导致高等级词条全部退化为 0）。

## 2. 接线时发现并修掉的真实缺陷：等级 0 误命中

直接接入后，`"clean text here"` 命中了 `clea`（4 字符 ASCII 子串）。

量化结果：

| 项 | 数值 |
|---|---|
| 总词条 | 10,182 |
| **最高等级 = 0 的词条** | **9,077（89.1%）** |
| 单字符词条 | 294 |
| 2 字符词条 | 871 |
| 纯 ASCII 词条 | 77（其中 48 个 ≤4 字符） |

等级 0 在 HNC 表里的含义是"**未被判定为有害**"，不是"无害证据"。

**修法**：`to_findings()` 只转换 `max(levels) >= 1` 的命中。修后：

```
'clean text here'          → 0 findings
'hello world'              → 0 findings
'this is normal'           → 0 findings
```

这是原产品最典型的失败模式（误报），不能在新实现里重演。

## 3. 命中转 Finding 的规则

```python
semantic.analyse_findings(matcher, text) -> list[policy.Finding]
```

| 字段 | 值 | 理由 |
|---|---|---|
| `kind` | `KIND_SCORED` | 等级来自打分表，不是类别规则 |
| `sources` | `("falun-terms",)` | 来源可追溯 |
| `levels` | `hit.all_levels` | 保留全集（同一词可能多等级） |
| `attested` | `True` | 等级读自出厂数据，非猜测 |
| **`action`** | **`None`** | 分→动作 映射未还原 |

`action=None` 是刻意的：即使等级 7 也只是"等级 7"，不是"该阻断"。

## 4. 两个同名 `Verdict` 的处理

`semantic.Verdict`（测量）与 `policy.Verdict`（决策）同名不同义。

**不合并、不改名**，而是让桥接函数显式转换。理由：改名会破坏
`test_semantic.py` 里 17 项已有测试的语义；合并会让"测量"与"决策"混为一谈。
桥接处的注释明确写了这一点。

## 5. `Filter.check_text` 的合并逻辑

```python
findings = []
if wordlist engine:  findings += engine._word_findings(text)
if hnc matcher:      findings += analyse_findings(hnc, text)

if not findings:     reason = "no_wordlists" / "no_evidence"
else:                verdict = engine.verdict(text, findings=findings)
```

注意 `no_wordlists` 与 `no_evidence` 的区分：

| 情况 | reason |
|---|---|
| 两个源都没配置 | `no_wordlists` |
| 配置了但没命中 | `no_evidence` |

这条区分与 `gated` vs `allow` 是同一原则：**"没在看"不能报成"看了没问题"**。

## 6. 验收（9 项新增）

| 组 | 覆盖 |
|---|---|
| 桥接 | 等级 0 不算证据（3 个正常句子）、Finding 形状、span 指向命中、高等级也无 action |
| Filter | 干净文本零命中、仅 HNC 命中只给证据不给动作、status 反映 hnc、两源共存、gate 优先于 HNC |

## 7. 未做

| 项 | 说明 |
|---|---|
| 第二后端（规则式） | 架构要求"两个可互换后端"，目前只有 HNC 一个 |
| 分块层（`chunker.py`）接线 | 1,192 行已有，但未接入 `check_text` |
| 语义判定（`semantic.py` 之外的 `chunker`→判定） | 未接 |
| 等级阈值策略 | 未定；原厂 分→动作 未知 |
