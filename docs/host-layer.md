# 宿主层（`host/`）

`src/green_dam` 是引擎：纯标准库、零 I/O、任何平台可测。`host/` 是它缺失的
另一半——所有 `socket`、`subprocess`、`ctypes`、`pywin32` 都在这里。

本文记录**为什么这么切**，以及每个模块的边界。实现细节在代码里；这里只写
那些"看代码看不出来"的决定。

---

## 1. 为什么分成两个包

`tests/test_contracts.py::NoIOTest` 禁止 `src/` 出现 `socket`/`subprocess`/
`ctypes` 等导入。这不是洁癖：它让引擎在**没有 Windows 的机器上也能跑完整
测试套件**，也就让"判定逻辑对不对"这个问题和"Windows 集成对不对"这个问题
彻底分开。

代价是宿主必须存在，否则产品不能运行。宿主的存在方式有两条硬规则：

1. **箭头单向**：`host` 可以 import `green_dam`，反过来不行。
   `host/tests/test_contracts.py::PlatformSplitTest` 从宿主侧检查这件事，
   因为引擎侧的契约测试只能看到 `src/`，看不到"有没有人反向 import"。
2. **引擎不为宿主改变**。宿主适配 `transport.plan`、`imagepath.ImageBackend`、
   `Filter` 这些既有接口；如果宿主需要新东西，先问"这属于判定还是属于动作"。

---

## 2. 模块边界

| 模块 | 职责 | 不属于它的 |
|---|---|---|
| `capabilities.py` | 探测可选依赖，报告 present/absent/broken/wrong_os | 不决定缺失时怎么办 |
| `paths.py` | 路径布局；拒绝写系统目录（B7） | 不创建目录 |
| `engine.py` | **唯一** import 引擎的地方；装配 `Filter` | 不含任何判定逻辑 |
| `protection.py` | 三类终止保护（critical / security / self） | 不枚举进程、不终止 |
| `processes.py` | M6：枚举、问引擎、按 `apply` 决定是否终止 | 不判断"该不该杀" |
| `firewall.py` | 规则构造为 argv、按组可逆卸载 | 不执行 `netsh` |
| `proxy/message.py` | HTTP/1.x 解析，敌意输入返回原因而非抛异常 | 不判定、不转发 |
| `proxy/policy.py` | 问引擎、组 Outcome、生成阻断页 | 不碰 socket |
| `proxy/inbound.py` | 入站响应体关键词检查 + 响应替换 | 不读请求、不转发 |
| `proxy/server.py` | 监听、读字节、转发或拒绝 | 不做判定 |
| `packaging.py` | 部署清单：哪些文件、去哪、为什么 | 不拷贝任何东西 |
| `browsers.py` | 多浏览器检测 + 策略位置（Chromium / Firefox） | 不写入 |
| `policy/` | 策略变更表达为 `Change`，可逆；`apply` 才写 | 不做判定 |
| `imaging/` | Marqo 384 ONNX 后端 + OOD 拒绝 | 不碰文件、不枚举进程 |
| `extension/` | 扩展的规则（Python 定义）+ manifest + JS 资产 | JS 不做判定，判定在 Python |
| `ui/` | 控制台：数据层（纯函数）+ 回环服务 + 静态页 | 不写任何文件、不执行任何动作 |

`engine.py` 是唯一的引擎入口。二十个模块各自 `import green_dam` 会让一次部署
错误产生二十份 traceback，也会让 `Wiring`（解析出的路径报告）失去意义。

---

## 3. 三个刻意的设计选择

### 3.1 危险动作默认关闭

`sweep(apply=False)` 是默认。会终止进程的路径全部在 `apply=True` 之后。
原因不是谨慎，是历史：被替代的那个产品会在你没预期的时候动手。一个执行动作
默认关闭的代价是三行代码；默认打开的代价是测试、首次启动、误配置时杀掉用户的
工作。

### 3.2 保护名单先于任何动作，且拒绝要报告

`host/protection.py` 三类拒绝：

