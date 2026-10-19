# 复刻成熟度盘点：产品到底用了 HNC 的哪一部分

**先说结论：不能说「原引擎的 HNC 表现已经可以复刻了」，因为原厂自己也只用了 HNC 的一小块。**

| 能力 | 产品的态度 | 我们能否复刻 |
|---|---|---|
| **词条过滤**（找敏感词 + 打分） | ✅ 全部功能都在这 | ✅ 完全能 |
| **HNC 句类分析** | ❌ **从不调用**（死代码） | ⚠️ 需要 12 个 `.lib`，产品不用 |
| **语义网络 / 五元组** | ❌ 从不调用 | ⚠️ 同上 |
| **句蜕 / 块扩 / 团簇排除** | ❌ 从不调用 | ⚠️ 同上 |

**关键转折**：句类分析不是「我们复刻不了」，而是「产品根本没接这条线」。

证据见 `docs/engine-call-graph.md`：

```
Handler.dll（绿坝真正的过滤模块）
  └─ 只导入 2 个函数：CreateHncTextGroup / ReleaseHncTextGroup
      └─ 加载的唯一数据文件：\hnclib\FalunWord.lib
          └─ CorpGroup -> this-0x124 -> CFWordLib::MakeALC
                                        （词库级字符串扫描，非句法分析）
```

`GetSentenceALC` 在整个 `SentenceObj.dll` 里：

- `call` 引用数：**0**
- 绝对地址（`0x10006DC0`）出现次数：**0**

它是导出符号，连虚表都没进。所以之前纠结的「12 个 `.lib` 缺 11 个」
其实是个**假问题**：即使凑齐了，绿坝也不会用它。

## 1. 过滤这块：能复刻，但价值要打折

### 已验证的部分

```
FalunWord.lib
  ├─ trie 272,920 节点        通用词库（今天/公司/工作 都在里面）
  └─ 打分表 4,693 条          每条 (反动度 0-7, 红黑度 0-4)
```

`SearchWord`（0x2550）第 4 个参数 `int&` 返回打分表下标：

- `out >= 0`：命中，值即下标
- `out == -1`：trie 里有，无分数
- `out == -2`：trie 里没有

往返校验 4,683 / 4,693 = **99.8%**。

### 但这份打分表不是通用规则

```
'法轮功'  在表里（idx=16，得分 (0,1)）
 法轮功   不在表里（SearchWord 返回 -1）
[法轮功]  在表里
```

**4,693 条是某份具体文档的原文片段**，不是「把敏感词规范化后登记」的词表。
裸词 `法轮功` 根本没注册。

后果：

1. **无法直接当屏蔽词用** —— 词形不规范。
2. **复刻它意义有限** —— 它记录的是「这篇文章里出现了这些串」。

### 分数语义仍然未知

`反动度 0-7` / `红黑度 0-4` 具体怎么映射到「拦截 / 放行」，没有公开文档。
`Handler.dll` 自己不含 `FanDong`/`HongHei` 字符串，判定在别处（可能是
`HASrv.dll`，未查）。`attested=False`。

## 2. HNC 本体：代码在，路径不在

### 硬事实（反汇编 + 全文件扫描）

| 函数 | RVA | `call` 引用 | 绝对地址引用 |
|---|---|---|---|
| `CSentenceObj::ctor` | 0x5190 | 1（0x3ECF） | 0 |
| `CSentenceObj::Ini` | 0x68D0 | **0** | 0 |
| `CSentenceObj::GetSentenceALC` | 0x6DC0 | **0** | **0** |
| `CSentenceObj::MakeFandongWordList` | 0x5CC0 | **0** | 0 |
| `CSentenceObj::CheckVV` | 0x5E10 | **0** | 0 |
| `GetALC`（全局） | 0x4E70 | **0** | 0 |

唯一的那个 `ctor` 调用在 `0x3ECF`，属于 `0x3E40`——`CFWordLib` 的构造函数。
也就是说**把 `CSentenceObj` 当作内嵌成员构造出来，然后从不使用**。

### `Ini` 的 12 个检查点（以前当成「硬阻断」）

```
CSentenceObj::Ini
  ├─ Word.lib              ❌
  ├─ SentLib.lib           ❌
  ├─ 句类表                ❌
  ├─ ActiveMode.lib        ❌
  ├─ DefIndex/Defl0*.lib   ❌
  ├─ UnifyPerc.lib         ❌
  ├─ EgPerc.lib            ❌
  ├─ 多动词框架            ❌
  ├─ ASCII 表              ❌
  ├─ 句结构表              ❌
  ├─ JiBenJL.lib           ❌
  └─ rule.lib              ❌
```

