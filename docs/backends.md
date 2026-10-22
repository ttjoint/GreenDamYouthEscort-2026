# M3 双后端接口与 A/B 对比（E1/E2 已实现）

`src/green_dam/backends.py`。测试 `tests/test_backends.py` 32 项 +
`test_evidence_paths.py::BackendSwapTest` 6 项 + `test_cli.py::AbTest` 5 项。

## 1. 架构要求

`architecture.md` §M3：

```
M3 ──► SemanticBackend.analyse(text) -> [Hit]
         ├── HncBackend    — 概念网络路线
         └── JiebaBackend  — 统计分词，参考/回退
```

> 两者可选，且可对同一语料做 A/B。「不能对照基线测量的复刻只是意见。」

## 2. 两处偏离，都写在代码里

### 2.1 参考后端不是 jieba

架构点了 jieba 的名，但同一份文档也要求**核心零第三方依赖**，契约测试
（`test_contracts.py::DependencyTest`）强制这一点。

所以 `SegmentationBackend` 是**纯标准库实现的统计分词器**：对词频表做正向
最大匹配，这与 jieba 默认模式属于同一族算法。**它按自己是什么来命名，
不冒充一个它不是的库。**

若将来把 jieba 作为可选依赖引入，它实现同一 `Protocol` 即可，调用方不用改。

### 2.2 两个后端回答的不是同一个问题

| 后端 | 回答 |
|---|---|
| `HncBackend` | 出现了哪些已知词条，各自多严重 |
| `SegmentationBackend` | 词边界在哪 |

拿严重度去比较两者毫无意义。**有意义的、也是 `compare()` 真正测量的，是
span（命中区间）上的一致性**：两方在同一个位置都命中了什么，以及只有一方
命中的是什么。

## 3. 接口

```python
@runtime_checkable
class SemanticBackend(Protocol):
    name: str
    def analyse(self, text: str) -> BackendResult: ...

@dataclass(frozen=True)
class BackendResult:
    backend: str
    hits: tuple[Hit, ...]
    text_length: int
    attested: bool = False      # 两个后端都是 False
    segments: tuple[str, ...]   # 分词后端才有
```

`attested=False` 是**字段而非注释**：没有后端是原引擎的位级复刻。
`architecture.md` 明确说明原因——每词 normal/blocked 标志存在 `FMean.tmp`
里且从未随包发布，HNC 语法本体也未完全还原。

## 4. A/B 对比

```python
left, right = load_ab_pair()
report = run_ab(left, right, corpus)
report.summary()
```

`Comparison` 记录三类 span：

| 字段 | 含义 |
|---|---|
| `agreed` | 双方都命中 |
| `only_left` | 只有左方命中 |
| `only_right` | 只有右方命中 |

`agreement` = `agreed / total`（Jaccard 式）。**不叫"错误率"**——两个后端
都不是已证实的，分歧是待查的问题，不是某一方的 bug。

**部分重叠算分歧**：`(0,2)` 与 `(0,3)` 是对"词在哪"的不同主张，合并会
掩盖掉对比本来要产生的信息。

## 5. 真实语料上的第一次 A/B

```bash
$ green-dam ab corpus.txt
backends       : hnc vs segmentation
texts          : 3
mean agreement : 0.6667
identical      : 2
disagreed      : 1
spans agreed   : 0
spans only L/R : 0 / 1
```

分歧来源清楚：`segmentation` 在 "this is normal" 里切出一个词条，而
`HncBackend` 因该条等级为 0 把它丢弃。

**这正是 A/B 的价值**——它把"两个后端对同一句话看法不同"变成可见的数字，
而不是各自跑各自的。

## 6. 接入 `Filter`

```python
Filter(backend=HncBackend(matcher=...))      # 新方式
Filter(hnc=matcher)                          # 旧方式，仍支持
```

`backend` 优先于 `hnc`。保留 `hnc` 是因为既有调用点都在用它，破坏性改动
没有收益。

`_backend_findings()` 同时接受 `SemanticBackend`（有 `analyse`）和裸
matcher（无 `analyse`），所以两种风格都能工作。

## 7. 验收（43 项）

| 组 | 覆盖 |
|---|---|
| Protocol | 两后端都满足接口、都有 name、都返回 `BackendResult`、都不声称 attested |
| HncBackend | 找到词条、丢弃等级 0、min_level 可配、保留 all_levels、最长优先、span 正确、空文本 |
| SegmentationBackend | 正向最大匹配、未知字符单字输出、空文本、等级恒为 0、单字不算命中、同表构建、确定性 |
| Compare | 完全一致、完全不相交、部分重叠、**近似但不同算分歧**、双方都空、记录后端名 |
| AbReport | 空报告、summary 只含计数、语料级运行 |
| 真实语料 | 成对加载、同表、真实词条上跑出共识、正常文本两方都干净 |
| Filter 接入 | 两后端都可换、裸 matcher 仍工作、backend 优先、status 反映、无后端报 `no_wordlists` |
| CLI | summary 输出、JSON、空语料、缺失文件、不回显语料 |

## 8. 未做

| 项 | 说明 |
|---|---|
| 真正的 jieba 后端 | 需可选依赖，契约测试目前禁止 |
| 语料级人工标注 | 没有 ground truth，"哪个后端更准"无法回答 |
| 分块层（`chunker`）接入判定 | E6，仍未做 |
| HNC 语法本体 | 未完全还原，非本项目能力范围 |