| 类别 | 例子 | 为什么 |
|---|---|---|
| `critical` | `csrss` `lsass` `winlogon` | 终止会导致蓝屏或会话结束 |
| `security` | `MsMpEng` `360tray` `ekrn` | **B8**：杀安全软件就是恶意行为 |
| `self` | 本产品进程树 | 自我终止从来不是意图 |

匹配是**故意过宽**的（子串、小写、去 `.exe`）。假阳性 = "我们拒绝终止"，
可恢复且会记录；假阴性 = "我们杀了杀毒软件"，不可恢复。这个项目里每个
保护判断都朝安全方向失败，这一条最明显。

拒绝不是静默跳过：家长把 `csrss` 加进名单 B 是配置错误，`Refusal` 让界面
能说出来，而不是"阻断莫名不生效"。

### 3.3 第三方 import 必须在函数内部

`host/tests/test_contracts.py::DependencyTest` 检查模块级没有第三方 import。
这样缺一个依赖时，`import host.processes` 仍然成功，`capabilities.probe()`
能报出"psutil 缺失"，而不是在栈顶抛 `ImportError`。失败模式从"整个程序起不来"
变成"一个功能不可用，并说清为什么"。

---

## 4. 状态

| 里程碑 | 内容 | 状态 |
|---|---|---|
| H0 | 骨架：能力探测、路径、引擎装配 | **DONE** |
| H1 | M6 进程枚举与终止（默认 dry-run） | **DONE** |
| H2a | 防火墙规则构造与可逆卸载（纯数据，不执行） | **DONE** |
| H2b | 明文 HTTP 代理（阶段 2，含阻断页与解析器） | **DONE** |
| H2b' | 入站响应体关键词检查 + 响应替换（p19） | **DONE** |
| H2c | HTTPS MITM（阶段 3） | **不做**：改为浏览器扩展（见 §2.6） |
| H4a | 浏览器扩展：DOM 级过滤 + 多浏览器策略安装 | **DONE** |
| H5 | M4 ONNX 图像分类（Marqo 384） | **DONE**，见 `image-backend.md` 附录 |
| H5b | 本地图像评分服务（扩展 POST `/score` 的对接端） | **DONE**，见 §2.11 |
| H6 | M5 UIA + OCR | **不做**：全屏 OCR 等于录屏，见 §2.7 |
| H3 | M7 服务宿主（ACL、恢复动作、IPC） | 未开始，需真机 |
| H4 | M9 控制台（pywebview + HTML + 托盘） | 未开始，依赖 UI 框架选型 |

剩下两项（H3/H4）都卡在真机或 UI 框架选型上。原先的四个未决决策已全部
落定：模型用 Marqo 384（Apache-2.0），MITM 不做（改浏览器扩展），
M5 不做（全屏 OCR 等于录屏），打包形态仍待定但已不影响任何里程碑。

### 2.1 代理层为什么再分四层

`host/proxy/` 里 `message` / `policy` / `inbound` / `server` 四层，只有 `server` 碰 socket。

不是为了好看，是为了让"被阻断的请求有没有连到上游"这种问题能用**计数**回答：
假上游记录 `connections`，测试断言它为 0。只看返回 403 的话，一个"先连接、
后判定、然后撒谎"的实现也会通过。

同样因为分层，"阻断页会不会泄露命中词条"可以在没有网络的情况下穷举测试。
这条约束很重要：泄露了命中词条，过滤器就变成了预言机（oracle），孩子可以
用二分法把词库读出来。

### 2.2 入站响应体才是内容过滤的主战场

`analysis/probe/p19` 结论很明确：原厂在 **DBFP_Recv** 里对 **HTTP 响应体**
做关键词匹配，然后 `memcpy` 把预设页覆盖上去。出站方向只做 URL 提取。

所以只检查浏览器发出的请求是检查错了半。孩子在地址栏敲 `example.com`，
请求里没有任何违禁词，该读的是回来的那一页。