分发包里只有 `FalunWord.lib`。但**产品从不调 `Ini`**，所以这 11 个缺失
对过滤功能没有任何影响。

### 已经拿到的（但只是骨架）

| 拿到 | 怎么拿的 | 够不够复刻 |
|---|---|---|
| 输出 XML 格式（`<主句>`/`<块扩>`/`<句蜕>`…） | `.rdata` 字符串 | 只是格式 |
| 21 个源文件名 | 调试字符串 | 只是文件名 |
| 72 个导出函数名 | 导出表 | 没有实现 |
| 24 句类码 | 早前逆向 `0x30650-0x30AF0` | 部分 |
| `MakeALC` 返回码 | 反汇编 | 只是控制流 |

## 3. 我们自己重写的那套在哪

项目里的 `chunker.py` / `l_concepts.py` / `similarity.py` 是
**按论文重建的**，不是复刻引擎：

| 模块 | 依据 | 验证状态 |
|---|---|---|
| `chunk_tags.py` | 论文附表（78 个介词） | 数字锚点测试 |
| `l_concepts.py` | 论文 11.1 l 准则 | 数字锚点测试 |
| `chunker.py` | 论文 11.2 切分策略 | 退化率 41.4%（`docs/verb-table.md`） |
| `similarity.py` | [王] 论文 1/3/4/5/6 式 | 替代实现，`degraded=True` |

**这套东西原厂没有。** 从这个角度说，我们比原厂走得远——
原厂只做了词表过滤，我们试图做论文说的语义块分析。

但也要诚实：**我们的 chunker 还没在真实语料上跑通**。
单块退化 41.4%，意味着近一半分句找不出谓语。

## 3.5 产品的过滤是「四层」，不是「一层词表」

前面的分析只说清了一个模块（`Handler.dll` -> `SentenceObj.dll` ->
`FalunWord.lib`）。把安装清单 `_build-manifest.txt` 和 `xstring.s2g`
的安装表对齐后，可以确认产品其实有**四条互相独立的过滤路径**，
任何一条都可以单独拦截：

| 层 | 载体 | 机制 | 我们能复刻吗 |
|---|---|---|---|
| L1 文本词表 | `FalunWord.lib` | `CFWordLib` 字符串扫描 + 打分 | ✅ 已复刻（`data/hnc-falun-filter.json`） |
| L2 网址/文件名规则 | `adwapp`/`adwfil`/`iawfil`/`lgwfil`/`vgamfil.dat` | 前缀/后缀/字面组合展开，9,143 条规则 → 11,187 词条 | ✅ 已复刻（`data/legacy-rule-audit.json`） |
| L3 信任/黑名单网址 | `TrustUrl.dat` + `wfile.dat` | `Surfgd.dll` 逐条比对 | ✅ 已导入（1,157 主机） |
| L4 进程/端口黑名单 | `filtport.dat` + LSP（`dbfilter.dll` → `Surfgd.dll`） | Winsock 劫持，按进程/端口判定 | ✅ 已审计（2 条明文） |

> **注意**：`1.urf`–`7.urf` **不是**过滤层的任何一部分。
> 它们是 AppFace 换肤库的皮肤资源包（内嵌 GIF/BMP/JPEG，无加密），
> 详见 `docs/legacy-control-files.md` §4 与
> `REVERSE-ENGINEERING/04-urf规则库格式分析.md` §7。

关键证据：

- `xstring.s2g`（安装表，128 条 `File=`/`Folder=`）里，`2006data\*.dat`
  共 30+ 个文件，我们只拿到其中 5 个的分类规则和 1 个信任表。
  **`auctfil`/`bnrfil`/`chtfil`/`cultfil`/`entfil`/`imgfil`/`pkmon`/`wrestfil`
  等分类在安装表里存在，但安装树里根本没有**——缺的不只是 HNC 的 `.lib`。
