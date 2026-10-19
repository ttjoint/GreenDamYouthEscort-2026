# 原引擎到底做不做句法分析？——完整的调用链证据

**结论：引擎的 DLL 里有句类分析的代码，但产品里没有任何一条路径走到它。**

准确地说：

- `SentenceObj.dll` 里**有**完整的 HNC 句类分析实现（`CSentenceObj`、
  `GetSentenceALC`、`MakeObjList`、`MakeFandongWordList`）。
- **没有任何调用点**。`GetSentenceALC` 的地址在整个 DLL 里一次都不出现。
- `Handler.dll`（绿坝真正的过滤模块）只导入 2 个函数：
  `CreateHncTextGroup` / `ReleaseHncTextGroup`。
- 它加载的唯一数据文件是 `\hnclib\FalunWord.lib`。
- 它的分析入口 `CorpGroup` 最终调到 `CFWordLib::MakeALC`，
  这是**词库级**的字符串扫描，不是句类分析。

换句话说：**买来了一整套句法分析引擎，产品只用了它的词表查询部分。**

## 1. 调用链（逐层反汇编）

入口是 `IHncTextGroup` 的 vtable 第 4/5 项：

```
CorpGroup(char*)      @0x2E30
  └─ call 0x4DB0                    GBK -> wchar_t 转码
      └─ CorpGroup(wchar_t*)  @0x2DF0
          └─ lea ecx,[esi-0x124]    取主对象（CFWordLib）
              └─ call 0x28D0        ?MakeALC@CFWordLib@@QAEHPAG0_NPAH@Z
```

`0x2DF0` 的机器码（32 字节）：

```
8b 54 24 04            mov edx,[esp+4]        ; 文本指针
85 d2                  test edx,edx
74 24                  je  ...
8b 44 24 08            mov eax,[esp+8]
85 c0                  test eax,eax
74 1c                  je  ...
56                     push esi
8b 74 24 14            mov esi,[esp+0x14]
56                     push esi
8b 74 24 14            mov esi,[esp+0x14]
56                     push esi
50                     push eax
52                     push edx
81 c1 dc fe ff ff      add ecx,-0x124        ; <<< 关键：this 变成主对象
e8 b8 fa ff ff         call 0x2DF0+0x23+...  ; -> 0x28D0 MakeALC
5e                     pop esi
c2 10 00               ret 0x10
```

`add ecx,-0x124` 就是 `this - 0x124`：子对象指针退回主对象。
`0x28D0` 正是 `CFWordLib::MakeALC`——**词库方法，不是句类方法**。

## 2. `MakeALC` 里没有一处调用句类分析

`0x28D0` 长度约 0x3D0 字节，全部 `call`：

```
0x2902 -> 0x1CB0      内部工具
0x29A7 -> 0x27D0      内部
0x29DB -> 0x2830      内部
0x29FD -> 0x13E0      内部
0x2A08 -> 0x13E0
0x2A13 -> 0x13E0
0x2A1E -> 0x13E0
0x2A7A -> 0x2500      SearchChar（trie 单字符查找）
0x2B56 -> 0x13F0      内部
0x2BA8 -> 0x2830      内部
0x2BDB -> 0x1CB0
0x2C26 -> 0x1CB0
0x2C53 -> 0x1CB0
0x2C7B -> 0x1CB0
```

对照句类分析的地址：

| 函数 | RVA | 在 `MakeALC` 里被调用？ |
|---|---|---|
| `CSentenceObj` 构造 | 0x5190 | ❌ |
| `?Ini@CSentenceObj` | 0x68D0 | ❌ |
| `?GetSentenceALC` | 0x6DC0 | ❌ |
| `?GetALC`（全局） | 0x4E70 | ❌ |
| `?MakeFandongWordList` | 0x5CC0 | ❌ |
| `?CheckVV@CSentenceObj` | 0x5E10 | ❌ |

## 3. 全 DLL 扫描：句类函数的调用点

用两种方式找引用：`call rel32` 和绝对地址（`0x10000000 + RVA`）。

| 函数 | `call` 引用数 | 绝对地址引用数 |
|---|---|---|
| `CSentenceObj::ctor` (0x5190) | **1**（0x3ECF） | 0 |
| `CSentenceObj::Ini` (0x68D0) | **0** | 0 |
| `CSentenceObj::GetSentenceALC` (0x6DC0) | **0** | 0 |
| `CSentenceObj::MakeFandongWordList` (0x5CC0) | **0** | 0 |
| `CSentenceObj::CheckVV` (0x5E10) | **0** | 0 |
| `GetALC` (0x4E70) | **0** | 0 |