`host/proxy/inbound.py` 实现这一半，三条语义直接复用引擎（不重写）：

| 规则 | 引擎出处 |
|---|---|
| 只在 HTTP 端口集上做关键词匹配 | `intercept.HTTP_PORTS`（p19 step 6） |
| 二进制体跳过 | `intercept.looks_binary`（p19 step 4） |
| 名单 C 的 144B 规则语义 | `NameList.match_rule` |

### 2.3 一个刻意偏离原厂的修正

原厂的二进制嗅探是：**前 10 字节里有没有 0x80-0xFE**。

对中文产品而言这条规则是错的：**每一个 UTF-8/GBK 汉字的首字节都 ≥0x80**。
所以一个以中文开头的响应（JSON 接口、`text/plain`、没有前导标签的 HTML 片段）
会被判为二进制并跳过——也就是说，它跳过的恰恰是这个产品存在的理由。

处理方式分两层，这是本项目的通用做法（**逆向结论是证据，实现是产品**）：

- **引擎保持原厂语义**（`intercept.looks_binary`），因为那是"厂商做了什么"的证据；
- **宿主层做修正**（`inbound._decodes_as_text`）：按声明 charset → UTF-8 → GBK
  尝试解码，并要求结果不含 C0 控制字符（除 `\t \n \f \r \x1b`）。
  PNG 仍然解不出来，仍然被跳过。

GBK 几乎能解码任何字节序列，所以"能解码"单独不够——控制字符检查是必需的，
否则 PNG 也会被当成文本。这条在 `test_inbound.py` 里有回归测试。

### 2.4 打包：61–112 MB，不是 20 MB

`docs/architecture.md` 写的"约 20 MB"是**只算引擎+宿主+数据**时的量级。
实测（本机 CPython 3.12.6 + pywin32 + cryptography + psutil）：

| 配置 | 大小 | 包含 |
|---|---|---|
| 引擎+宿主+数据 | 3.7 MB | 53 个文件 |
| + 必需依赖（无 M4/M5） | **61 MB** | + CPython 853 文件 + pywin32 |
| + 可选（M4 图像） | **112 MB** | + onnxruntime 38M + Pillow 15M |

差距的主因是 `onnxruntime`：它自带整个推理运行时。所以：

- 发布包默认 **61 MB**（无 M4），M4 作为单独的可选包
- 那个"20 MB"应修正为"不含 M4 的 61 MB"——本文先记实测值，
  架构文档的修改留给一次专门的文档修订（不在实现文档里改架构承诺）

### 2.5 打包时踩到的三个坑

都是"看起来对、跑起来错"的类型：

**`Lib/site-packages` 被整树拷进去**。第一次打包出来是 **6.0 GB**：
构建机上装了 `torch`（4.3G）和 `nvidia`（1.3G）。文件数只从 853 变成 34437，
不看体积根本发现不了。现在 `site-packages` 由按名字挑选的 `_copy_packages` 负责。

**pywin32 不是普通包**。它装的是 `win32/` `win32com/` `win32comext/` 目录
加一个 `pywin32.pth`，可导入的名字（`win32api`、`win32serviceutil`）是
**子目录里的模块**。按 import 名去找全部失败。现在整个目录拷 + 重现 `.pth`——
没有 `.pth` 的话文件都在但导入不了，这是最难查的失败形态。

**用户级 site-packages**。本机 `psutil` 在
`%APPDATA%\Python\Python312\site-packages`，而 `cryptography` 在安装目录下。
只查一个目录会报"必需包缺失"，而重装一遍什么都不会改变。

---

## 5. 怎么跑