- `_build-manifest.txt` 要求 `1.urf`–`9.urf` 共 9 个，实际只有 7 个。
- `HncEng.exe` 是**独立服务进程**（`HncEngServer.exe`，`LocalService`，
  导入 `SentenceObj.dll` 的 `CreateHncTextGroup`/`ReleaseHncTextGroup`
  和 `hnclib\` 路径），说明 HNC 引擎确实有常驻进程形态；
  但 `Handler.dll` 是**进程内直接链接** `SentenceObj.dll`，不走 IPC 服务。
  两条路都存在，产品默认走进程内那条。

结论修正：

- 之前写「原厂只有词表过滤」**说过头了**。准确说法是：
  **原厂的中文文本判定只有词表；但网络侧还有规则库、信任表、进程/端口表。**
- 一个请求要真正被放行，得同时穿过 L3 和 L4；词表只影响 L1。
  所以「`法轮功` 裸词不在打分表里」不等于产品漏放——
  它可能被 L2 的网址规则或 L3 的 `.urf` 挡住。

### `gn.exe` 的 `.urf` 加载点（本轮新证据，**定性已翻转**）

> **2025 重勘结论：`.urf` 不是过滤规则库，是 AppFace 换肤库的皮肤资源包。**
> 完整证据链见 `REVERSE-ENGINEERING/04-urf规则库格式分析.md` §7，
> 提取脚本 `tools/extract_urf_images.py`，产物 `_urf_images/`（12 张 GIF）。
> 下面保留本轮反汇编记录，因为它是证据链的最后一环。

`gn.exe`（`%WinSysDir%\gn.exe`，来自 `xxxnet2\play\Release\gn.exe`）
里有一个完整的最小加载器，和 `XNet2.exe` 的 `EnsureUrfReady` 同构：

```
0x40b4f0  sub esp, 0x200
0x40b50c  GetWindowsDirectoryA(buf)          ; 栈 buffer 512 字节
0x40b519  call 0x40b580                     ; 读 PARAM 子键, 默认 10, 钳 10..30
0x40b51e  mov eax, [0x451d10]               ; 注册表读出的索引
0x40b523  cmp eax, 1 / jl  -> eax = 1
0x40b528  cmp eax, 8 / jle -> 保留
0x40b52d  else              -> eax = 1
0x40b538  lea edx, [esp+0x110]
0x40b53f  push 0x44d3c0                     ; "%s\\%d.urf"
0x40b545  call 0x41978e                     ; sprintf
0x40b54a  mov eax, [esi+0xc8]               ; AppFace.dll!SkinStart
0x40b56e  call eax                          ; SkinStart(0, path, 1, name, 0, 0)
```

`call [esi+0xc8]` 的实现在 `gn.exe` 构造函数 0x40a8b0 处可以逐字段对账：

```text
0x40a8de  call [0x43e32c]        ; LoadLibraryA
0x40a8f8  push 0x44d338          ; "SkinStart"
0x40a8fe  call edi               ; GetProcAddress
0x40a902  mov  [esi+0xc8], eax   ; ← 这就是「+0xc8 处的 .urf 回调」
0x40a914  push 0x44d32c          ; "SkinStartW" → +0xcc
0x40a930  push 0x44d320          ; "SkinRemove" → +0xd0
0x40a94c  push 0x44d310          ; "SkinWindowSet" → +0xd4
0x40a964  push 0x44d304          ; "SkinThread" → +0xd8
0x40a97c  push 0x44d2f8          ; "BkCreate" → +0xdc
0x40a994  push 0x44d2f0          ; "BkDraw" → +0xe0
0x40a9ac  push 0x44d2e4          ; "BkDelete" → +0xe4
0x40a9be  mov  [esi+0xc0], 1     ; 初始化完成标志
```

也就是说整个 `.urf` 加载链是：

```
LoadLibraryA("AppFace.dll")
  → GetProcAddress("SkinStart")   → [this+0xc8]
  → SkinStart(0, "%WinDir%\\%d.urf", 1,
               "1PPRNFQ5YPKZI0UYV4U5GB5RKHDYNEKJ", 0, 0)
