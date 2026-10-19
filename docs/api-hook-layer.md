# 原厂的 API hook 层：谁 hook 了什么

`HASrv.dll`（64 KB）一直被当成“拦截引擎”。它确实是引擎，但**它不含策略**。
本文只讲这一层的结构与内容，不讲它挡了什么内容。

```bash
python tools/audit_api_hooks.py --legacy ../LH-installed-tree \
    --json data/legacy-api-hooks.json
```

## 1. `HASrv.dll` 是通用引擎

导出表只有四个函数：

| 导出 | 作用 |
|---|---|
| `SetApiHookList` | 接收一张表，存进全局区 |
| `InstallApiHook` | 遍历该表，逐个打补丁 |
| `UninstallApiHook` | 还原 |
| `LoadClient` | 加载 hook 回调所在模块 |

**证据**：`HASrv.dll` 全文搜索不到任何一个被 hook 的 API 名
（`TextOutA`、`ExtTextOutA`、`RegDeleteValueA` 都不存在）。
一个 64 KB 的模块里如果真内置了拦截策略，这些名字必须出现。
它们不在，说明表是外部传入的。

这条已经固化成测试
`tests/test_api_hooks.py::EngineIsGenericTest::test_engine_source_has_no_hooked_api_names`。

## 2. 表在 `Handler.dll` 里，有两张

`Handler.dll`（684 KB）导出 9 个函数，其中 `HookFuncHandler`
是注入端调用的入口。它按消息号（`0..5`）分派，每个分支末尾都是
`call [ebp+0xc]` —— 转交给调用者给的回调，自己不做判定。

真正装 hook 的流程在 `0x31001e71` 起：

```
LoadLibraryA("HASrv.dll")            ; 失败则退到 LoadLibraryExA
GetProcAddress(..., "InstallApiHook")
GetProcAddress(..., "UninstallApiHook")
GetProcAddress(..., "SetApiHookList")
GetProcAddress(..., "LoadClient")
    ↓
call SetApiHookList(table)
```

传哪张表由一个全局标志决定：

| 标志 `[0x31018fac]` | 表地址 | 条数 |
|---|---|---|
| `0` | `0x31014178` | 6 |
| 非 `0` | `0x31014470` | 15 |

这个标志在 `0x31002290` 被写入，来源是把 `c:\settings.ini` 里
`[Settings] WriteLog` 的值与 `2` 比较后 `setne dl`：

```
cmp eax, 2          ; eax = GetPrivateProfileInt(Settings, WriteLog)
setne dl            ; 等于 2 则 dl=0
mov [0x31018fac], edx
```

所以**这是日志开关，不是语言/版本选择**：`WriteLog=2` 时只装 6 条，
其他值装 15 条。多出来的 9 条是宽字符（`W`）版本；
为何与日志开关挂钩，尚未查清，不要当成已证实的设计意图。

两张表都在 `.data`。非零的那张是 ANSI 表的**超集**（测试
`test_wide_table_contains_ansi_entries` 锁住），差别是多了 `W` 版本
的服务/注册表 API，以及 `OpenProcess`。

## 3. 记录格式：108 字节

`SetApiHookList` 先清空 `0x21c` 个 dword（2160 字节）的全局区，
然后按固定步长复制。`InstallApiHook` 的遍历用
`lea eax,[esi+esi*2]`（×3）接 `lea eax,[eax+eax*8]`（×9），
即 `esi × 27` dword = **108 字节**，与 2160/108 = **20 条**容量吻合。

单条记录：

```
+0x00  16 字节   DLL 名
+0x10  16 字节   API 名
+0x20  16 字节   出厂数据里全零
+0x30  16 字节   出厂数据里全零；InstallApiHook 扫它的 NUL
+0x40   4 字节   参数个数
+0x44  60 字节   全零
```

**只有三个字段在磁盘上有数据。** 这是读这份文件时最容易搞错的地方：
表是**意图声明**，被 patch 的地址在安装时才写入。测试
`test_runtime_address_slot_is_empty_on_disk` 锁住这一点。

## 4. 15 条 hook 的构成

| DLL | API | 参数个数 | 归类 |
|---|---|---|---|
| `gdi32.dll` | `TextOutA` / `TextOutW` | 5 | 文字渲染 |
| `gdi32.dll` | `ExtTextOutA` / `ExtTextOutW` | 8 | 文字渲染 |
| `advapi32.dll` | `RegDeleteValueA/W` | 2 | 自保护 |
| `advapi32.dll` | `RegDeleteKeyA/W` | 2 | 自保护 |
| `advapi32.dll` | `RegSetValueA/W` | 5 | 自保护 |
| `advapi32.dll` | `RegSetValueExA/W` | 6 | 自保护 |
| `advapi32.dll` | `OpenServiceA/W` | 3 | 自保护 |
| `kernel32.dll` | `OpenProcess` | 3 | 自保护 |

**11 : 4。** 自保护条目是内容拦截条目的近三倍。

这不是细节，它改变了怎么理解这套东西：

- **文字渲染只有 4 条**，全是 GDI 文本输出。也就是说这一层的“内容拦截”
  是**在屏幕上拦字**，不是分析语义。它解释不了任何句法判断，
  因为 GDI 这一层拿到的只是要绘制的字符串片段。
- **其余 11 条是防止自己被关掉**：改注册表值（删自启动/改配置）、
  删注册表键（删配置项）、`OpenProcess`（结束进程）、
  `OpenService`（停止服务）。

这和 `XNet2.exe` 0x4055c0 段那段进程名单比对
（`mpsvcc`/`hnceng`/`hh`/`lookpic`/`looklog`）是同一件事的两半：
一半在用户态拦 API，一半在自己进程里查名单。

## 5. 对复刻意味着什么

- 这一层**不需要复刻**也能得到等价的内容判定——它只拦屏幕绘制，
  而我们的策略层在文本层面判定，位置更靠前，信息也更全。
- 但它解释了原厂为什么“看起来挡得住”：浏览器的文字最终都要走
  `TextOut`/`ExtTextOut`，在渲染前把片段换掉，用户就看不到。
  代价是它**只能按字符串片段判断**，看不到上下文，
  这与论文里的句类分析完全是两个层面的东西。
- **自保护那 11 条是另一回事**，它不属于过滤能力，属于对抗卸载。
  复刻时刻意不要照搬。

## 6. 仍未查清

- `LoadClient` 加载的“client”是哪个模块、回调原型是什么；
- `HookFuncHandler` 的 6 个消息号分别对应什么操作
  （目前只确认每个分支都转交 `[ebp+0xc]`）；
- 被 hook 的函数替换成什么（回调在 `LoadClient` 载入的模块里，
  不在 `Handler.dll` 内）。
