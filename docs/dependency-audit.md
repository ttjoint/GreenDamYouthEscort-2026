# 依赖审计：为什么全 Python

本文记录一次**事后审计**：架构文档最初写"宿主是 .NET 8"，但那条结论从未被
论证过。开始宿主层开发前，逐模块核对了实际需求。

结论：**没有任何模块需要 .NET**。改为全 Python。

---

## 0. 为什么审计

`docs/architecture.md` 原文：

> The host — service, network interception, UI automation, console — is **.NET 8**
> and Windows-only.

这句话写于项目早期，**没有给出理由**。它是 Windows 桌面开发的"合理默认值"，
不是经过论证的选择。

触发这次审计的是一条产品需求：

> 只要是 Win10 及以上系统，**无须额外安装太多东西就可以直接部署**。

这条需求直接否掉了 .NET 8：

| 方案 | 目标机需要 | 部署体积 | 符合需求 |
|---|---|---|---|
| .NET 8 框架依赖 | 装 .NET 8 运行时 | ~2 MB | ❌ |
| .NET 8 自包含 | 无 | ~150 MB | ⚠️ 体积大 |
| .NET Framework 4.8 | Win10 1903+ 内置 | ~2 MB | ✅ |
| **嵌入 CPython** | **无** | **~20 MB** | ✅ |

若坚持 .NET，唯一符合"零安装"的是 **.NET Framework 4.8**（Win10 1903+ 内置）。
但 .NET Framework 4.8 是**维护模式**技术：无 `System.Text.Json` 内置、异步受限、
新库支持差。为一条未经论证的假设去锁死一个维护模式技术栈，代价过高。

于是回头问：**真的需要 .NET 吗？**

---

## 1. 逐模块核对

方法：对每个模块列出"需要调用什么 Windows 能力"，再查 Python 是否有可用绑定。
所有版本号来自本机 `pip index versions` 实测（2026-10）。

### M2 网络拦截

架构要求四段降级（`architecture.md §M2`）：

| 需求 | Python 方案 | 实测 |
|---|---|---|
| 阶段1 防火墙规则 | `subprocess` + `netsh advfirewall`，或 pywin32 COM (`INetFwPolicy2`) | ✅ |
| 阶段2 明文 HTTP 透明代理 | 纯 `asyncio` + `socket` | ✅ **零依赖** |
| 阶段3 HTTPS MITM | 标准库 `ssl` + `cryptography` 生成/签发证书 | ✅ `cryptography 48.0.0` 已装 |
| 阶段4 图像判定 | 现有引擎 | ✅ 已有 |
| 根证书装入受信任存储 | `subprocess` + `certutil -addstore`，或 pywin32 COM | ✅ |

**判断**：无需 .NET。MITM 代理在 Python 生态里是成熟领域——`mitmproxy` 本身
就是 Python 写的。

### M3 文本判定

已完成，纯标准库。✅

### M4 图像分类

| 需求 | Python 方案 | 实测 |
|---|---|---|
| ONNX 推理 | `onnxruntime` | ✅ **1.26.0 已装** |
| 图像解码/缩放 | `Pillow` | ⚠️ 需装（约 3 MB） |

**判断**：无需 .NET。且 `onnxruntime` 的 Python 绑定是官方一等支持。

### M5 屏幕文字（唯一实质降级）

| 需求 | Python 方案 | 实测 |
|---|---|---|
| UI Automation | `uiautomation 2.0.29`（基于 `comtypes`） | ✅ pip 可装 |
| Windows.Media.Ocr | `winrt-Windows.Media.Ocr 3.2.1` | ✅ pip 可装 |

⚠️ **这是唯一比 .NET 弱的地方**：

- WinRT 的 Python 绑定由社区维护（`pywinrt` 项目），不如 .NET 官方绑定稳
- `winsdk` 包已废弃，现用 `winrt-*` 分包系列，API 变动较快
- OCR 语言包依赖系统组件，Python 侧错误信息不如 .NET 清晰

**判断**：能力上可行，工程质量上略弱。如实记录，不掩饰。

### M6 应用控制

| 需求 | Python 方案 | 实测 |
|---|---|---|
| 进程枚举 | `psutil` | ✅ 已装 |
| 终止进程 | `psutil.Process.terminate()` / `win32api.TerminateProcess` | ✅ |
| 命令行读取（判定名单 B） | `psutil` / `win32process.GetModuleFileNameEx` | ✅ |

**判断**：无需 .NET。

### M7 基础设施

