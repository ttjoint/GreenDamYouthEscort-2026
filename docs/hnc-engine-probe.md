# 原始 HNC 引擎（SentenceObj.dll）实测报告

> 本文件记录 **实际运行** 的结果，不是逆向推断。
>
> 复现：`D:\Python27\python.exe tools\call_hnc_engine.py`
> （必须 32 位 Python；DLL 是 PE32 x86）

---

## 0. 结论：分析那条路被数据文件堵死，**但过滤那条路完全通畅**

**一句话**：`SentenceObj.dll` 能加载、能建组、能调函数。
**句类分析**需要 14 个裸 `.lib`，包里一个都没有，走不通；
**词条过滤**只需要 `FalunWord.lib`，包里就有，**实测 4,693 条全部有效**。

> ⚠️ **重要修正**：本文早前把「引擎能调但给不出结果」当成整体结论，
> 那是错的。过滤走的是 `CFWordLib` 这条路，不经过 `CSentenceObj::Ini`，
> 完全不受那些缺失文件影响。详见 **`docs/hnc-filter-lib.md`**。

### 0.1 引擎的 12 个启动检查点（反汇编确认）

`CSentenceObj::Ini` 走一条串行链，任何一步失败就打印对应的
`Xxx Ini Err!` 并返回 1。全部检查点及所需文件：

| 顺序 | 错误字符串（DLL 内偏移） | 所需文件 | 分发包含有 |
|---|---|---|---|
| — | `Ini Error!` @0x45334 | `%s\FalunWord.lib` | ✅ |
| 1 | `WordLib Ini Err!` @0x459d0 | `Word.lib` | ❌ |
| 2 | `SentLib Ini Err!` @0x459bc | `SentLib.lib` | ❌ |
| 3 | `EsseSentCat Ini Err!` @0x459a4 | 句类表 | ❌ |
| 4 | `ActiveRule Ini Err!` @0x4598c | `ActiveMode.lib` | ❌ |
| 5 | `DefInit Ini Err!` @0x45978 | `DefIndex.lib` / `Defl0Index.lib` / `Defl0Data.lib` | ❌ |
| 6 | `UnifyPercList Ini Err!` @0x45960 | `UnifyPerc.lib` | ❌ |
| 7 | `UserDefine_hNotV Ini Err!` @0x45944 | （用户定义） | ❌ |
| 8 | `EgPerc Ini Err!` @0x45930 | `EgPerc.lib` | ❌ |
| 9 | `MoreVFrame Ini Err!` @0x45918 | （多动词框架） | ❌ |
| 10 | `Asc Disp Ini Err!` @0x45904 | （ASCII 处理表） | ❌ |
| — | `SentStructure Ini Err!` @0x453d0 | 句结构表 | ❌ |
| — | `JiBenJL.lib` @0x45c64 | 基本句类 | ❌ |
| — | `rule.lib` @0x45d00 | 规则 | ❌ |
| — | `AttribE.lib` @0x7fb60 | E 块属性 | ❌ |

**只有 `FalunWord.lib` 一个文件在包里。** 这正是它能"建组成功"
却"无法分析"的原因——建组只用到 `FalunWord.lib`，分析要用其余 12 个。

### 0.2 实测复现

```
Ini('D:\_tmp2\HNCLIB'  ) -> 1     WordLib Ini Err!
Ini('D:\_tmp2\HNCLIB\') -> 1     WordLib Ini Err!
```

第一条就挂。`Word.lib` 的路径拼接代码在 `0xff90`：

```asm
0xffa3: push 0x10045778       ; "Word.lib"
0xffa8: push path
0xffad: push 0x100458e4       ; "%s%s"     ← 无分隔符！
0xffb4: call sprintf
0xffc2: mov ecx,0x100B3A08    ; 全局 wordlib 对象
0xffc7: call 0x7c00           ; SMakeWordSetFromFile
```

