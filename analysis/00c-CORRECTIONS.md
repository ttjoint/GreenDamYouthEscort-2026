# 00c — 本轮自我纠错记录（CRITICAL 修正）

> **这份文件的存在，就是为了回答你刚才的质疑：「之前的分析可能有误」。**
> 经过逐条复核，确认：**上轮分析有 3 处实质错误**，已在下方逐一纠正。
> 每一个纠正都有二进制级证据，不是口头认错。

---

## 错误 1（最严重）：K-07 说「CSentenceObj 从未被实例化」—— **错**

### 上轮错误表述

`07-CONFLICTS.md` K-07 裁决栏写：
> 「`CreateHncTextGroup` 只 `new CFWordLib` + `LoadLib`，**全程不出现 `CSentenceObj`**」
> 「`CSentenceObj` 被实例化过吗？静态证据显示**没有**」

### 真相（本次字节级证据）

`??0CFWordLib`（CFWordLib 构造函数）@ `0x10003e40`，在 `0x10003ec9` 处：

```
0x10003ec9  lea   ecx, [esi + 0x10013c]   ; esi = CFWordLib 对象
0x10003ecf  call  0x10005190               ; ??0CSentenceObj  ← 就地构造！
```

**`CFWordLib` 在自身偏移 `+0x10013c` 处内嵌了一个 `CSentenceObj` 子对象**，
每次 `new CFWordLib` 都会自动构造它。

### 修正后的正确结论

| 项 | 修正前（错） | 修正后（对） |
|---|---|---|
| CSentenceObj 是否被实例化 | 否 | **是**（作为 CFWordLib 内嵌成员，每次构造） |
| 句法子系统的状态 | "死代码/未实例化" | **对象在，但句法方法未被调用**（缺 Word.lib/rule.lib） |
| CFWordLib 的成员布局 | 未观察 | `+0x10013c` 处内嵌 CSentenceObj |

**根因**：我上轮只追踪了 `OutputObjListIni` → `new` → `??0CFWordLib` 这一层，
就断言"只构造 CFWordLib"，但**没有继续追 `??0CFWordLib` 内部**。
构造函数内部还有一次 `call ??0CSentenceObj`，我漏了。

---

## 错误 2：K-07 遗留里我"制造"了伪差异（对象大小 0x1004a0 vs 0x101000）

### 上轮表述

> "GDE harness 记 0x101000，本账读到 push 0x1004a0，差 2912 B"

### 真相

这两个是**不同的分配**，不该拿来比对：

- `0x1004a0` — `OutputObjListIni` 里 `new` **CFWordLib** 本体的尺寸
- `0x101000` — GDE 在**运行时实测**的对象大小（可能含 CRT 对齐/cookie）
- 另外还有个 `0x4fc280` — `CSentenceObj::Ini` 里的另一个大对象（GDE 文档明确记录）

我上轮把「反汇编读到的 CFWordLib 尺寸」与「GDE 运行时观测的尺寸」
当成同一个东西硬比对，得出"差 2912"的伪差异。**这是我自己制造的矛盾。**

**纠正**：撤销这条"遗留分歧"。它不影响任何结论，但留在这里提醒：
**不同语义来源的数值不能并排比对。**

---

## 错误 3：CreateHncTextGroup 返回值语义表述不严谨

### 上轮表述

> "返回 `[全局]+0x124`"

### 精确真相

```
0x100050a9  call OutputObjListIni       ; 成功返回 0，失败抛异常/返回非0
0x100050b1  cmp  eax, 1                 ; 注意是跟 1 比
0x100050b4  je   0x50c5                 ; ==1 → xor eax,eax;ret（返回 NULL）
0x100050b6  mov  eax, [0x100b31e8]
0x100050bb  test eax,eax
0x100050bd  je   0x50c5                 ; 空 → NULL
0x100050bf  add  eax, 0x124
0x100050c4  ret                         ; 成功才返回 全局+0x124
```

**成功（OutputObjListIni 返回 0）才返回 `全局+0x124`；失败返回 NULL。**
我上轮省略了"成功才"这个前提，虽然结论方向对，但表述不严谨。

---

## 附带发现（正确且重要的新证据）

`CreateHncTextGroup` 返回的 `+0x124` 子对象，其 vtable（`0x451dc`）就是
**`IHncTextGroup` 接口**：

| slot | 地址 | 函数 |
|---|---|---|
| 0 | `0x10002db0` | SetTextAbstract |
| 1 | `0x100078e0` | SetSentBreak |
| 2 | `0x100078e0` | SetSentBreak |
| 3 | `0x10006f70` | AddSentBreak |
| 4 | `0x10002dc0` | SetSpecificCount |
| 5 | `0x10002e30` | CorpGroup（词条级分析入口） |

而主对象 vtable（`0x4520c`）是另一组（CFWordLib 自身的管理接口）。

**这解释了 GDE harness 为什么说"corpGroup->this-0x124"**：`CorpGroup` 是子对象
vtable 的 slot 5，它内部 `sub ecx, 0x124` 找回主对象指针。

---

## 结论（对你这句质疑的正面回答）

**"之前的分析可能有误"——是的，且已被证实有 3 处错误，其中 1 处是实质性错误（K-07）。**

但同样要指出：**大部分结论经字节级复核仍成立**，包括：
- FalunWord 布局（ARITH 闭合，不可错）
- 句法函数零调用（字节级 E8 扫描复核，3/0/0/0 与 capstone 一致）
- 数据文件名归属（LoadLib→FalunWord.lib，SetSentBreak→Word.lib/rule.lib）
- 版本资源 UTF-8、IOC 清单 48/48 等

**错误的共性**：都发生在「我从 A 推 B 时，没有把 A 的证据链走到底」。
这是一个可改进的纪律缺陷，已写入 00-MASTER §1.4（新增 E5）。

---

## 待继续做的复核（自上轮遗留的 CLAIM 项）

| 优先级 | 事项 | 当前状态 |
|---|---|---|
| P0 | 复核 K-07 全部下游引用（谁用了"CSentenceObj 未实例化"这个说法） | 本轮已改 K-07 |
| P1 | Unicorn 动态验证：CFWordLib 构造后 +0x10013c 处的 CSentenceObj vtable 是否就绪 | 待做 |
| P2 | `.urf` 消费方追到函数级（仍完全冻结） | 待做 |
| P3 | `sys.dat` 下标→策略映射 从 XNet2 读取代码确认 | PROBING |
