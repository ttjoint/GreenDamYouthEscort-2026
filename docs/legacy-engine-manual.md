# 原厂算法完整留档：这套东西怎么用、能做什么、怎么调用

本文是**操作手册**。目标：任何人拿到这份仓库 + 安装树，
能照着把原厂引擎跑起来，或者用导出的数据做等价的纯 Python 模拟。

- 算法结构：`docs/hnc-filter-lib.md`、`docs/hnc-filter-query-chain.md`
- 为什么不完整：`docs/engine-call-graph.md`
- 实际效果：`docs/legacy-filter-efficacy.md`

---

## 1. 数据资产清单

| 文件 | 位置 | 大小 | 说明 |
|---|---|---|---|
| `FalunWord.lib` | `LH-installed-tree/Windows/HNCLIB/` | 5,564,271 | 唯一的词库，两层结构 |
| `SentenceObj.dll` | `LH-installed-tree/Windows/` | 544,768 | 32 位 PE，72 个导出 |
| `Handler.dll` | `LH-installed-tree/Windows/` | 684,032 | 过滤模块，只导入 2 个函数 |
| `data/hnc-falun-filter.json` | 本仓库 | 612,973 | **已导出的打分表 4,693 条** |
| `data/legacy-imported/falun-terms.json` | 本仓库 | 大 | 旧解析，`terms` 142,742 条 |

**只要 `data/` 里那两个 JSON，就能做纯 Python 模拟，不需要 32 位环境。**

---

## 2. 算法结构（完整）

```
FalunWord.lib
│
├── 头部 0x00–0x0C
│     dword0 = 272,920   字符节点（trie）条数
│     dword4 = 54
│     dword8 = 8
│
├── 索引记录区 0x0C–0x53C6CC    171,883 条，每条变长
│     格式：[id u32][义项数 u32][0xFFFFFFFE][UCS2 字符...]
│
└── 明文词条区 0x53C6CC–0x54E76F  36,945 个 UTF-16LE 字符

内存里（CFWordLib 对象）：
     this+0x128  打分表基址      4,693 条 × 16 字节
     this+0x12C  打分表条数
     this+0x140  字符节点表基址  272,920 条 × 16 字节
     this+0x144  字符节点条数
     this+0x134  文本表基址      (2 字节/char)
     this+0x138  文本表字符数    36,943
```

### 2.1 打分表每条 16 字节

| 偏移 | 含义 |
|---|---|
| `+0` | `u32` 文本表字符下标（词条的起始位置）|
| `+4` | `0xCDCDCDxx` **未初始化堆填充，无意义，不要读** |
| `+8` | **反动度** 0–7 |
| `+12` | **红黑度** 0–4 |

**词条长度 = 与下一条的 `off` 差**，不是「遇到终止符」。最后一条延伸到文本表末尾。

```python
def word_at(entries, index, text_len):
    start = entries[index].off
    end = entries[index+1].off if index+1 < len(entries) else text_len
    return text[start:end]
```

这一步踩过坑：第一版按「遇非汉字就停」切，得到粘连乱码
（`恶党书` 应为 `恶党书记`，`我坐在` 应为 `我坐在他的小腿上`）。
正确切法只有 0.9% 的直觉解会是错的。

### 2.2 字符节点每条 16 字节

| 偏移 | 含义 |
|---|---|
| `+0` | `u16` 字符值（实测 `法`=27861、`共`=20826、`今`=22825）|
| `+4` | 子节点表指针 |
| `+8` | 子节点数 |
| `+12` | **打分表下标**，三种取值见下 |

`+12` 的三种取值：

| 值 | 含义 |
|---|---|
| `0xFFFFFFFE` (-2) | 不是词的终点，纯中间节点 |
| `0xFFFFFFFF` (-1) | **是词，但无分数** |
| `0 ≤ n < 4693` | 有分数，`n` 是打分表下标 |

---

## 3. 核心 API

### 3.1 `SearchWord` —— 唯一的查询入口

```c
FCCharNode* SearchWord(CFWordLib* this,  // ecx
                       wchar_t*   word,  // esp+4
                       int        len,   // esp+8
                       int*       out);  // esp+12
```

**RVA 0x2550**，`__thiscall`，普通 `ret`（不是 `ret N`）。

返回值的三种情形：

| `out` | 含义 | 实测例子 |
|---|---|---|
| `>= 0` | 命中打分表，值是下标 | `大纪元` -> 808 -> (6,3) |
| `-1` | trie 里有，无分数 | `法轮功`、`明慧网`、`今天`、`公司` |
| `-2` | trie 里没有 | `610`、`斜教` |