`%s%s` 意味着 `Ini` 的路径参数**必须自带尾反斜杠**，与
`CreateHncTextGroup` 同一个坑（但那个是 `%s\FalunWord.lib`，自带分隔符）。

### 0.3 绕过 `Ini` 的尝试与结果

`GetSentenceALC` 需要 `CSentenceObj->member0` 指向一个
`0x4fc280` 字节的大对象（`Ini` 里 `operator new` + `0x6230` 构造）。
我手工 `VirtualAlloc` + 调 `0x6230` 构造，把指针塞进 `member0`：

- ✅ 不再崩（`writing 0x24` 消失）
- ❌ 一律返回 **7**

原因在 `MakeObjList`（`0x69a0`）的收尾：

```asm
0x6b38: mov eax,[ebp+0xc]   ; 切词结果1
0x6b3f: mov eax,[ebp+8]     ; 切词结果2
0x6b47: 两个都为 0 → return 1（失败）
```

→ `GetSentenceALC` 的 `0x6e27` 收到非零 → `jne 0x6f30` → `return 7`。

**手工建的空对象里没有词表，切词必然失败。** 这不是调用方式问题，
是数据缺失问题——绕不过去。

### 0.4 长度上限（意外收获）

`GetSentenceALC` 开头就限长：

```asm
0x6dc8: sub esi,edi
0x6dca: sar esi,1          ; 字节差 / 2 = 字符数
0x6dcc: cmp esi, 0x23      ; ★ 35
0x6dd2: jl  继续
0x6dd4: mov eax,7; ret     ; 超过 35 字 → 直接返回 7
```

`MakeObjList` 里还有 `cmp edi, 0x1fd`（509）的长度钳制。
**这两条说明原始引擎本来就是按句（≤35 字）设计的**，喂整段会被截断——
这对我们复刻项目是个有用的旁证：分句粒度是对的。

### 0.5 修正记录

- **第一次修正**（提交 `3fad90b`）：推翻"缺文件无法初始化"，
  真实原因是 `CreateHncTextGroup` 路径要带尾反斜杠。那条也没错，
  但只适用于建组。
- **第二次修正**（提交 `0496db1`）：`MakeALC` 的 `ret=7` 不是错误，
  是"处理完一个 token"。`GetALC` 的 7 才是"全局未初始化"。
- **第三次修正**（提交 `c413fe2`）：`CSentenceObj::Ini` 需要的是 `Word.lib`，
  **不是** `FalunWord.lib`——两个文件属于不同层，建组用前者、分析用后者。
- **❹第四次修正（最关键）**：本文全篇把「引擎给不出结果」当结论，
  但**过滤功能一直在正常工作**。`GetWordFanDong`（@0x2d80）和
  `GetWordHongHei`（@0x2d50）是纯内存查表，只依赖 `FalunWord.lib`。
  实测导出 4,693 条打分记录，并逐条与函数返回值交叉校验通过。

## 1. 引擎怎么工作（原始程序的实际流程）

`REVERSE-ENGINEERING/08-HNC语义引擎剖析.md` 已还原调用链，本次实测补充：

```
Handler.dll（注入 48 个进程）
  └─ CreateHncTextGroup("\hnclib\")        ← 注意：传的是**目录**
       └─ 内部拼路径：
            %s\FalunWord.lib    ← 词条表（5.5MB，有）
            %s\FMean.lib        ← 词义表（缺失）
            %s\FMean.tmp        ← 词义表中间产物（缺失）
            %s\FWL.tmp          ← 词表临时文件（缺失）
            %s\FWL.txt          ← 词表文本（缺失）
            另外还会读 UserWord.lib / HelpWord.txt / Number.txt /
            NumTail.txt / NumPre.txt / Word.lib
       └─ 任一必需文件缺失 → 打印 "Ini Error!" → 返回 NULL
```

### 1.1 `Ini Error!` 的确切位置

实测：`CreateHncTextGroup` 返回 `NULL`，并且 DLL **自己往 stdout 打印
了 `Ini Error!`**（不是崩溃，是正常的错误分支）。

