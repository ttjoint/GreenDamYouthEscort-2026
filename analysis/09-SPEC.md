# 原产品接口契约 SPEC（复刻对照用）

> **本文件是复刻项目的工程规范**，内容**全部来自** `00-MASTER.md` §4 的
> C-001…C-020 已证实结论（每项均有可重跑 probe 证据）。
> **没有证据的字段不写**，宁缺毋滥。凡标注「待定」的，是当前未知、禁止臆造。

**证据源**：`probe/p01…p21`（可重跑）；状态说明见 `00b-FACTS.md`。

---

## 1. 架构总览（原产品）

```
XNet2.exe (主控 UI/策略)
  │
  ├─ SG_Control 协议 ──▶ SurfGd.dll (过滤引擎)
  │                        ├─ DBFP_*  网络过滤（LSP 数据路径）
  │                        ├─ 三名单数组（A=URL, B=进程, C=关键词）
  │                        └─ SG_Control 命令分发（55 命令码）
  │
  ├─ LoadLibrary ────▶ dbfilter.dll (LSP: WSPStartup)
  │                        └─ import SurfGd.dll!DBFP_*
  │
  ├─ LoadLibrary ────▶ appface.dll (皮肤, UPX 壳)
  │
  ├─ 配置读写 ────────▶ sys.dat / xconfigs.dat (67 dword 策略)
  │
  └─ 进程/服务 ──────▶ MPSvcC.exe / hnceng.exe / MgTaki 驱动
```

---

## 2. 数据格式

### 2.1 策略数组（sys.dat / xconfigs.dat）

| 项 | 值 |
|---|---|
| 大小 | 268 B = 67 × 4（LE dword） |
| 内容 | 策略项 1..25 的值 + 其余 0 |
| 下标映射 | `下标 = 策略号 − 1` |
| MD5（两者相同） | `b935aad6e250fd44fed3c76e965793ea` |

策略 1..25 名称见 `03-DATA-FORMATS.md` §F-002。注意：
- `sys.dat` = 安装模板（无 PE 静态读）；`xconfigs.dat` = 运行时持久化（XNet2 写）。
- `sysEx.dat`（32 B）是 XNet2 写出的运行时状态，**非**配置。

### 2.2 名单记录（144 B = 0x90）

| 偏移 | 字段 |
|---|---|
| +0x00 | 规则字符串 |
| +0x84 | 有效标志（1=有效） |
| 其余 | 填充/预留（待定） |

规则语法：

| 字符 | 含义 |
|---|---|
| `{` `}` | 组（多组间 **AND**） |
| `[` `]` | 字符类（同类多词 **OR**） |
| `,` | 词分隔符 |

语义：`{词A,词B}[字C,字D]` =（含 A 或 B）且（含 C 或 D）。
匹配：对目标文本逐词 `strstr`（大小写不敏感，先小写化）。

### 2.3 三名单

| 名单 | 基址（SurfGd 内部） | 语义 | 容量（记录） |
|---|---|---|---|
| A | `0x1021bf18` | 出站 URL/协议指纹 | 1500 |
| B | `0x102186d8` | 监控目标进程名 | 100 |
| C | `0x1003db84` | 入站关键词 | 1000 |

单条添加上限：11000（`0x2af8`）。

### 2.4 词库（FalunWord.lib）

六段 `fread` 布局，算术闭合（残差 = 5 = `len("HNCIT")`）：

```
+0              u32 n1 = 272,920     链节点数
+4              n1 × 16 B            链节点数组
+4+n1*16        (固定暂存表)
                u32 n2 = 4,693       词义记录数
                n2 × 16 B            词义记录
                u32 n3 = 36,943      字符池字数
                n3 × 2 B             UTF-16 字符池
末尾 5 B        "HNCIT"
```

链节点 16 B：`code`(u32) / `next`(字符路径) / `children`(分支数) / `sense_id`。

### 2.5 其他格式

| 文件 | 格式 | 证据 |
|---|---|---|
| `.dat` 词库 | 可逆 2:1 位重排混淆（零密钥） | CODEC-RT 往返 5/5 |
| `filtport.dat` | 明文 `FreeGate/8567/tcp` 等 | 33 B ASCII |
| `kwselectinfopp.dll` | 84 B × 8 记录（进程名+GBK显示名） | 672/84=8 |
| `.urf` | AppFace 皮肤包，明文 GIF 内嵌 | 头10B前缀+内嵌GIF89a |
| `appface.dll` | UPX 壳（全树唯一） | UPX!@0x3e0 |

---

## 3. 接口契约

### 3.1 SurfGd.dll 导出（9 个）

| 导出 | RVA | 签名（据反汇编） |
|---|---|---|
| `DBFP_Socket` | `0x5458` | 过滤入口（进程→IP→URL） |
| `DBFP_CloseSocket` | `0x55c5` | 空操作，返回 1 |
| `DBFP_Connect` | `0x55cc` | 基本空操作，返回 1 |
| `DBFP_Accept` | `0x55e0` | 空操作，返回 1 |
| `DBFP_Send` | `0x5919` | 出站过滤（URL 提取） |
| `DBFP_Recv` | `0x6184` | 入站过滤（关键词） |
| `DBFP_SendTo` | `0x6480` | 出站过滤 |
| `DBFP_RecvFrom` | `0x6606` | 入站过滤 |
| `SG_Control` | `0x6ffa` | 控制协议（见 3.3） |

