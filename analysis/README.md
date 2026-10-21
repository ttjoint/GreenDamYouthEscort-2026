# analysis/ — 逆向分析与复原文档归档

本目录存放对原软件（Green Dam Youth Escort 3.17 / HNC 过滤器）的逆向分析成果。
**今后新项目的各类分析文档也统一放这里**，根目录只留 README。

---

## 目录结构

| 文件 | 内容 |
|---|---|
| `00-MASTER.md` | 总账：反幻觉纪律、状态机、已确认结论 C-001…C-020、进度 |
| `00b-FACTS.md` | 统一机读事实表（每行带 method + evidence + status） |
| `00c-CORRECTIONS.md` | 自我纠错记录（3 处实质错误 + 证据） |
| `01-FILES.tsv` | 117 个文件清单（sha256/md5/magic） |
| `02-MODULES.md` / `-RAW.tsv` | 32 个 PE 模块（厂商/导出/导入/基址） |
| `03-DATA-FORMATS.md` | 8 个数据格式 + 闭合证明 |
| `04-ENTRYPOINTS.md` | 函数级入口/出口/参数（IPGate 完整还原） |
| `05-INTERFACES-RAW.txt` | 7,312 行全量导出/导入 |
| `06-LINKS.tsv` | 310 条跨模块边（机器可枚举） |
| `06b-INDIRECT-LINKS.md` | 间接边（注册表/文件/进程/COM） |
| `07-CONFLICTS.md` | 10 个冲突裁定（含判定实验） |
| `08-CLAIMS-DEMOTED.tsv` | 99 条旧断言逐条裁定 |
| `09-SPEC.md` | 复刻项目接口契约 spec（工程规范） |
| `probe/` | 可重跑验证脚本 + 证据输出 |

## 核心结论（C-001…C-020 摘要）

逆向工作分多轮完成，每轮都有字节级证据，纠正了旧文档（`REVERSE-ENGINEERING/`）
的多处错误。关键结论：

1. **`.urf` = AppFace 皮肤包**（明文 GIF 容器，非加密），消费方 `appface.dll`（UPX 壳）
2. **`FalunWord.lib`** = 六段 `fread` 布局（算术闭合，残差 = 5 = len("HNCIT")）
3. **过滤体系** = 双向五层（进程 → IP/端口 → 协议指纹 → URL → 关键词）
4. **名单（四组）**：A=出站URL(100)、B=监控进程(无显式上限)、C=入站关键词(1000)、D=动态(1500)
5. **规则语法**：`{词A,词B}[字C,字D]` = （含A或B）且（含C或D）
6. **控制协议**：`SG_Control` 命令码（55 个实现），策略/名单下发中枢
7. **HNC 句法子链**：代码完整但入口零调用（产品只用词条级 `CorpGroup→MakeALC`）

详见 `09-SPEC.md`（复刻契约）与 `00-MASTER.md`（完整结论）。

## 关于 probe 脚本

- probe 脚本是**证据生成器**，其输出（.txt/.tsv）已保存在本目录。
- 脚本用 `os.path` 相对定位**原始安装树**（`LH-installed-tree/`），
  该树被 `.gitignore` 排除（版权原因），不在此仓库内。
- **重跑条件**：需在包含 `LH-installed-tree/` 的上级目录运行（原始工作目录），
  或在脚本中调整 `ROOT` 路径指向原始安装树。

## 方法论（反幻觉纪律）

- 每个事实必须可重跑验证（`probe/` 脚本）
- `CLOSED` 只授予独立取证的事实；`DOC-CLAIM` 计零
- `REFUTED` 不删除（防止重新推导出同样的错误）
- 已知核验器误差（§1.4 主账）：大小写、VA vs 文件偏移、显示截断、编码层
