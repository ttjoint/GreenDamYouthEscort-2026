# HNC 句类框架逆向

这篇记录 `SentenceObj.dll` 里 HNC 语义引擎的**句类判定层**，以及它为什么
是我们能还原的最后一层。核心结论：**判定逻辑 100% 还原并用模拟器逐条验证
等价；句类码的语义命名无法还原，因为字典文件从未随包分发。**

## 引擎身份

`.rdata` 里有一行 PDB 路径：

```
e:\Vs7\ASC_Set\Release\SentenceObj.pdb
```

**ASC = Advanced Sentence Comprehension**，HNC 的官方产品名。这不是"绿坝
自研"——是采购来的商用语义引擎，绿坝只做了领域词库（`FalunWord.lib`）和
调用封装。

同一段字符串还列出了引擎的完整源文件组成：

| 源文件 | 职责（据文件名） |
|---|---|
| `KDiaoDu-fkJK.cpp` | 句类调度 / 分句框架 |
| `ChunkCnst.cpp` | 块构造 |
| `EgComplexCnst.cpp` | 复合概念 Eg |
| `EgHypo.cpp` / `EgPerc.cpp` | 假设与感知 |
| `ElHypoTest.cpp` | 元素假设检验 |
| `fkPerc.cpp` | 句类感知 |
| `GetSCForm.cpp` | 句类形式获取 |
| `GetPhrase.cpp` / `GetlvList.cpp` / `GetPercLE.cpp` | 短语 / 层级 / 感知 |
| `SortV.cpp` | 动词排序 |
| `UnifyPercList.cpp` | 统一感知表 |
| `MoreVFrameFace.cpp` / `EgFrameFace.cpp` | 多元 / Eg 框架面 |
| `ActiveRule.cpp` / `DefFace.cpp` | 活动规则 / 定义面 |
| `AscSysAPI.cpp` | 联想系统 API |
| `eigen.cpp` / `def.cpp` | 基础 |

## 缺失的数据文件

引擎要加载 14 个数据文件，**`FalunWord.lib` 是唯一随包分发的**：

```
SENTEN.LIB   TYPE.LIB   XU.LIB   rule.lib
JiBenJL.lib + JiBenJL.idx      （基本句类 + 索引）
ElTest.lib   EgPerc.lib   UnifyPerc.lib
DefIndex.lib  Defl0Index.lib  Defl0Data.lib   （概念定义索引/数据）
HelpWord.txt  Number.txt  NumTail.txt  NumPre.txt
Word.lib      UserWord.lib      （通用词库 / 用户词库）
```

所以引擎在包里是**半残运行**：句法机制完整，语料字典缺失。这解释了为什么
绿坝的语义判定看上去只依赖敏感词表——它确实只剩领域词库能加载。

## 句类判定器

函数 `0x10030650`，cdecl，三个参数：

```
arg1  [esp+4]   未使用
arg2  [esp+8]   输入结构体
arg3  [esp+0xC] 输出缓冲（7 字节：ASCII 句类码 + 变体字节）
```

输入结构体布局：

| 偏移 | 类型 | 含义 | 代码依据 |
|---|---|---|---|
| `+0x01` | i8 | role_a | `0x1003065A movsx eax,[ecx+1]` |
| `+0x04` | i32 | participants | `0x10030654` |
| `+0x08` | i32 | spare | `0x10030663 mov edi,[ecx+8]` |
| `+0x0C` | u8 | role_b | `0x1003065F mov bl,[ecx+0xC]` |
| `+0x0F` | u8 | concept（role_b==1 用） | `0x100308DF` |
| `+0x10` | u8 | concept（role_b==2 用） | `0x10030695` |
| `+0x13` | i8 | role_a 副本 | `0x1003068F` / `0x100308DC` |
| `+0x14` | u8 | role2 | `0x10030692 mov bl,[ecx+0x14]` |

入口守卫（`0x10030657`–`0x1003066B`）：

```asm
cmp edx, 3      ; participants > 3 -> 返回 0
jg  fail
cmp bl, 2       ; role_b > 2 -> 返回 0
jle 0x10030672
```

**`role_b` 上限是 2。** 我最初按 `bl==3` 写了整整一个分支，那是读错偏移
导致的臆造，后来被模拟器打回。

### 三段分支

| 分支 | concept 来源 | 关键约束 | 输出示例 |
|---|---|---|---|
| `role_b==0` | — | `role_a==2`；`participants==1` 时按 `spare` 再分 | `!0` / `!32133` / `!233` |
| `role_b==1` | `+0x0F` | 必须 `0x30`；跳表按 `role_a` 分 4 路 | `!31123` / `!113` / `!114` / `!235` |
| `role_b==2` | `+0x10` | 分 `0x30` / `0x40` 两路，各自再判 `role2` | `!31125` / `!31116` / `!31133` |