这说明：**`FalunWord.lib` 只是词条索引，语义分析还需要词义表 `FMean.*`。**

### 1.2 调用链的每一步（原始程序的语义过滤）

从 `SentenceObj.dll` 导出的 72 个函数名 + 字符串可以确认这条链：

| 步骤 | API | HNC 术语 |
|---|---|---|
| 1 加载词库 | `CFWordLib::LoadLib(char*)` | 词库 |
| 2 建索引 | `BuildWordIndexFromFile` | 词库索引 |
| 3 分词 | `CutWordMax(wchar*, int, CFWordPosRec*, int)` | 最大匹配分词 |
| 4 构造逻辑链 | `MakeALC(wchar*, wchar*, bool, int*)` | **ALC** 抽象逻辑链 |
| 5 动动搭配检查 | `CheckVV(wchar*, wchar*, char*)` | v 概念搭配 |
| 6 整句语义 | `GetSentenceALC(...)` | 句类 |
| 7 **反动词判定** | `GetWordFanDong(int)` | 反动等级 ★ |
| 8 **红黑判定** | `GetWordHongHei(int)` | 红黑倾向 ★ |
| 9 结构生成 | `MakeObjList` / `MakeObjFromJK` / `MakeSentSC` | 语义块/句类 |

第 7、8 步是**真正的过滤决策点**——不是"匹配到词就拦"，而是给每个
词/对象一个数值等级，再由上层策略决定。

---

## 2. 实测：能不能调用

### 2.1 可以加载

```
[OK] LoadLibrary 成功，基址 0x10000000
```

技术障碍与解法：

1. **必须 32 位 Python**。DLL 是 PE32 x86。本机 `D:\Python27\python.exe`（2.7-32）可用，64 位 Python 直接 `LoadLibrary` 失败。
2. **中文路径**。`LoadLibraryA` 无法处理非 ANSI 路径，`GetShortPathNameW` 在该卷上也返回 FAIL。解法：先 `os.chdir()` 到目录，再用裸文件名加载。
3. **`__thiscall`**。`CFWordLib`/`CSentenceObj` 的成员函数 `this` 走 ECX，`ctypes` 无法表达，需生成机器码 thunk（脚本里有实现）。

### 2.2 初始化成功（关键：路径要带尾反斜杠）

```
"D:\...\_lh_rw\HNCLIB"    -> None        Ini Error!
"D:\...\_lh_rw\HNCLIB\\"  -> 0x3207144   ← 成功
"HNCLIB"                     -> None        Ini Error!
```

反汇编确认原因（`LoadLib` @ 0x22d0）：

```asm
mov  eax, [0x10045248]     ; "FalunWord.lib"
...
push 0x10045244            ; "rb"
push ecx                   ; sprintf 出来的路径
call fopen_ish
test eax, eax
jz   ->  return 2          ; 打开失败
...
return 1                   ; 成功
```

上层（`sub_10004FA0` @ 0x4fa0）：

```asm
call LoadLib
cmp  eax, 1
jne  0x5051                ; → 打印 "Ini Error!"
```