```

`1PPRNFQ5…` 是 AppFace 皮肤的口令参数，**不是解密密钥、不是属性名**。
实际提取验证：7 个 `.urf` 里的 payload 是未加密的
GIF/BMP/JPEG（12 张完整 GIF，含 285×24 的 4 状态工具条按钮贴图条），
此前看到的「高熵、magic `5599259b`、记录标记」全是 AppFace 容器元数据。

差别有两处，说明这是**同一套 MFC 封装库的不同宿主**：

1. `gn.exe` 的索引上界是 **8**，`XNet2.exe` 是 **9**；
2. `gn.exe` 索引来自注册表 `SOFTWARE\KingWay` 的 `PARAM` 值
   （默认 10，钳到 10..30），而 `XNet2.exe` 的 `g_urfIndex` 初始为 1。

`gn.exe` 的 `PARAM` 读函数（0x40b580）还顺手做了三件事：

- `RegOpenKeyExA(HKEY_LOCAL_MACHINE, "SOFTWARE\\KingWay", ...)`
- `RegQueryValueExA(..., "PARAM", ...)` -> 12 字节缓冲
- 值不在 `[0xa, 0x1e]` 就重置为 `0xa`

所以安装后的默认行为是：**装 7 个皮肤方案，只加载第 1 个**
（索引被钳到 1），其余 2–7 是备选皮肤，由 `PARAM` 切换——
不是「规则分类」，更不是「加密规则库」。

### 对「四层过滤」结论的修正

| 层 | 载体 | 机制 | 我们能复刻吗 |
|---|---|---|---|
| L1 文本词表 | `FalunWord.lib` | `CFWordLib` 字符串扫描 + 打分 | ✅ 已复刻（`data/hnc-falun-filter.json`） |
| L2 网址/文件名规则 | `adwapp`/`adwfil`/`iawfil`/`lgwfil`/`vgamfil.dat` | 前缀/后缀/字面组合展开，9,143 条规则 → 11,187 词条 | ✅ 已复刻（`data/legacy-rule-audit.json`） |
| L3 信任/黑名单网址 | `TrustUrl.dat` + `wfile.dat` | `Surfgd.dll` 逐条比对 | ✅ 已导入（1,157 主机） |
| L4 进程/端口黑名单 | `filtport.dat` + LSP（`dbfilter.dll` → `Surfgd.dll`） | Winsock 劫持，按进程/端口判定 | ✅ 已审计（2 条明文） |

`.urf` 从这张表里**移除**了。剩下的未复刻资产是：

- `uFWORD.dat` / `uTUrl.Dat` / `uFUrl.Dat` / `filtpros.Dat` /
  `FiltUrl.Dat` / `server.dat` —— `Surfgd.dll` 声明了但安装包没带，
  应为联网更新下发；
- 安装表里提到、包里缺席的 `auctfil`/`bnrfil`/`chtfil`/`cultfil` 等分类规则。

结论修正：

- 之前写「原厂只有词表过滤」**说过头了**。准确说法是：
  **原厂的中文文本判定只有词表；但网络侧还有规则库、信任表、端口表。**
- 一个请求要真正被放行，得同时穿过 L3 和 L4；词表只影响 L1。
  所以「`法轮功` 裸词不在打分表里」不等于产品漏放——
  它可能被 L2 的网址规则或 L3 的信任表反向放行所覆盖。

## 4. 诚实的差距清单

按「离可用有多远」排序：

1. **`Word.lib`**：没有它切词都做不了。可以从 `FalunWord.lib` 的 trie
   反推一份词表，但**没有词性/词义**，HNC 要的正是这个。
2. **`_predicate_start` 右边界**：`分析问题` 被整体吞进 E，B 丢了。
3. **`v+着` 优先级**：`代表带着` 抢了 `离开`，违反 `[张]` §3。
4. **句类判定表**（24 类怎么判）：论文有定义，具体规则不完整。
5. **辅块 7 类**（Ms/In/Wy/Cn/Re/Pr/Rt）：同上。
6. **句蜕 / 块扩 的识别**：只有输出标签，没有判定逻辑。

## 5. 所以现在该怎么定位

**不要**说「HNC 复刻完成」。准确表述：

- 我们**能复刻原引擎的词条过滤**，这就是产品的全部能力。
  但这份打分表本身不规范，不可直接复用。
- **HNC 句类分析不是「没法复刻」，而是原厂就没用**。代码在 DLL 里，
  但是死代码。
- 我们按论文重建的 chunker 是**超出产品**的努力，但还没跑通。

如果目标是「做得比原厂好」，那么句类分析是唯一方向——
因为原厂那条路（词表 + 打分）已经走到头了，而它漏掉了语义。
`法轮功` 裸词没进打分表这件事，正是纯粹词表法的典型失败。

## 6. 下一步建议

| 优先级 | 做什么 | 理由 |
|---|---|---|
| 高 | 修 `_predicate_start` 右边界 | 修好才能开扩展动词表（收益 −15pp） |
| 高 | 修 `v+着` 优先级（`[张]` §3） | 同上 |
| 中 | 查 `HASrv.dll` 里反动度/红黑度的映射 | 才知道分数怎么变成拦截动作 |
| 中 | 补上 `wfile.dat`（260 字节）与 `filtport.dat` 的规则语义 | L4 结构与 L2 不同 |
| 低 | 继续找那 12 个 `.lib` | 产品不用，找到也只是研究价值 |
| 低 | 反汇编 `CheckVV` / `CheckRelNigRule` | 过滤 API 的补充 |

**不建议**继续在「还原引擎」上投入 —— 已经还原到产品的全部能力了。