```bash
# 全部（引擎 + 宿主）
python -m pytest

# 只有宿主
python -m pytest host/tests

# 看看这台机器能做什么
python -c "from host.capabilities import probe; import json; print(json.dumps(probe().summary(), indent=1))"

# 看部署清单（不拷任何文件）
python tools/package.py --out ../GreenDam-2026 --dry-run

# 真的打一个包（嵌入本机 CPython）
python tools/package.py --out ../GreenDam-2026 --python C:/Python312

# 不含 M4（小 50 MB）
python tools/package.py --out ../GreenDam-2026 --python C:/Python312 --no-optional

# 图像评分服务（扩展的图片路径靠它；不启动则图片不被检查）
python -m host.imaging                    # 模型在 %ProgramData%\GreenDam2026\models
python -m host.imaging --no-model         # 服务起，但答“未评估”

# 父控制台
python -m host.ui
```

`pyproject.toml` 的 `testpaths` 已包含两个目录。`host/tests/` 是包
（有 `__init__.py`），因为 `tests/test_contracts.py` 和
`host/tests/test_contracts.py` 同名，不加包会让一次 `pytest` 两个目录报
"import file mismatch"。

### 2.6 为什么用浏览器扩展而不是本地根证书

原计划是 H2c：装本地 CA，MITM 解密 HTTPS。**放弃这个方案**，理由不是技术难度。

**HTTPS 让阶段 1-2 的文本过滤失效。** 2026 年的现实：Chrome 的 HTTPS-First
默认开启、HSTS 预加载让主站强制 HTTPS、新顶级域（`.app` `.dev`）只能走 HTTPS、
搜索结果页几乎全是 HTTPS。只做主机名匹配等于"能看域名，看不到任何内容"，
对一个内容过滤器来说是功能层面的废弃。所以必须解决 HTTPS，但 MITM 不是唯一解。

**MITM 的三个问题**：

1. **管辖范围扩张**。装上根证书后，这台机器上**所有** TLS 流量都经过本产品，
   包括家长自己的网银、公司 VPN、医疗记录。而产品定位是"家长保护自己的
   孩子"——这个定位不支持那个范围。
2. **单点风险**。CA 私钥在本机生成，泄露的后果比不装证书更糟。
3. **与产品的核心承诺冲突**。`eventlog.py` 那句"never the body, never the
   host's full path"在 MITM 下必须继续成立，否则产品性质就变了。技术上是
   可以做到的，但这意味着每一条新代码路径都要重新证明这一点。

**浏览器扩展方案**（`host/browsers.py` + `host/policy/`）：

| 维度 | 本地 CA MITM | 浏览器扩展 |
|---|---|---|
| 覆盖 | HTTPS 全量 | 该浏览器内 |
| 系统信任存储 | **修改** | 不碰 |
| 私钥 | 本机生成，需保护 | 不存在 |
| 卸载 | 需删证书 + 私钥 | 删注册表项/文件 |
| 拿到的数据 | 解密后的字节流 | 解密后的 DOM |
| 判定粒度 | 整个响应 | **单个元素** |

最后一行是决定性的。域名/URL 拦截只能"整页放行或整页拦"，而实际场景是：

- **论坛/社交平台**：同一页里 NSFW 和正常内容并存，应该拦那个帖子
- **黄色网站域名随时换**，有的直接走 IP，白名单/黑名单都不可预测
- 维护"黄网网址"需要联网更新，而本产品承诺零网络依赖

DOM 级过滤把这些全部绕开：判的是内容，不是地址。域名换了、IP 直连、
论坛混排，都不影响。

**多浏览器适配**（`host/browsers.py`）：本机实测装了 Chrome、Edge、Firefox
三个，机制完全不同：

| 族 | 检测 | 策略位置 |
|---|---|---|
| Chromium（Chrome/Edge/Brave/Vivaldi/Opera） | `App Paths` 注册表 | `HKLM\SOFTWARE\Policies\<vendor>` |
| Firefox 族 | 同上 | `<安装目录>\distribution\policies.json` |

检测读 `App Paths` 而不是猜路径：本机 Chrome 在 `Program Files`、Edge 在
`Program Files (x86)`，猜任何一个都会漏掉另一个。Chromium **同时读 HKLM 和
HKCU**（`policy_loader_win.cc` 的 `kScopes[]`），所以非管理员安装是可能的。