拼路径用的是 `sprintf(buf, "%s\FalunWord.lib", arg)`，所以 `arg`
必须是「目录 + 尾 `\`」。传裸目录会拼成 `HNCLIBFalunWord.lib`，
自然打不开。

### 2.3 初始化后并未生成任何新文件

```
新建文件: []
现有: ['HNCLIB\FalunWord.lib']
```

这说明 `FMean.lib` / `FMean.tmp` / `FWL.tmp` / `FWL.txt` **都不是
必需前置文件**，只是运行期可选的缓存/中间产物。

### 2.4 已实测可调用的接口

拿到 `IHncTextGroup*` 后，其 vtable 前 11 项全部是 `CFWordLib` 的分析函数：

| vtable | 地址 | 符号 |
|---|---|---|
| [0] | 0x10002DB0 | `SetTextAbstract(int,int,int)` |
| [1] | 0x100078E0 | `SetSentBreak(int, wchar_t*, ...)` |
| [2] | 0x10006F70 | `AddSentBreak(wchar_t*)` |
| [3] | 0x10002DC0 | `SetSpecificCount(int,int,int)` |
| [4] | 0x10002E30 | `CorpGroup(char*, char*, bool, int&, int, int)` |
| [5] | 0x10002DF0 | `CorpGroup(wchar_t*, wchar_t*, bool, int&, int, int)` |
| [6] | 0x10002EC0 | `GetRedWordCount()` |
| [7] | 0x10002ED0 | `GetNigWordCount()` |
| [8] | 0x10002EE0 | `GetSenseWordCount()` |
| [9] | 0x10002EF0 | `GetDebugInfo()` |
| [10] | 0x10002F00 | `GetWordManagerInterface()` |

实测调用成功：

```
GetNigWordCount      = 0
GetSenseWordCount    = 0
```

`CorpGroup`（文本分析主入口）目前仍崩在读取 `0x100EB000`，说明除了
建组还需要额外的内部初始化步骤（尚未定位）。`GetRedWordCount` 崩在
读 `0x00000001`，推断 vtable 该项在未加载词库时为未初始化指针。

---

## 3. ★ 首次拿到的原始引擎输出格式

从 `.rdata` 提取的字符串显示，引擎生成的是 **XML**，标签体系如下：

```xml
<原始语义分析结果>
  <原始词频表>...</原始词频表>
  <~句子 value=%s>
    <主句 value="%s"> ... </主句>
    <复合子句 value="%s"> ... </复合子句>
      <块扩 value="%s"> ... </块扩>
      <句蜕 value="%s"> ... </句蜕>
        <E要素句蜕 value="%s"> ... </E要素句蜕>
        <JK1要素句蜕 value="%s"> ... </JK1要素句蜕>
        <JK2要素句蜕 value="%s"> ... </JK2要素句蜕>
        <JK3要素句蜕 value="%s"> ... </JK3要素句蜕>
        <包装句蜕 value="%s"> ... </包装句蜕>
      <~块素 value="%s"--
                  %s(%d,
                     对立统一性:%d,
                     褒贬:%d,
                     下级E的褒贬:%d,
                     相对立场:%d%s)>
      <~块素 value=虚对象--%s>
    <作者立场原点>%d(%s)</作者立场原点>
    <对象语义信息(直接命中)> ... </对象语义信息(直接命中)>
    <对象语义信息(间接命中)> ... </对象语义信息(间接命中)>
  </~句子>
