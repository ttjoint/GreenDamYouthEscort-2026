# 分块层接入（E6 已实现）

`src/green_dam/chunkpath.py`。测试 `tests/test_chunkpath.py` 27 项 +
`test_cli.py::ChunkCliTest` 6 项。

## 1. 先说清楚接了什么、没接什么

`chunker.py`（1,192 行）此前完全没被使用。现在接上了——**但不是接进
`hnc.classify`**。

| 能做 | 不能做 |
|---|---|
| 分块结构作为**文本特征证据** | 喂给 `hnc.classify` |
| 结构摘要进日志 | 推断句类 |
| 无谓语/单块/结构化三态区分 | 编造 `role_a`/`role_b`/`participants` |

## 2. 为什么不接 `hnc.classify`

`hnc.classify` 需要 `role_a`、`role_b`、`participants` 三个**整数编码**。
这些整数到语言学角色的映射在 `SENTEN.LIB` 里，**该文件从未随产品发布**。

`chunker.Chunking.to_frame_hint()` 因此返回 `None`，且 `test_chunker.py:106`
钉住了这个行为：

```python
def test_to_frame_hint(self):
    self.assertIsNone(r.to_frame_hint())
```

所以"把分块接进句类分析"这件事**在证据层面不可行**。硬接等于编造一个
看起来能跑的分类器——正是本项目存在的理由要避免的失败模式。

`test_chunkpath.py::NoFrameInventedTest` 用 **AST 检查**（不是文本匹配）断言
`chunkpath.py` 里不出现 `role_a`/`role_b`/`participants`/`spare`/`role2`
任何标识符。

## 3. 那分块结构本身有什么用

两个**可测量**的性质：

| 性质 | 含义 | 为什么有值 |
|---|---|---|
| **退化** | 分块器找不到谓语 | 这是句子的客观形状，不是猜测 |
| **结构形态** | `A+E+B` vs 裸 `E` | 单块片段与完整从句是真实区别 |

三态：

| 形态 | 判据 |
|---|---|
| `no_predicate` | 没有 E 块 |
| `single_block` | 有 E 但只有一块 |
| `structured` | 有 E 且多块 |

## 4. 证据等级：level 0，action None

分块结构**不是有害证据**，是一个可与其他证据权衡的特征。

```python
Finding(levels=(0,), action=None, attested=True, reason=shape)
```

- `level=0`：与词表/ HNC 的等级体系一致，0 表示"未被判定为有害"
- `action=None`：是否因结构异常而阻断是策略，原厂映射未还原
- `attested=True`：分块器的输出是**测量**，不是猜测（与 HNC 等级不同）

## 5. 默认不产出证据

```python
ChunkSource()                    # emit_findings=False（默认）
ChunkSource(emit_findings=True)  # 才产出 Finding
```

理由：大多数调用方只想要**结构摘要**（记日志、做路由），不想引入一个
没有危害含义的证据。默认开会让"结构异常"意外影响判定。

## 6. 结构摘要可安全进日志

```python
{'length': 9, 'blocks': 3, 'main': 3, 'secondary': 0,
 'pattern': 'A+E+B', 'shape': 'structured', 'min_confidence': 0.6}
```

**只有计数和角色名，没有文本**。测试
`test_summary_contains_no_text` 断言句子片段不出现在摘要里。

这是它能进日志的原因——与 `Finding.__repr__` 的修正是同一原则。

## 7. CLI

```bash
$ green-dam chunk --text "如果下雨，比赛就取消"
shape      : structured
pattern    : Cn+A+E
blocks     : 3 (main 2, secondary 1)
min conf   : 0.40
  (use --show-text to print block text)
```

默认**只打印角色不打印文本**，`--show-text` 才显示。测试断言默认输出不含
句子片段。

## 8. `Filter` 接入

```python
f = Filter(chunker=ChunkSource(emit_findings=True))
f.chunk_structure("...")   # -> dict | None
f.check_text("...")        # 结构证据进入 findings
```

`status()` 新增 `chunker_loaded`。

## 9. 验收（33 项）

| 组 | 覆盖 |
|---|---|
| 形态 | 完整从句、无谓语、空文本、单块、条件句 |
| 摘要 | 计数与 pattern、辅块单列、min_confidence、JSON 友好、**不含文本**、空分块 |
| 来源 | 默认不产出、无结构时产出、结构化时不产出、**无 action**、attested=True、便捷方法 |
| **不伪造 Frame** | `to_frame_hint()` 仍返回 None、不 import classify、**AST 断言无整数角色标识符** |
| Filter | 结构摘要可用、无 chunker 时 None、findings 进入文本路径、不产出时为空、status 反映、结构化文本零证据 |
| CLI | 显示结构、默认不打印文本、`--show-text` 才打印、JSON、JSON 无文本、无谓语 |

## 10. 顺带修的一个真实 bug

写测试时发现：配置了 `chunker` 但 `TextSources.configured` 没算它，导致
`check_text` 返回 `reason="no_wordlists"`——**明明配了却说没配置**。

这正是本项目反复强调的那类错误："没在看"与"看了没问题"必须可区分，
反过来"配了"也不能报成"没配"。已修，`test_structured_text_yields_nothing`
钉住。

## 11. 未做

| 项 | 说明 |
|---|---|
| 句类判定 | 需 `SENTEN.LIB`，不可行 |
| 分块结果参与阻断决策 | 需原厂 分→动作 映射 |
| `similarity.py` 接入 | 句子相似度（291 行）仍未使用 |
| 多句分块的跨句结构 | 当前逐句处理 |