唯一的那个 `ctor` 调用在 `0x3ECF`，属于 `0x3E40`——`CFWordLib` 的构造函数，
也就是**把 `CSentenceObj` 当作内嵌成员构造出来，然后从不使用**。

`GetSentenceALC`（`0x10006DC0`）作为 32 位绝对地址在整个文件里
**出现 0 次**，说明它连虚表都没进 —— 只是导出符号。

## 4. `Handler.dll` 只用了 2 个函数

导入表里与 `SentenceObj.dll` 相关的条目（`.rdata` 0x13878 起）：

```
?CreateHncTextGroup@@YAPAVIHncTextGroup@@PAD@Z
?ReleaseHncTextGroup@@YAXPAVIHncTextGroup@@@Z
SentenceObj.dll
```

**就这两个。** 72 个导出里其余 70 个完全没用。

`Handler.dll` 里出现的数据文件路径只有一个（0x151F9）：

```
\hnclib\FalunWord.lib
```

没有任何 `Word.lib` / `SentLib.lib` / `JiBenJL.lib` / `rule.lib` 的字样。
与 `Ini` 的 12 个检查点对照：`Handler.dll` 从来不调 `Ini`，所以缺那 11 个
文件对产品的过滤功能**没有影响**。

`HncEng.exe` 同样只导入这两个函数。

## 5. 所以「原始引擎不做句法分析」这个说法要分层

| 层次 | 有没有 | 产品用不用 |
|---|---|---|
| HNC 句类分析**代码** | ✅ 有，完整在 `SentenceObj.dll` 里 | ❌ 从不调用 |
| HNC 句类分析**数据** | ❌ 12 个 `.lib` 缺 11 个 | 无关 |
| 词库查询（`MakeALC`/`SearchWord`） | ✅ 有 | ✅ **主力** |
| 打分表（`GetWordFanDong`/`GetWordHongHei`） | ✅ 有，4693 条 | ✅ **主力** |

所以我们之前纠结的「12 个 `.lib` 缺失导致没法复刻 HNC」，
其实是个**假问题**：即使把那 12 个文件凑齐，绿坝也不会用它做过滤，
因为 `Handler.dll` 根本没接这条线。

## 6. 这解释了红旗出版社那个「理论 vs 产品」的落差

论文（`_hnc_theory.txt` / `_ccl99.txt` / `_hnc_class.txt`）描述的是
一套完整的语义块分析体系：主辅块、句蜕、块扩、句类 24 类、五元组。

但实际产品 `绿坝-花季护航` 做成的是一个**词表 + 打分**的过滤器。

原因现在很清楚：

1. 句类分析需要 12 个数据文件（词表、句类表、规则库），体积大、维护重。
2. 词表过滤只需要 1 个 `FalunWord.lib`（5.5 MB），够用。
3. 舆情监控的实际需求就是「定位带情绪的段落」，不需要完整的句法树。

**这不是实现不完整，是产品定位的选择。** 也可以说是
「把研究原型压成能装进 10 MB 安装包的成品」时必然发生的裁剪。

## 7. 仍然未知

- `Handler.dll` 拿到 XML 之后怎么判定「拦截 / 放行」。它自己不含
  `FanDong`/`HongHei` 字符串，说明判定在 `HASrv.dll` 或别处，
  或者通过 `IHncTextGroup` 的其他 vtable 项拿结果。**没查。**
- 反动度 / 红黑度数值到拦截动作的映射。**没查。**
- `MakeFandongWordList`（0x5CC0）设计给谁用。它在 DLL 内也没有调用点。

## 复现

```bash
# 反汇编调用链
python - <<'PY'
d = open(r'LH-installed-tree/Windows/SentenceObj.dll','rb').read()
rva = 0x2df0
code = d[rva:rva+0x40]
for i in range(len(code)-5):
    if code[i] == 0xe8:
        rel = int.from_bytes(code[i+1:i+5], 'little', signed=True)
        print('call @0x%x -> 0x%x' % (rva+i, rva+i+5+rel))
PY
```

`tests/test_engine_call_graph.py` 把上面的数字固化成测试。