**往返校验**：4,693 条打分表词条逐个查，**4,683 条**返回自身下标（99.8%）。
10 条不一致的是重复词归并（`法轮大法好` 在 idx 1123/1124，都归 1125）。

### 3.2 `GetWordFanDong` / `GetWordHongHei` —— 按下标取分

```c
int GetWordFanDong(CFWordLib* this, int index);  // RVA 0x2D80
int GetWordHongHei(CFWordLib* this, int index);  // RVA 0x2D50
```

两者机器码**逐字节相同，只差 1 字节**（在偏移 28），差别只是取 `+8` 还是 `+0xC`：

```
FANDONG: 8b442404 3b812c010000 7d14 85c0 7c10 8b8928010000 c1e004 8b440108 c20400
HONGHEI: 8b442404 3b812c010000 7d14 85c0 7c10 8b8928010000 c1e004 8b44010c c20400
                                                                        ^^ 这一字节
```

**纯内存查表，函数体内没有任何 `call`。** 不需要任何 `.lib` 之外的文件。

被调用方是 `ret 8`（`c2 04 00`），所以 `ctypes.CFUNCTYPE` 直接调不通，
必须手写 thunk。

---

## 4. 怎么把引擎跑起来（32 位 Python 2.7）

### 4.1 五个坑，全部踩过

| 坑 | 现象 | 解法 |
|---|---|---|
| 中文路径 | `ctypes.CDLL` 报 `IOError` | 先 `chdir` 到纯 ASCII 临时目录，用**裸文件名** |
| 尾部反斜杠 | `CreateHncTextGroup` 返回 NULL | 路径必须写 `"...\\Windows\\"`，带尾反斜杠 |
| `this` 指针 | 调用崩溃或返回垃圾 | 传**主对象** `global = sub - 0x124`，不是 `CreateHncTextGroup` 的返回值 |
| 地址基准 | 调到了错误位置 | 用 `dll._handle + RVA`，不用 `0x10000000 + RVA` |
| `ret N` 函数 | `CFUNCTYPE` panic | 手写 shellcode thunk |

### 4.2 最小可运行骨架

```python
# -*- coding: utf-8 -*-
# D:\Python27\python.exe -u
import ctypes, os, shutil, struct, tempfile

SRC = os.path.join(os.path.dirname(os.getcwd()),
                   "LH-installed-tree", "Windows")

# 1. 拷到纯 ASCII 目录
tmp = tempfile.mkdtemp(prefix="hncfilter_")
shutil.copy(os.path.join(SRC, "SentenceObj.dll"), tmp)
shutil.copytree(os.path.join(SRC, "HNCLIB"), os.path.join(tmp, "HNCLIB"))
os.chdir(tmp)

# 2. 加载
dll = ctypes.CDLL("SentenceObj.dll")

# 3. 建组（路径带尾反斜杠！）
Create = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_char_p)(
    dll._handle + 0x50A0)
sub = Create("HNCLIB\\")          # 成功；不带尾反斜杠返回 0

# 4. this 是主对象
G = sub - 0x124

# 5. 手写 thunk 调 SearchWord
k32 = ctypes.windll.kernel32
alloc = k32.VirtualAlloc
alloc.restype = ctypes.c_void_p
alloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t,
                  ctypes.c_uint32, ctypes.c_uint32]

def search(word):
    wbuf = ctypes.create_unicode_buffer(word)
    out = ctypes.c_int(-1)
    code = (b"\x68" + struct.pack("<I", ctypes.addressof(out))
          + b"\x68" + struct.pack("<I", len(word))
          + b"\x68" + struct.pack("<I", ctypes.addressof(wbuf))
          + b"\xb9" + struct.pack("<I", G)
          + b"\xb8" + struct.pack("<I", dll._handle + 0x2550)
          + b"\xff\xd0"      # call eax
          + b"\xc3")         # ret
    buf = alloc(None, len(code), 0x3000, 0x40)
    ctypes.memmove(buf, code, len(code))
    ctypes.CFUNCTYPE(ctypes.c_void_p)(buf)()
    return out.value

print search(u"大纪元")   # -> 808
print search(u"法轮功")   # -> -1
print search(u"610")      # -> -2
```

完整可运行版本：`tools/dump_falun_filter.py`、`tools/probe_searchword.py`。

---

## 5. 完整过滤算法

```
输入：一段文本 S
输出：[(命中词, 打分表下标, 反动度, 红黑度)]

positions = trie 根的子节点表

i = 0
while i < len(S):
    # 从当前位置沿 trie 下降，记录最后一个 out >= 0 的节点
    node = root
    best = None
    j = i
    while j < len(S) and node 有字符 S[j] 的子节点:
        node = child(node, S[j])
        j += 1
        if node.score_index >= 0:
            best = (i, j, node.score_index)   # 贪心记录
    if best:
        emit(S[best[0]:best[1]], score_at(best[2]))
        i = best[1]
    else:
        i += 1
```