安装不覆盖的浏览器（如 IE）会**报告**而非静默跳过——家长需要知道过滤器
在那上面不生效。

### 2.7 为什么不做全屏 OCR

方案 B 是"全屏截图 + OCR 摄屏过滤"。**明确不做**，三条理由，第二条是决定性的：

1. **OCR 全屏等于录屏**。哪怕只存判定结果，中间帧也在内存里。
2. **它管的是"屏幕上有什么"，不是"网络上有什么"**。家长的网银、工作文档、
   私人聊天全在管辖范围内。这个产品没有资格看这些。
3. **技术上也不成立**。全屏 OCR 每帧 100ms+，连续跑会让机器发烫、风扇狂转——
   用户会立刻发现并关掉它。一个会被关掉的功能不是功能。

DOM 级过滤是同一目标的正确实现：只看到该看到的（网页内容），看不到不该看的
（其他窗口），而且成本低到可以持续运行。

### 2.8 词库：默认不带，全部可开关

`host/packaging.py` 原本把 `data/legacy-imported/falun-terms.json` 列为必带
运行时数据。**已移出。**

理由来自本仓库自己的测量（`docs/corpus-eval.md` §4.1）：

```
普通小说   命中 96.12% 的文件
成人语料   命中 97.28% 的文件
```

**几乎没有区别。** 那份词表针对的是别的领域，用它做色情过滤等于随机拦截。
把它打进一个"家长控制"产品，既无效，也是在替家长表态——而这产品没有这个
立场。

改为**词库包**（`green-dam words`）：

```bash
green-dam words list                          # 有哪些包，多少词，是否启用
green-dam words enable adult                  # 打开
green-dam words disable political             # 关闭（仍然列出）
green-dam words add mylist --term 自定义词     # 加词
green-dam words import custom.wordpack.json   # 导入
green-dam words export adult out.json         # 导出（给另一个家长）
```

三个设计点：

- **一个包一个文件**。"装了什么"和"开了什么"不可能不一致，家长可以单文件备份
  或迁移。
- **禁用不等于隐藏**。禁用的包仍然列出，因为"为什么这个没被拦"需要一个答案。
- **状态输出只有计数，没有词条**。列出词条等于把词库复制一份到日志里。

默认部署**不含任何词库**。这是诚实的起点：没有词库的过滤器报告"未评估"而不是
"干净"，引擎已经区分这两者。

### 2.9 扩展本身：为什么判定写在 Python 里

扩展的代码是 JS，无法在这里单元测试。但**决定隐藏什么**这件事的风险最高，
所以它被写成 Python（`host/extension/__init__.py` 的 `decide_text` /
`decide_image`），带测试，然后由 `build_extension()` 生成 `config.json`。
`content.js` 实现同一套规则并加载同一个配置。

两边的同步靠一个 Node harness（`host/tests/js/run.js`）保证：它从
`content.js` 里**提取真实函数**（不是复制品）在 DOM stub 上跑，断言 15 个行为。
`test_extension.py` 调用它，所以 Python 规格与 JS 实现的偏离会让测试失败。

**为什么不用真浏览器验证**：Chrome 152 / Edge 156 已经忽略
`--load-extension`。本机实测：`chrome://extensions` 的列表为空，
页面里 `chrome.runtime` 是 `undefined`。驱动真浏览器什么也测不到，所以 DOM
stub 是诚实的选择而不是偷懒。

三个行为约束，每个都有测试：

| 约束 | 理由 |
|---|---|
| **隐藏元素，不跳转** | 论坛一帖不合适只隐藏那一帖，整站照常可用 |
| **不碰页面自身结构** | 隐藏导航/评论框会让站点看起来坏了，家长会关掉过滤器 |
| **就地显示提示** | 元素静默消失像渲染 bug，孩子会学会刷新绕过 |

扩展的权限只有 `storage` + `activeTab` + 网页 host。`test_extension.py`
里有一条否定断言：不得出现 `tabs`/`history`/`debugger`/`webRequest` 等
监控类权限——一个家长控制扩展不该申请恶意软件才申请的权限。