`role_b==1` 的跳表在 `0x10030AF4`，按 `ecx = role_a - 1` 索引：

| `role_a` | 目标 |
|---|---|
| 1 | `0x1003092B` |
| 2 | `0x100309A3` |
| 3 | `0x100309EF` |
| 4 | `0x100308FF` |

### 句类码

24 条，全部来自 `.data`：

```
!0   !112 !113 !114 !115 !116 !125 !133 !135
!233 !235 !320 !330
!31114 !31115 !31116 !31123 !31125 !31133 !31135
!32113 !32114 !32133 !33123
```

结构（**可确定**）：

```
! 3 1 1 1 6
│ │ │ │ └─ 变体位
│ │ │ └─── 关系位
│ │ └───── 关系位
│ └─────── 角色组
└───────── 句类族   (1 / 2 / 3)
```

`!0` 是 2 字节短码，`!113`/`!233`/`!320`/`!330` 是 4 位，其余 6 位。长度
不统一说明它来自缺失的 `SENTEN.LIB`，这里只按实际字节原样保留。

## 等价性验证

这是本轮最关键的一步。做法不是"读代码觉得对"，而是用 **Unicorn** 把
`SentenceObj.dll` 映射进模拟器，直接调 `0x10030650`，读它写回的输出，
和 Python 复刻逐条比对。

```
974 组输入，0 处不一致
```

验证过程抓出了 **4 个真实错误**，全是凭静态阅读想当然导致的：

| # | 错误 | 后果 |
|---|---|---|
| 1 | 跳表前两项读反（`role_a=1/2` 对调） | 这两个值的输出整体互换 |
| 2 | `role_b==2` 分支漏掉内层对 `[+0x14]` 的再判定 | 整段输出全错 |
| 3 | STATE 分支漏掉 `role2==2` 前置条件 | 非法输入被当成合法 |
| 4 | `role_b==0` 分支漏掉 `spare` 二级判据 | `!0` / `!32133` 无法区分 |

第 2 条尤其能说明问题：那段代码里 `movsx ecx, bl; sub ecx, 2; jne fail` 紧跟在
`role_b==2` 的判断后面，很容易被当成"已经确认是 2 了，这段是冗余"。它不是。

验证脚本 `tools/verify_hnc.py` 与测试 `tests/test_hnc.py::NativeEquivalenceTest`
都可复跑。测试里加了 `assertGreater(checked, 500)`——早期版本因为模拟器
初始化写错，一个用例都没跑起来却"零不一致通过"，这个断言就是为了防止那种
假绿。

## 能做到 / 做不到

**能做到**：

- 句类判定逻辑**逐条等价**（974 组输入零偏差，可复跑）。
- 词性 → 概念层次码映射：基码 `c` 遇上单字 `+3`、首字 `+1`、末字 `+2`、
  中间 `+4`，基码 `0x10/0x20/0x30/0x60/0x70` 五组共用同一规律。
- 词库容器、编码、语料流的完整解码（见 `falunword-format.md`）。

**做不到，且必须写清楚**：

1. **句类码 → 自然语言语义**。`SENTEN.LIB` 从未分发。我们能断言"这是
   `!31116` 句类"，不能断言"HNC 管这种句子叫什么"。码的**结构**能确定，
   **名字**不能——推测名字就是编造术语。
2. **语法层的组合规则**。`MakeObjFromJK` → `MakeObjFromSri` 的调用链、对象池
   （`+0x10C`×64 / `+0x43410`×64 / `+0x5DD14`×512，每项 `0x6A4`）能读出来，
   但真正的组合规则表在缺失的 `rule.lib` / `TYPE.LIB` / `XU.LIB` 里。
3. **逐词判定标志**。`FMean.tmp` 未分发，原引擎的"正常/拦截"位不可还原
   （见 `falunword-format.md`）。

## 对新实现的意义

结论是明确的：**新的语义层应该重建，不该复刻**。

理由不是"复刻太难"，而是**复刻出来的东西没有可比的价值**：

- 句类机制完整但字典缺失，等于一台能运转却不知道自己在算什么数的计算器。
- 真正决定拦截效果的领域词库（`FalunWord.lib`）已经完整解码，这部分是
  硬资产，直接导入。
- `hong_hei` 的语义边界也已实测清楚（见 `semantic-corpus.md`）。

所以 `src/green_dam/hnc.py` 的定位是**参考资料与回归基线**，不是运行时依赖。
`docs/architecture.md` 里 M3 的双后端方案保持不变：HNC 复刻后端与 jieba 后端
并存可 A/B——只是现在知道了，HNC 这一侧的天花板由缺失的字典决定，不由实现
质量决定。

把这句话写进文档，比写"已完全复刻 HNC"有用得多。