</原始语义分析结果>
```

### 3.1 这纠正了我们此前的哪些假设

| 此前的假设 | 实测结果 |
|---|---|
| 引擎输出只有句类码和块角色 | **它是一个 7 维打分体系**：对立统一性、褒贬、下级E的褒贬、相对立场、立场原点、直接命中/间接命中 |
| "红黑"就是黑白名单 | 有 `团块绝对排除` / `团块相对排除` 两级，说明决策是**分级**的 |
| 句蜕只是理论概念 | 引擎实际生成了 5 类句蜕（E/JK1/JK2/JK3要素句蜕 + 包装句蜕） |
| 每句一个"作者立场" | 有 `确认原点` / `默认原点` 两种取值路径 |

**`直接命中` / `间接命中` 的区分是此前完全没意识到的**：引擎不是
只做字面匹配，它把"通过语义对象推断出的命中"和"字面命中"分开记录。
这正是 HNC 结构分析相对于纯词表的价值所在。

---

## 4. ★ 首次拿到的源文件路径（21 个）

DLL 的 `.rdata` 泄漏了编译期源文件路径，每个名字直接对应一个 HNC 模块：

```
.\src\system\def.cpp              常量定义
.\src\system\eigen.cpp            特征/本原
.\src\user\ActiveRule.cpp         活动规则（= 论文 11.2 的"运用规则的规则"）
.\src\user\AscSysAPI.cpp          API 层
.\src\user\ChunkCnst.cpp          语义块构造（Chunk = 块，Cnst = construct）
.\src\user\DefFace.cpp            句类框架面（Face = 句类框架）
.\src\user\EgComplexCnst.cpp      复杂复合构造（Eg = 一般/复合）
.\src\user\EgFrameFace.cpp        框架面
.\src\user\EgHypo.cpp             假定/假设
.\src\user\EgPerc.cpp             感知（Perc = perception）
.\src\user\ElHypoTest.cpp         要素假定检验
.\src\user\GetPercLE.cpp          取感知 LE
.\src\user\GetPhrase.cpp          短语获取
.\src\user\GetSCForm.cpp          语义块形式（SC = Semantic Chunk）
.\src\user\GetlvList.cpp          ★ lv 列表（对应论文 11.1 的 l 准则）
.\src\user\KDiaoDu-fkJK.cpp       ★ 句类调度 + fK/JK
.\src\user\MoreVFrameFace.cpp     多 v 框架面（动词语义框架）
.\src\user\SortV.cpp              v 排序
.\src\user\TestSC.cpp             语义块测试
.\src\user\UnifyPercList.cpp      感知列表归一
.\src\user\fkPerc.cpp             fK 感知 ★
```

### 4.1 这些名字证明了什么

- `GetlvList.cpp` 存在 → **论文 11.1 的 lv 准则确实是引擎的实现基础**，
  我们复刻 `l_concepts.py` 的方向是对的。
- `KDiaoDu-fkJK.cpp` → 该文件同时管 **句类调度**（DiaoDu）和 **fK/JK**，
  印证论题 34 的"fK/JK 是函数层"划分。
- `ActiveRule.cpp` → 印证论文 11.2「切分策略 = 运用规则的规则」，
  即规则本身是**运行时激活**的，不是硬编码顺序。
- `EgPerc.cpp` / `UnifyPercList.cpp` / `GetPercLE.cpp` → **Perc（感知）**
  是一等公民，与晋耀红文档里"感知"9 模块的说法一致，且引擎里有专门的
  归一化步骤。
- `MoreVFrameFace.cpp` / `SortV.cpp` → v 概念的**框架面**和**排序**独立成模块，
  说明多动词处理（连动/兼语）在引擎里是专门机制，不是顺手做的。

---

## 5. 面对本项目当前问题的对照

本项目 `src/green_dam/chunker.py` 复刻到第 3、4 步（分块 + 相似度）。
对照原始引擎：

| 原始引擎有 | 本项目有 | 差距性质 |
|---|---|---|
| `FalunWord.lib`（词条索引） | `falun-terms.json`（10,182 条） | **等价**，字面层已复刻 |
| `FMean.lib`（词义表） | 无 | **数据缺失**，不可补 |
| `CutWordMax` 分词 | 无（chunker 直接扫字符串） | 缺分词层 |
| `MakeALC` | `chunker.py` 部分等价 | 部分 |
| `GetlvList`（lv 准则） | `l_concepts.py` | **已按文献复刻** |
| `kdiaoDu-fkJK`（句类调度） | `hnc.py` 族位 1/2/3 | 部分 |
| `GetWordFanDong`/`GetWordHongHei` | `semantic.py` 的 level 0..7 | **等价**，但领域不同 |
| 7 维打分（褒贬/立场/对立统一性） | 无 | **概念层未复刻** |
| 直接命中 / 间接命中 区分 | 无 | **未复刻** |
| 团块绝对排除 / 相对排除 | 无 | 未复刻 |

### 5.1 对"真实语料 66.7% 退化"的解释

上一轮实测发现本项目分块器在真实小说上 66.7% 分句退化成单块，
归因是动词表只覆盖 2.47%。

**对照原始引擎，这个诊断需要修正**：原始引擎根本不用"动词表"，
它用 `FalunWord.lib` 里的**词条索引**（每条带义项数）+ `FMean.lib`
这类词义数据。`FalunWord.lib` 本身是索引，语义要靠义项表展开。

所以本项目的退化**不是"词表太小"这个量的问题，而是缺了词义层
这个质的问题**。把动词表从 208 条扩到 3000 条能把单块率压到 4.5%，
但那是用统计手段模拟语义属性，必然混入名词。

---

## 6. 诚实边界

### 已实测确认的

1. 引擎能加载（PE32 x86，32 位 Python）。
2. `CreateHncTextGroup` 能建组（路径须带尾 `\`）。
3. `CSentenceObj` 能构造（无参，只分配 + 置零）。
4. 无参计数接口能读（对象确实分配了 0x101000 字节）。
5. `MakeALC` 能调用，返回码表已反汇编确认。
6. `CSentenceObj::Ini` **可以调**——它会走到第一个检查点并
   打印 `WordLib Ini Err!`。这本身是"调通了"的证据。
7. 绕过 `Ini` 手工构造 `member0` 后，`GetSentenceALC` **不再崩溃**，
   稳定返回 7。
8. 引擎完整的 12 个启动检查点（见 §0.1 表）。
9. 输出格式（XML 标签体系）已完整拿到。
10. 21 个源文件名已拿到。
11. `FalunWord.lib` 结构已解析（171,883 条索引记录 + 36,945 字符文本区）。
12. **两条长度限制**：`GetSentenceALC` 35 字、`MakeObjList` 509 字。

### 仍未做到的

1. **拿不到任何一条分析结果。** 所有路径都退化成返回状态码。
2. **12 个必需数据文件全部缺失**，且不在安装包内（已搜过 76 个条目）。
3. 各维度的数值范围与权重（褒贬 ±? 等）—— 无法观测。
4. `GetWordFanDong` 返回整数如何映射到"拦截/放行"—— 在 `Handler.dll`。

### 这一轮真正学到的东西

**"能调用"和"能用"是两件事。**

前四轮我一直在证明"引擎能不能被调起来"：能加载、能建组、
能调函数、返回码不是错误。这些都是真的，但对项目没有直接价值——
因为**它给不出分析结果**。

给出不了结果的原因不是调用姿势，是**数据**：这套引擎是一台
很精密的机器，但分发包里只带了它的一颗螺丝（`FalunWord.lib`）。

**所以本项目复刻 HNC 时的定位应当摆正**：我们不是在"还原一个能跑的
原始引擎"，而是在"用公开论文重建一套能用的规则"。原始引擎的证据
价值在于**它的输出格式和模块划分**（已拿到），而不在于**它能给我们
标准答案**（给不了）。

### 一个意外收获：长度上限

`GetSentenceALC` 卡 35 字、`MakeObjList` 卡 509 字，两者独立。
这说明原始引擎按"句"设计。本项目在 `chunker.py` 里坚持先分句
再切块，方向是对的——这条现在有反汇编证据支撑了。

## 7. 对下一阶段的建议

1. **不要再扩动词表来模拟语义**。原始引擎的做法证明了正确架构是
   词条 + 词义属性两层。扩词表只是权宜。
2. **把 7 维打分体系记入 `docs/hnc-theory.md`**。这是文献里没写清楚、
   但原始实现明确存在的东西（尤其"直接命中/间接命中"）。
3. **`l_concepts.py` 的方向被证实正确**（引擎有 `GetlvList.cpp`），
   可以放心继续深化。
4. **最高优先级：把 `CorpGroup` 调通。** 方法：反汇编
   `CFWordLib::CorpGroup` (0x10002DF0 / 0x10002E30)，看它读
   `0x100EB000` 前依赖什么初始化；或参照 `HncEng.exe` 的调用序列。
   一旦调通，就能直接拿原始引擎跑本项目那 47M 字语料做对照实验——
   这是验证复刻正确性最硬的证据。
5. 若要继续挖，其余优先级：`HncEng.exe` 反汇编 > `Handler.dll` 策略层
   > `FalunWord.lib` 索引结构（补齐漏掉的 59,505 条记录）。