### 2.10 控制台为什么是只读的

本版控制台**没有任何写操作**。家长做的每一件事——开关词库、改密码、装扩展——
都走命令行，因为 CLI 的每个动作都进日志、可复现、可审计。一个 POST 端点需要
CSRF 防护和第二条审计路径，在没有屏幕需要它之前不加。

四条安全边界，每条一个测试（`host/tests/test_ui.py`）：

| 边界 | 为什么承载 |
|---|---|
| **只绑 127.0.0.1** | 绑到 `0.0.0.0` 会把控制台发布到局域网——一个显示拦截记录的页面，等于一张"什么该躲开"的地图 |
| **每次启动生成 token** | 没有它，同用户的任何本地进程都能读日志 |
| **拒绝跨源请求** | 开放网页可以让浏览器向 `127.0.0.1` 发请求；Origin 检查把 token 泄漏到浏览器历史也变成不可重放 |
| **不提供任何文件服务** | 白名单四个资产，没有任何路径参数——这是通常被跳过、把服务器变成文件泄露漏洞的那一条 |

诚实语义贯穿数据层：`assessing=False` 渲染为红点加"未在检查任何内容"，绝不
渲染成绿勾。日志视图只显示 reason token 和内容摘要（16 位截断 SHA-256），
**没有正文**——不是控制台刻意不显示，而是日志里根本没有。

窗口三路回退：`pywebview`（WebView2）→ Edge `--app`（无地址栏的独立窗口，
每台 Win10/11 都有）→ 默认浏览器。`pywebview` 是可选依赖，缺了就降级，不会
让控制台起不来。

### 2.11 一个被“测过了”掩盖的空洞：评分服务

`background.js` 一直在往 `http://127.0.0.1:17871/score` POST 图片字节，而**那
个端口上从来没有进程监听**。扩展的图片路径因此是完全的空转：每次
`sendMessage` 拿到 `null`，图片原样留在页面上。

它没被发现，是因为**两端各自都有测试**：规则在 `test_extension.py`，模型在
`test_imaging.py`，引擎的 `verdict_from_score` 在 `test_imagepath.py`。缺的是
两者之间那个 HTTP 端点，而它不属于任何一边的测试范围。这是本项目要防的典型
失败：**每个零件都对，装配起来是空的。**

`host/imaging/service.py` 补上这一段，`python -m host.imaging` 启动它。

三条边界，每条一个测试（`host/tests/test_scoring.py`，28 项）：

| 边界 | 为什么承载 |
|---|---|
| **只绑回环** | 能返回“这张图有多露”的服务，等于一个内容判定预言机。与控制台同一条规则，但这里后果更直接 |
| **只让扩展源读回复** | 任何网页都能让浏览器 POST 到 `127.0.0.1`；若回复可读，任何站点都能白拿家长的分类器，并用探测法量出阈值 |
| **先按头部限长，再解码** | 解压炸弹发送极便宜、解码极贵。`Content-Length` 与像素数都在 `Image.convert` 之前检查 |

回复的形状由 `background.js` 决定，因此逐字段断言：`assessed` 为 `false` 时
`score` 必须是 `null`。给 0.0 会让扩展把“没看”当成“干净”而放行——这正是
`docs/HANDOFF.md` §4.1 那条规则，扩展自己的注释也写明它依赖这一点。

**同一处补上的另一个洞**：`host/tests/test_contracts.py::_modules()` 原先把所有
`__init__.py` 排除在扫描外，理由是“包初始化文件没有逻辑”。实际上它排除了包里
最大的三个模块——`imaging`（ONNX 后端）、`extension`（过滤规则）、`policy`
（注册表写入）——因为子目录就是它们的归档方式。修正后扫描立刻抓到两个真问题：
`imaging` 未申报 `numpy`，且直接 `import green_dam` 绕过了 `host.engine`。
两个都已按规则修好（申报 + 走 `host.engine.verdict_from_score`），不是加豁免。