**关键**：只有 `score_index >= 0` 才算命中。
`== -1` 表示「trie 里有但无分数」，不能当命中——
否则普通词会全部误报（实测负样本 10/10 误报）。

### 5.1 这就是全部

没有别的步骤。没有句法分析、没有词义、没有上下文。

---

## 6. 三种调用方式对照

| 方式 | 需要什么 | 速度 | 适合 |
|---|---|---|---|
| **A. 真引擎**（32 位 Py2.7 + DLL） | Windows + `D:\Python27` + 安装树 | 慢（每次跨语言调用）| 验证、取真值 |
| **B. 导出 JSON + Python 模拟** | 只要 `data/*.json` | 快 | 批量评测、生产 |
| **C. 只读 `FalunWord.lib` 二进制** | 只要 lib 文件 | 中 | 不信任导出数据时 |

**推荐 B**：`data/hnc-falun-filter.json` 已经过 4,683/4,693 往返校验，
和真引擎等价。

```python
import json
entries = json.load(open("data/hnc-falun-filter.json"))["entries"]
# entries[i] = {"index": i, "word": "...", "fandong": n, "honghei": m}
```

---

## 7. 能做什么 / 不能做什么

### 能做

| 用途 | 怎么做 |
|---|---|
| 判断文本里有没有**见过的**敏感片段 | `search()` 或 JSON 查表 |
| 给文本打「反动度 / 红黑度」标签 | 取命中的最大分数 |
| 做**精确**的黑名单（低误报） | 只用打分表，不用 trie |
| 当**候选生成器** | trie 层宽松召回，再用别的规则精筛 |

### 不能做

| 做不到 | 为什么 |
|---|---|
| 识别同义改写 | 没有语义层 |
| 识别词形变化 | `'法轮功'` 在表里，`法轮功` 不在 |
| 识别新内容 | 没有词条就不认 |
| 判断褒贬倾向 | 句类分析是死代码（`docs/engine-call-graph.md`）|
| 判断「谁在说」 | 同上 |
| 可靠地区分敏感/正常 | trie 层误报 10/10；打分表层漏检 3/10 |

### 数字（详见 `docs/legacy-filter-efficacy.md`）

| 策略 | 正样本 | 误报 | 真实小说 |
|---|---|---|---|
| trie 全部 | 10/10 | 10/10 | 582.8 次/千字 |
| trie ≥2字 | 10/10 | 10/10 | 123.9 次/千字 |
| **打分表** | **7/10** | **0/10** | **0.003 次/千字** |

---

## 8. 反动度 / 红黑度的语义

**没有公开文档，`attested=False`。** 以下是观察，不是结论：

- 反动度 `0–7`，7 最重。5 条 7 分：`http://www.epochtimes.com`、
  `大纪元新闻网`、`纪元文化`、`明慧网消息`、`明慧消息`。
- 红黑度 `0–4`，分布偏向 4（3,673/4,693），1 分 146 条。
- `法轮功` 的带引号形式只有 `(0, 1)` —— 反动度 0 但红黑度 1。

**`Handler.dll` 自己不含 `FanDong`/`HongHei` 字符串**，说明分数到
「拦截/放行」的映射在别处（可能是 `HASrv.dll`，未查）。
在查清之前，**不要假设「分数高 = 一定拦」**。

---

## 9. 文件清单（本文相关）

```
docs/
  hnc-filter-lib.md            打分表结构、反汇编依据
  hnc-filter-query-chain.md    SearchWord 查询链路、往返校验
  engine-call-graph.md         为什么句类分析是死代码
  legacy-filter-efficacy.md    实测效果（本文的 §7 数字来源）
  replication-status.md        项目整体成熟度

data/
  hnc-falun-filter.json        4,693 条打分表（从引擎导出）
  legacy-imported/falun-terms.json   142,742 条 trie 词条

tools/
  dump_falun_filter.py         导出打分表（Py2.7 32位）
  probe_searchword.py          SearchWord 往返校验（Py2.7 32位）
  compare_filter_tables.py     新旧解析差分
  bench_legacy_filter.py       三种策略效果实测（纯 3.x）

tests/
  test_hnc_filter_lib.py       20 个测试：反汇编字节、导出数据形状
  test_searchword.py           13 个测试：out 三态、往返、通用词陷阱
  test_engine_call_graph.py    10 个测试：句类分析零调用点
```
