# 动作策略层设计（policy layer）

**一句话：本层只把「证据」整理成「有据可查的建议动作」，自己不做任何拦截。**

## 1. 为什么需要这一层

在第 5 轮之前，项目里有三套互相独立的证据：

| 证据源 | 内容 | 现状 |
|---|---|---|
| 中文词条 | `data/external-wordlists.json`，等级来自 `words.dict` | 只能查询，无法定级 |
| 英文分类规则 | `data/legacy-imported/rules/*.json`，5 个类别 | 已解析，未接入 |
| 信任 URL | `data/legacy-imported/trust-urls.json`，1,177 条 | 已导入，未接入 |
| 原厂打分表 | `data/hnc-falun-filter.json`，反动度/红黑度 | 语义未知 |

四套证据**没有任何一处汇合**。查询层返回 `WordHit`，然后就结束了。
「命中之后怎么办」这个问题，代码里根本没有答案。

这一层就是补这个洞。

## 2. 三条硬约束

### 2.1 不臆造动作

原厂 `反动度 0-7` / `红黑度 0-4` 到「放行/记录/警告/阻断」的映射
**没有公开文档，也没有反汇编证据**（`HASrv.dll` 未查）。

因此：

- 本层**不**把任何原厂分数自动翻译成动作；
- 凡是依赖未知语义的映射，一律返回 `action=None` + `reason="unmapped"`；
- 所有默认策略都标 `attested=False`。

### 2.2 词表命中 ≠ 应该屏蔽

`今天`、`公司`、`工作` 也在原厂 trie 里。`敏感词列表.txt` 里也有大量
普通词条（域名、扩展名、地名）。

因此本层区分三种**命中性质**：

| 性质 | 含义 | 默认动作 |
|---|---|---|
| `library_only` | 只在原厂 trie 中出现，无分数 | `None`（不动作） |
| `scored` | 原厂打分表命中（反动度/红黑度） | `None`（语义未知） |
| `leveled` | 外部词库带明确等级 | 由策略表决定 |
| `rule` | 命中英文分类规则 | 由策略表决定 |

### 2.3 来源必须保留

`WordHit.sources` 一路带到 `Finding`。任何一个动作建议都能追回到
具体文件和原始行号（`raw_forms` / `line`）。

## 3. 数据结构

```python
Action = Literal["allow", "log", "warn", "block", "quarantine"]

@dataclass(frozen=True)
class Finding:
    span: tuple[int, int]      # 文本中的位置
    surface: str               # 命中的原文
    kind: str                  # library_only / scored / leveled / rule / trust
    category: str | None       # 原厂类别（adwfil / vgamfil / ...）
    sources: tuple[str, ...]
    levels: tuple[int, ...]
    attested: bool
    action: str | None         # 建议动作，None 表示无据
    reason: str                # 为什么是这个动作

@dataclass(frozen=True)
class Verdict:
    findings: tuple[Finding, ...]
    action: str | None         # 汇总动作，None 表示无法判定
    reason: str
```

`action=None` 是**一等公民**，不是错误。它表示「我查到了证据，但不知道
该做什么」，这是诚实的答案。

## 4. 策略表

`PolicyTable` 是一个显式、可覆盖、可审计的映射：

```python
PolicyTable(
    level_actions={1: "log", 2: "warn", 3: "block", 4: "block"},
    category_actions={"adwapp": "block", "vgamfil": "warn", ...},
    trust_domains=frozenset(),     # 信任表优先
    min_level=None,                # 低于此等级不动作
)
```

默认值全部标 `attested=False` 并在文档里说明「这是我们的选择，不是原厂的」。

## 5. 汇总规则

多个命中如何汇总成 `Verdict.action`：

1. 若任一命中在信任域内 → `allow`（信任表优先，与原厂 `TrustUrl.dat` 用途一致）；
2. 否则取**最强**动作（`block` > `quarantine` > `warn` > `log` > `allow`）；
3. 若所有命中都是 `action=None` → 汇总 `action=None`，`reason="all_unmapped"`。

## 6. 明确不做的事

- 不做实际拦截、不做文件写入、不做进程操作；
- 不引入新的词库，只消费已有 JSON；
- 不猜 `HASrv.dll` 里 `反动度` 的含义；
- 不把 `FalunWord.lib` 的 4,693 条打分表当通用词库。

## 7. 验收标准

| 项 | 要求 |
|---|---|
| 信任表优先 | 有单测，且命中信任域时不产生 block |
| 无等级词不动作 | `levels=()` 的词条 `action is None` |
| 原厂打分命中不动作 | `scored` 类 `action is None` |
| 汇总取最强 | 有单测 |
| 全部无据 | `Verdict.action is None` |
| 可追溯 | 每个 `Finding.sources` 非空 |
| 不打印词条 | 测试只断言结构与计数 |

## 8. 后续接入点

1. `HASrv.dll` 反汇编清楚后，把真实映射填进 `level_actions`，并把
   该行标记为 `attested=True`；
2. 评测集（缺口 D）用本层产出「建议动作」，与人工标注比对，
   得到误报/漏报率。

> **不要往这张表里接 `.urf`。** `1.urf`–`9.urf` 是 AppFace 换肤库的
> 皮肤资源包，与过滤、类别映射都无关，早前「解出类别映射再填表」的
> 待办已作废。证据见 `docs/legacy-control-files.md` §4。