教训与 §3.2 同一条，只是换了个位置：**扫描器自己也会漏扫，而漏扫比不扫更糟
——它会对没读过的代码报“干净”。**

### 2.12 离线是产品属性，不是部署选项

产品**从不为自己联网**。这是一条硬约束，不是"默认行为"：

| 什么 | 从哪来 |
|---|---|
| 图像模型（21.4 MB） | **随安装包分发**，落在 `<部署>/data/models/nsfw-384/` |
| 词库包 | 用户自己下载 JSON 后 `green-dam words import` 导入 |
| 程序更新 | 用户下载新安装包，手工覆盖 |
| 许可证/说明 | 随包分发（`LICENSE` + `README.md` 跟着权重走） |

**为什么模型必须打进包**：家长可能把它装在永不上网的机器上。一个"装完再下载
模型"的图像过滤，在那种机器上就是图像过滤不存在。而且下载意味着首次启动要等、
要能连通、要有重试与断点——三个都可能失败的环节，换来的只是安装包小 21 MB。

`.gitignore` 排除 `*.onnx`（22 MB 进源码树是错误，git 也不是分发渠道），
但 `host/packaging.py` 把**缺模型当构建错误**而不是警告：

```
refusing to package: the manifest is incomplete
  data/models/nsfw-384/model.onnx: the image classifier is not present
```

因为一个悄悄缺模型的部署，控制台会报"没有模型"、扩展会放行每张图，
**而没有任何东西会失败**。`--no-optional` 是唯一合法的无模型构建，
它把省略**记进 manifest 的 excluded**，箱子仍然能解释自己。

**唯一的例外是代理**：它按浏览器的请求去连接目标站点。那是用户要求的转发，
不是产品为自己取数据。`host/tests/test_contracts.py::OfflineTest` 用白名单
钉住这一点——白名单只有 `("proxy.server", "socket.create_connection")` 一项，
新增任何一条都要写明"这是转发用户流量"的理由。

同一个测试还钉住：没有 `telemetry`/`analytics`/`check_for_updates` 之类的名字，
扩展清单里没有 `update_url`（有它就是一条推送代码的通道），扩展不 `eval`、
不 `importScripts`。

**这条规则与 §2.11 的模型查找顺序是一件事**：模型先找 `<部署>/data/`，再找
`%ProgramData%`。前者让便携安装自给自足，后者让管理员能一处更新所有账户。
两条路径都由 `host.engine.find_model()` 决定，控制台与评分服务都调它——
否则会出现"控制台说没模型、评分服务却能加载"这种让人找错地方的状态。

### 2.13 打包形态 × 服务账号：四种组合的实际差别

两个决策经常被当成同一个问题，其实正交：**装在哪**决定路径与可写性，
**以谁运行**决定能碰什么。四种组合的差别不是风格，是可用的功能集。

#### 两个维度的含义

**打包形态**