### 3.2 过滤行为契约

| 项 | 值 |
|---|---|
| 总开关 | SurfGd 全局 `0x1022800c == 2` 时全放行 |
| 出站拦截 | 返回 0 + 错误码 `0x2746`（10054 WSAECONNRESET） |
| 入站拦截 | `memcpy` 替换响应为预设内容（非断连） |
| 进程判定 | Toolhelp 快照（CreateToolhelp32Snapshot + Process32First/Next） |
| 大小写 | 名单匹配前先小写化（ASCII A-Z → +0x20） |

过滤层次（出站）：进程 → IP/端口 → 协议指纹 → URL/域名 → 内容 URL 提取。
过滤层次（入站）：进程 → IP/端口 → 二进制检测 → HTTP 端口 → 关键词匹配 → 替换。

HTTP 端口集（入站关键词过滤触发）：`{80, 81, 8080, 8081, 8888, 3128}`。

### 3.3 SG_Control 命令码（XNet2 实际使用的）

| 命令码 | 十进制 | 参数 | 语义 |
|---|---|---|---|
| `0x32` | 50 | (144, buf) | 单条添加记录（上限 11000） |
| `0x50` | 80 | (容量, buf) | 数组下发（与 0x32 配合） |
| `0x78` | 120 | (144000, buf) | 设置名单 C（1000 条） |
| `0x118` | 280 | (216000, buf) | 查询/设置（1500 条） |
| `0x192` | 402 | (268, buf) | 设置名单 A |
| `0x1bb` | 443 | (14400, buf) | 设置名单 B（100 条） |
| `0x262` | 610 | (268, buf) | 接收 67 dword 策略数组 |
| `0x2bc` | 700 | (4, ...) | 设置标志 |
| `0x424` | 1060 | (4, ...) | 设置标志 |
| `0xbc2` | 3010 | (512, buf) | 字符串查询 |
| `0xfa0`/`0xfa1` | 4000/4001 | (4, buf) | 查询 |
| `0x3e7` | 999 | — | （待定） |

> 命令码分发结构：字节表 `@0x7d18` + 跳转表 `@0x7ce0`（区间 0x1ba-0x26c），
> 其余区间见 `probe/p16_sg_control_protocol.txt`。共 55 个实现命令码。

### 3.4 dbfilter.dll 导出（LSP）

| 导出 | RVA |
|---|---|
| `DBFilterInstall` | `0x1bb0` |
| `DBFilterIsinstalled` | `0x1d10` |
| `DBFilterUninstall` | `0x1d50` |
| `WSPStartup` | `0x13b0` |

`dbfilter.dll` import `SurfGd.dll` 的 6 个 `DBFP_*`（Socket/Connect/Send/Recv/SendTo/RecvFrom）。

### 3.5 自启链（RunAfterSetup.exe）

字符串块 `0x1a100-0x1a5c0`（明文），关键项：

- LSP 安装：`LoadLibraryA("\DBFilter.dll")` + `GetProcAddress("DBFilterInstall")`，传 `"\SurfGd.dll"` 作参数
- 自启：`...\CurrentVersion\Run` ← `xnet2.exe`
- 服务：`MPSvcC.exe -installjh`、`hnceng.exe -install`
- COM：`regsvr32 /s MPSvcPS.dll`、`regsvr32 /s hncengps.dll`
- 驱动：`\goldlock.exe -i`
- 防火墙：`netsh firewall add allowedprogram`

---

## 4. 复刻对照表（原产品 → 复刻项目）

| 原产品 | 复刻项目对应（GDE M1-M9） | 状态 |
|---|---|---|
| `sys.dat` 67 dword | M8 策略引擎配置 | 格式已闭合 |
| 三名单 144B 记录 | M1 数据 + M6 应用控制 | 结构/语法已闭合 |
| `FalunWord.lib` 词库 | M1 数据加载 | 布局闭合 |
| `dbfilter.dll` LSP | M2 网络拦截（改代理方案） | 接口闭合 |
| `Surfgd.dll` DBFP_* | M2 过滤逻辑 | 行为闭合 |
| `SentenceObj.dll` HNC | M3 文本（自研后端） | 词条链闭合，句法链未接线 |
| `kwimage.dll` OpenCV | M4 图像 | 待定 |
| `IPGate.dll` GDI hook | M5 屏幕文字 | 机理闭合 |
| `appface.dll` 皮肤 | M9 UI | .urf 明文容器已证 |

---

## 5. 待定项（禁止臆造）

| 项 | 现状 |
|---|---|
| 144B 记录 +0x84 之后的字段 | 未知 |
| SG_Control 其余命令码（55 个中约 40 个） | 未追语义 |
| `.urf` 元数据区（头→首个GIF）结构 | 未解 |
| `dbServ.dll` 与 `dbfilter.dll` 的调用优先级 | 主链=dbfilter，dbServ 用途待定 |
| 名单 B 与 kwselectinfopp.dll 的运行时关系 | 推断存在，未证实 |
| 图像过滤（kwimage.dll OpenCV）的具体调用链 | 未追 |