| 需求 | Python 方案 | 实测 |
|---|---|---|
| Windows 服务宿主 | `win32serviceutil.ServiceFramework` | ✅ pywin32 已验证可用 |
| 服务 ACL（拒绝非管理员 STOP） | `win32security` | ✅ pywin32 含 |
| 服务恢复动作 | `sc.exe failure` | ✅ `subprocess` |
| 托盘图标 | `pystray` | ⚠️ 需装（约 100 KB） |
| 配置/日志/密码/卸载清单 | 现有引擎 | ✅ 已有 |

**判断**：无需 .NET。pywin32 的服务框架是 Windows 服务的标准 Python 方案。

### M8 策略

已完成，纯标准库。✅

### M9 UI

| 需求 | Python 方案 | 实测 |
|---|---|---|
| 原生窗口 | `pywebview 6.2.1` | ✅ pip 可装 |
| WebView2 运行时 | 系统组件 | ✅ 本机 156.0.4314.42 |

**判断**：无需 .NET。且 HTML UI 比 WinForms 更现代、更易迭代。

---

## 2. 汇总

| 模块 | .NET 必需？ | Python 判定 | 备注 |
|---|---|---|---|
| M1 数据 | 否 | ✅ 已完成 | |
| M2 网络 | 否 | ✅ 可行 | 需 `cryptography` |
| M3 文本 | 否 | ✅ 已完成 | |
| M4 图像 | 否 | ✅ 可行 | `onnxruntime` 已装 |
| M5 屏幕 | 否 | ⚠️ **可行但略弱** | WinRT 绑定社区维护 |
| M6 进程 | 否 | ✅ 可行 | `psutil` 已装 |
| M7 基础设施 | 否 | ✅ 可行 | pywin32 已验证 |
| M8 策略 | 否 | ✅ 已完成 | |
| M9 UI | 否 | ✅ 可行 | pywebview + HTML |

**零个模块需要 .NET。一个模块（M5）Python 方案质量略低。**

---

## 3. 依赖清单

### 必需（不含 M4/M5）

| 库 | 用途 | 状态 |
|---|---|---|
| `pywin32` | 服务、ACL、进程、COM | ✅ 本机已装 |
| `cryptography` | MITM 证书签发 | ✅ 本机已装 48.0.0 |
| `psutil` | 进程枚举与终止 | ✅ 本机已装 |
| `pywebview` | 原生窗口 | 需装 6.2.1 |
| `pystray` | 托盘图标 | 需装 |

### 可选（按模块启用）

| 库 | 用于 | 体积 |
|---|---|---|
| `onnxruntime` | M4 图像分类 | 约 50 MB（含运行时） |
| `Pillow` | M4 图像解码 | 约 3 MB |
| `uiautomation` | M5 屏幕文字 | 约 200 KB |
| `winrt-Windows.Media.Ocr` | M5 OCR | 约 1 MB |

### 标准库即可

M1 / M3 / M8 全部、M2 阶段2 代理主体、M7 配置/日志/密码/清单。

---

## 4. 部署形态

```
GreenDam-2026/
├── GreenDam.exe          启动器（可由 pywin32 服务或快捷方式调用）
├── python/               嵌入 CPython 3.12（约 15 MB）
│   ├── python.exe
│   ├── python312.zip
│   └── Lib/site-packages/  仅含上表所需
├── engine/green_dam/     现有引擎（9,099 行，零第三方依赖）
├── host/                 宿主包
│   ├── ui/               HTML/CSS/JS
│   ├── service/          服务宿主
│   ├── proxy/            代理
│   └── winapi/           防火墙、证书、进程、托盘
└── data/                 词库、策略模板
```

**目标机需要安装的东西：无。**

WebView2 缺失时按 `architecture.md §Target` 的三级降级处理。

---

## 5. 这次审计暴露的流程问题

1. **架构文档里的技术选型必须带理由。** 原文只写"is .NET 8"，没写为什么。
   没有理由的决定无法被质疑，也就无法被修正。

2. **"合理默认值"不是论证。** .NET 对 Windows 桌面是惯例，但惯例不等于适配。
   这次是产品需求（零安装）逼出了重新审视。

3. **降级要写清楚。** M5 的 WinRT 绑定弱于 .NET，这条写进文档而不是含糊带过。
   如果将来 M5 成为核心功能，这个决定值得重新评估——那时的正确做法可能是
   **只为 OCR 写一个小的 .NET 辅助进程**，而不是把整个宿主搬回 .NET。

---

## 6. 复现

本文所有版本号可用下列命令复核：

```bash
python -c "import win32serviceutil, win32security; print('pywin32 ok')"
python -c "import cryptography; print(cryptography.__version__)"
python -c "import psutil, onnxruntime; print('ok')"
pip index versions pywebview
pip index versions uiautomation
pip index versions winrt-Windows.Media.Ocr
```

WebView2 运行时版本查注册表：

```powershell
Get-ItemProperty 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
```