| | Program Files（机器级安装） | 便携目录（自解压/绿色） |
|---|---|---|
| 程序文件位置 | `%ProgramFiles%\GreenDam2026\` | 用户选的任意目录 |
| 程序目录可写性 | **只读**（需提权） | 当前用户可写 |
| 数据位置 | `%ProgramData%\GreenDam2026\` | 可以全部在目录内 |
| 安装/卸载 | 需要安装程序 + 管理员 | 复制/删除目录 |
| 多用户共享 | 天然共享一份 | 每份独立 |
| 升级 | 安装程序覆盖，管理员 | 手工替换 |
| 防篡改（孩子删掉） | 较好（ACL 挡住普通用户） | **差**（在自己目录里） |

**服务账号**

| | LocalSystem | 专用虚拟账号（如 `NT SERVICE\GreenDam`） |
|---|---|---|
| 权限级别 | 本机最高，等同管理员 | 只给需要的特权 |
| 能否改 HKLM 策略 | ✅ | ✅（若授予） |
| 能否 `netsh advfirewall` | ✅ | ✅（若授予） |
| 能否终止其他用户进程 | ✅ | ✅（若授予 `SeDebugPrivilege`） |
| 能否读用户目录 | ✅（全盘可读） | 需显式授权 |
| 被攻破的后果 | **整个系统** | 仅产品功能 |
| 日志文件属主 | SYSTEM | 服务账号 |

#### 四种组合

| | **LocalSystem** | **专用账号** |
|---|---|---|
| **Program Files** | **推荐**。全功能：策略写入、防火墙、跨用户终止进程、卸载清单。程序目录只读，孩子改不了。代价是安装要管理员、升级要管理员。 | **最安全**。功能齐全但爆炸半径小。代价：要给 `SeDebugPrivilege` 与 HKLM 写权限，ACL 配错就是"某个功能静默失效"。 |
| **便携目录** | **矛盾组合**。服务账号是 SYSTEM，但程序目录在用户可写位置——孩子能直接改可执行文件或删模型。除非 ACL 收紧到管理员可写，否则**不推荐**。 | **受限可用**。适合"我只想过滤自己的浏览器"：进程监控与防火墙基本不可用（没有跨用户权限），但代理 + 扩展 + 图像评分全部可用。 |

#### 每种组合下哪些功能真的能用

`host/processes.py` 已经把权限不足变成**报告**而不是异常
（`"access denied: the service must run elevated to do this"`），
所以下表的"不可用"是控制台看得见的状态，不是崩溃。

| 功能 | Program Files + SYSTEM | Program Files + 专用账号 | 便携 + 专用账号 |
|---|---|---|---|
| 代理过滤（`host/proxy/`） | ✅ | ✅ | ✅ |
| 浏览器扩展（DOM 过滤 + 图像） | ✅ | ✅ | ✅ |
| 图像评分服务 | ✅ | ✅ | ✅ |
| 防火墙规则（`host/firewall.py`） | ✅ | ✅（需授权） | ⚠️ 需提权，否则拒绝并报告 |
| 进程终止（`host/processes.py`） | ✅ | ✅（需 `SeDebugPrivilege`） | ⚠️ 只能终止自己会话的进程 |
| 浏览器策略写入（`host/policy/`） | ✅ HKLM | ✅ HKLM（需授权） | ⚠️ 只能写 HKCU，对"孩子用别的账户"无效 |
| 审计日志（`%ProgramData%`） | ✅ | ✅ | ⚠️ 落在用户目录，孩子可读可删 |
| 防孩子卸载 | ✅ | ✅ | ❌ |

#### 两个具体后果

**便携 + 专用账号下，`host/policy/` 只能写 `HKCU`。** `apply.py` 已经支持两种
hive，且要求调用方显式写出作用域（`HKLM\...` 或 `HKCU\...`），因为"默认写哪个"
正是会被静默搞错的地方。写 `HKCU` 的策略只对**当前账户**生效——孩子换个账户
打开浏览器，策略就没了。这不是 bug，是便携形态的固有限制，必须说清楚。

**Program Files + LocalSystem 下，卸载清单必须完整。** 服务以 SYSTEM 写入的
每一项（HKLM 键、防火墙规则、`%ProgramData%` 目录、服务注册）都要能被
`host/paths.py::journal_path()` 记录的 install.json 反查并回滚。`B11`
（卸载后残留注册表项）是禁止项，而 SYSTEM 能写的地方比普通用户多，漏一项
就留一个残留。

#### 建议

**默认发 Program Files + 专用账号。** 功能完整，爆炸半径小，且 ACL 配置是
安装程序的一次性工作，而不是每次启动的风险。LocalSystem 留给"专用账号配不通"
的降级路径——它在功能上等价，只是被攻破时后果不同。

便携形态作为**第二条产品线**存在：它解决的是"家长没有管理员权限"和"不想在
系统里留下东西"两个真实需求。但必须接受上表里的三个 ⚠️，且控制台要把
"哪些功能因为形态不可用"直接显示出来，而不是让家长以为全都开着。
