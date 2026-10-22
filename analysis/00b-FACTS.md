# 00b — FACTS 统一事实表（机读，单一是非来源）

> **这是全案唯一的事实权威表。** 它存在的目的：让任何一行都能被**独立复核**，
> 让矛盾无处藏身。`.md` 文档只是本表的**人读摘要**，不具权威性。

## 规则（比 00-MASTER 更严）

1. 每行一个**原子事实**，附 `method`（取证方法）与 `evidence`（可重跑命令/文件行号）。
2. `status` 只允许四态：`CLOSED`（独立证实）/ `REFUTED`（独立推翻）/
   `PROBING`（证据部分）/ `CLAIM`（仅存在于旧文档，未复验）。
3. **关键**：`CLOSED` 只授予「我本人用二进制/反汇编/算术独立证实」的事实。
   引用 GDE 或 RE 的结论本身**不足以**成为 CLOSED，必须有自己的取证。
4. 交叉引用 `08-CLAIMS-DEMOTED.tsv` 里的 `claim_id`，保证可追溯。

## 字段说明

- `id`：F001 起递增
- `fact`：原子事实陈述（一句话，可证伪）
- `method`：`ARITH`/`HASH`/`PE-HDR`/`BYTES`/`STR`/`DISASM`/`BYTESCAN`/`UNICORN`
- `evidence`：probe 文件名 或 命令
- `status`：如上
- `src`：来源（ME=本账独立 / GDE=复刻仓库 / RE=逆向文档）
- `claim_id`：对应 08 表行

---

## 一、文件与容器层

| id | fact | method | evidence | status | src | claim_id |
|---|---|---|---|---|---|---|
| F001 | 安装树 117 文件、38 PE（树内 32 PE） | HASH | p01_inventory.py | CLOSED | ME | L-012,L-013 |
| F002 | `kwselectinfopp.dll` 672B 非 PE | PE-HDR | p01_inventory.py | CLOSED | ME | — |
| F003 | `FalunWord.lib` = 5,564,271 B，末 5B=`HNCIT` | BYTES | p01_closure_falunword.txt | CLOSED | ME | L-005,L-007 |
| F004 | `FalunWord.lib` 布局算术闭合：`4+n1*16+65535*16+4+n2*16+4+n3*2`，残差=5 | ARITH | p01_closure_falunword.txt | CLOSED | ME | L-007 |
| F005 | 上述 n1=272920, n2=4693, n3=36943 | ARITH | 同上 | CLOSED | ME | GDE一致 |
| F006 | `sys.dat`≡`xconfigs.dat`，268B=67 dword，MD5 b935aad6e250fd44fed3c76e965793ea | HASH+ARITH | 03 | CLOSED | ME | L-021,L-009 |
| F007 | `kwselectinfopp.dll` = 84B×8 记录 | ARITH | 03 | CLOSED | ME | L-011 |
| F008 | `.dat` 词库 2:1 位重排可逆，往返 5/5 | CODEC-RT | 03 | CLOSED | ME | L-022 |
| F009 | `filtport.dat` = 33B 明文 | BYTES | 03 | CLOSED | ME | L-020 |
| F010 | `HNCLIB/` 目录**仅** `FalunWord.lib` 一个文件 | BYTES | find | CLOSED | ME | — |

## 二、模块接口层

| id | fact | method | evidence | status | src | claim_id |
|---|---|---|---|---|---|---|
| M001 | 厂商版本资源是 UTF-8（非 GBK） | PE-HDR | 02-MODULES-RAW | CLOSED | ME | — |
| M002 | 主控端系列联合署名「金惠+大正」 | PE-HDR | 02-MODULES-RAW | CLOSED | ME | L-014 |
| M003 | `HASrv.dll` 导出 InstallApiHook/SetApiHookList/UninstallApiHook | EXP | 05-INTERFACES-RAW | CLOSED | ME | L-016 |
| M004 | `Handler.dll` 导出 9 个状态/保护面函数，无 InstallApiHook | EXP | 05-INTERFACES-RAW | CLOSED | ME | L-016 |
| M005 | `dbfilter.dll` 导出 WSPStartup@0x13b0 + DBFilter*@0x1bb0/1d10/1d50 | EXP | 05-INTERFACES-RAW | CLOSED | ME | — |
| M006 | `dbServ.dll` 92 导出中 89 与 kwimage 同名，另 3 个 DBFilter* | EXP | 05-INTERFACES-RAW | CLOSED | ME | L-023 |
| M007 | `dbfilter.dll`→`SurfGd.dll` 静态导入 6 个 DBFP_* | IMP | 06-LINKS | CLOSED | ME | — |
| M008 | `SurfGd.dll` 仅 3 消费者（dbfilter/IPGate/XNet2） | IMP | 06-LINKS | CLOSED | ME | — |
| M009 | `MPSvcDll.dll` 的 FilterWords/UpdateWords/SnapShotScreen/WriteP2FileStr 全树零导入、零字符串引用 | IMP+STR | 06-LINKS | CLOSED | ME | L-024 |
| M010 | `Handler.dll` 导入 SentenceObj 的 CreateHncTextGroup/ReleaseHncTextGroup | IMP | p08 | CLOSED | ME | — |
| M011 | `HncEng.exe` 导入 SentenceObj 同样 2 函数 | IMP | 06-LINKS | CLOSED | ME | — |

## 三、函数级行为（DISASM 取证）

| id | fact | method | evidence | status | src | claim_id |
|---|---|---|---|---|---|---|
| B001 | `IPGate.Start`→SetWindowsHookExA(WH_GETMESSAGE=3,proc@0x100010c0) | DISASM | 04 | CLOSED | ME | L-018,L-019 |
| B002 | IPGate 钩子过程 0x100010c0 是纯 CallNextHookEx 透传 | DISASM | 04 | CLOSED | ME | L-019 |
| B003 | IPGate 内联 hook 引擎：0x23c0 装/0x2440 卸，GetCurrentProcess→FlushInstructionCache→VirtualProtect(0x40) | DISASM | 04 | CLOSED | ME | — |
| B004 | IPGate SetParam 拷贝 9 dword(36B)→0x1000a528 | DISASM | 04 | CLOSED | ME | — |
| B005 | IPGate GetLogBuf = strlen+memcpy@0x1000a54c | DISASM | 04 | CLOSED | ME | — |
| B006 | `RunAfterSetup` LoadLibraryA(\DBFilter.dll)+GetProcAddress 取 3 个 DBFilter 函数 | DISASM | p06 | CLOSED | ME | — |
| B007 | `RunAfterSetup` 自启链字符串块 0x1a100-0x1a5c0 | BYTES | p07 | CLOSED | ME | L-030 |

## 四、HNC 引擎层（**高争议区，必须严格**）

| id | fact | method | evidence | status | src | claim_id |
|---|---|---|---|---|---|---|
| H001 | SentenceObj 导出 72 个，含 MakeALC/CutWordMax/MakeSentSC/GetSentenceALC 等 | EXP | p08 | CLOSED | ME | RE#47 |
| H002 | `MakeALC` 被 3 处直接 call（0x2e13/0x2ea6 在 CorpGroup 内，0x4ee2 在 GetALC 内） | BYTESCAN | p09 | CLOSED | ME | — |
| H003 | `GetSentenceALC`/`MakeSentSC`/`CutWordMax`/`CheckVV` 0 直接调用 + 0 指针引用 | BYTESCAN | p09 | CLOSED | ME | RE#42,GDE |
| H004 | `CreateHncTextGroup`@0x50a0 调 OutputObjListIni(path,2,3) | DISASM | p09 | CLOSED | ME | — |
| H005 | `OutputObjListIni` 内 `new(0x1004a0)` 构造 CFWordLib 再 LoadLib | DISASM | p09 | CLOSED | ME | — |
| H005b | `??0CFWordLib`@0x3e40 在 `+0x10013c` 处内嵌构造 CSentenceObj（`lea ecx,[esi+0x10013c]; call ??0CSentenceObj`） | DISASM | 00c | CLOSED | ME | 纠正 K-07 |
| H005c | `CreateHncTextGroup` 返回值 `+0x124` 子对象的 vtable=`0x451dc`，slot5=CorpGroup | DISASM | 00c | CLOSED | ME | — |
| H006 | `CreateHncTextGroup` 成功返回 `全局+0x124`，失败返回 NULL | DISASM | p09 | CLOSED | ME | — |
| H007 | 全局对象位于 `[0x100b31e8]` | DISASM | p09 | CLOSED | ME | — |
| H008 | `OutputObjListIni` 里 LoadLib 成功后存参数到对象 +0x100468/+0x100470 | DISASM | p09 | CLOSED | ME | — |
| H009 | 句法函数名全树仅出现在 SentenceObj.dll 自身 | STR | p09 | CLOSED | ME | — |
| H010 | 词条层用 `FalunWord.lib`（LoadLib@0x22d0 内联复制），句法层用 `Word.lib`+`rule.lib`（SetSentBreak@0x78e0），后者缺失 | DISASM+STR | p10b | **CLOSED** | ME | GDE一致 |

## 五、策略层

| id | fact | method | evidence | status | src | claim_id |
|---|---|---|---|---|---|---|
| P001 | XNet2 策略名表 1-25 项在 0x68dbc-0x68f78，倒序 | STR | p10 | CLOSED | ME | — || P002 | 第 17 项 =「允许自动抓屏」，非「允许自动关机」 | STR | p10 | CLOSED | ME | RE#67 |
| P003 | 「允许自动关机」在 XNet2 中不存在 | STR | p10 | CLOSED | ME | RE#67 |
| P004 | sys.dat 非零下标 = 3-24（非 3-22），25-66 全零 | ARITH | p10 | CLOSED | ME | RE#33 |
| P005 | 下标→策略号-1 的映射语义自洽（日志3天/更新14天等） | 推理 | p10 | **PROBING** | ME | — |

## 六、安全情报层（IOC）
| id | fact | method | evidence | status | src | claim_id |
|---|---|---|---|---|---|---|
| I001 | InjLib32 注入名单 48/48 命中 | STR | p05 | CLOSED | ME | L-025 |
| I002 | Handler 终止名单 22/22 命中（含自身进程） | STR | p05 | CLOSED | ME | L-026 |
| I003 | XNet2 硬编码 URL 9/9、IP 12/12 命中 | STR | p05 | CLOSED | ME | L-027,L-028 |
| I004 | 注册表路径 2 条归属错误（FilterDam/KingWayKillPorn 在 RunAfterSetup 非 XNet2） | STR | p05 | CLOSED | ME | L-029 |
| I005 | `7895123` 在 gn.exe 0x4d48c 与 XNet2 0x4690ec 均存在 | STR | p08 | CLOSED | ME | RE#23 |
| I006 | MD5('112233')=D097...98F 在 gn.exe 0x4d288 | HASH | p08 | CLOSED | ME | RE#11 |

## 七、.urf 皮肤容器层（本轮新增，重大认知修正）

| id | fact | method | evidence | status | src | claim_id |
|---|---|---|---|---|---|---|
| U001 | `.urf` 消费方 = `appface.dll`（非 Surfgd.dll） | IMP+STR | p12 | CLOSED | ME | RE#26b |
| U002 | `gn.exe` 从 AppFace.dll 只导入 ord3=SkinWindowSet | IMP | p12 | CLOSED | ME | — |
| U003 | `appface.dll` 是 UPX 加壳（全树唯一）：UPX!@0x3e0，UPX0+UPX1+.rsrc | PE-HDR | p12 | CLOSED | ME | RE#29b |
| U004 | `appface.dll` UPX 壳已用 Unicorn 脱壳成功（UPX0 450,560 B 解压） | UNICORN | p12 | CLOSED | ME | — |
| U005 | `.urf` 前 10 字节共同前缀 = `a1 a0 ae ab bc a8 aa a8 a0 a8` | BYTES | p13 | CLOSED | ME | — |
| U006 | `.urf` 是**明文容器**（非加密）：内嵌明文 GIF89a，熵 7.962 来自 GIF LZW | BYTES | p13 | CLOSED | ME | RE#28b |
| U007 | `1.urf` 内 GIF 位于 0x6ccb、0x7a15；`_urf_images/` 12 张 GIF 可复现 | BYTES | p13 | CLOSED | ME | RE#28b |
| U008 | `.urf` 元数据区含 279 个 0xa8 分隔符（0x0a→0x6ccb 之间） | BYTES | p13 | CLOSED | ME | RE#28b |
| U009 | 前 10 字节头最高位全为 1（≥0x80，非明文魔数） | BYTES | p13 | CLOSED | ME | — |

## 八、策略数据流（P0 成果，本轮新增）

| id | fact | method | evidence | status | src | claim_id |
|---|---|---|---|---|---|---|
| S001 | `sys.dat` 无任何 PE 模块静态读（名只在构建清单） | STR | p15 | CLOSED | ME | — |
| S002 | `xconfigs.Dat`（大写D）在 XNet2 0x68f94、Surfgd 0x17890(.uinitda) | STR | p15 | CLOSED | ME | — |
| S003 | `xconfigs.dat` 是 XNet2 写出的（WriteFileBin + "wb" + 268B） | DISASM | p15 | CLOSED | ME | — |
| S004 | `sysEx.dat` 是 XNet2 写出的（WriteFileBin + "wb" + 36B） | DISASM | p15 | CLOSED | ME | — |
| S005 | 策略数组通过 SG_Control(0x262) 拷贝到 SurfGd 全局 0x10228008 | DISASM | p15 | CLOSED | ME | — |
| S006 | `下标 = 策略号 - 1` 映射成立（XNet2 0x40bbd0 初始化代码） | DISASM | p15 | CLOSED | ME | — |
| S007 | SG_Control 是命令分发器：字节表@0x7d18 + 跳转表@0x7ce0 | DISASM | p15 | CLOSED | ME | — |

## 九、驱动层（P1 成果，本轮新增）

| id | fact | method | evidence | status | src | claim_id |
|---|---|---|---|---|---|---|
| D001 | `IDR_DRIVER_SYS.bin`/`NT4.bin` 是 native PE 驱动（subsystem=NATIVE），`VXD.bin` 是 VxD(LE) | PE-HDR | 本轮 | CLOSED | ME | RE#30b |
| D002 | 驱动名 = `mgtaki`（PDB: emp\Taki\Free\mgtaki.pdb / Takint4） | STR | 本轮 | CLOSED | ME | RE#30b |
| D003 | 导入完整 Zw* 注册表系列 + IoCreateDevice + IoRegisterShutdownNotification | IMP | 本轮 | CLOSED | ME | RE#30b |
| D004 | 驱动内无 urf/skin 字符串（与 .urf 无关） | STR | 本轮 | CLOSED | ME | RE#30b |
| D005 | VxD 版含 "VtoolsD"（Vireo VxD 工具库） | STR | 本轮 | CLOSED | ME | — |

## 十、SG_Control 控制协议（P2 成果，本轮新增）

| id | fact | method | evidence | status | src | claim_id |
|---|---|---|---|---|---|---|
| G001 | SG_Control 是 XNet2↔SurfGd 核心控制协议（命令码分发，55 个实现命令） | DISASM | p16 | CLOSED | ME | — |
| G002 | 命令码 0x262 = 接收 67 dword 策略数组到 0x10228008 | DISASM | p15/p16 | CLOSED | ME | — |
| G003 | 三个 144 字节记录数组：A@0x1021bf18, B@0x102186d8, C@0x1003db80 | DISASM | p16 | CLOSED | ME | — |
| G004 | 记录首字段是字符串（strcmp 匹配 + 小写化 0x14076） | DISASM | p16 | CLOSED | ME | — |
| G005 | 命令码 0x32 = 单条添加记录（上限 0x2af8=11000 条） | DISASM | p16 | CLOSED | ME | — |
| G006 | XNet2 实际使用 16 处调用，覆盖约 12 个命令码 | BYTESCAN | p16 | CLOSED | ME | — |
| G007 | 全局标志区 0x1022800c..0x10228034（单字节配置标志） | DISASM | p16 | CLOSED | ME | — |

## 十一、网络过滤层 DBFP_*（P3 成果，本轮新增）

| id | fact | method | evidence | status | src | claim_id |
|---|---|---|---|---|---|---|
| N001 | DBFP_CloseSocket/Accept 是纯空操作（返回1），Connect 基本空操作 | DISASM | p17 | CLOSED | ME | — |
| N002 | 过滤总开关 = 0x1022800c（==2 时全放行） | DISASM | p17 | CLOSED | ME | — |
| N003 | 拦截方式 = 返回0 + 设置错误码 0x2746(10054=WSAECONNRESET) | DISASM | p17 | CLOSED | ME | — |
| N004 | 过滤层次：进程名→IP/端口→协议指纹→URL/域名→内容URL提取（详见 p18） | DISASM | p17/p18 | CLOSED | ME | — |
| N005 | 进程判定用 Toolhelp（CreateToolhelp32Snapshot+Process32First/Next+GetCurrentProcessId） | STR+DISASM | p17 | CLOSED | ME | — |
| N006 | IP 表每项 7 字节（ip4+port2+flag1）@0x1022806c | DISASM | p17 | CLOSED | ME | — |
| N007 | 进程名/URL 名单 @0x102221f0（小写化 strcmp 匹配） | DISASM | p17 | CLOSED | ME | — |

## 十二、内容级过滤（P4 成果，本轮新增）

| id | fact | method | evidence | status | src | claim_id |
|---|---|---|---|---|---|---|
| C001 | DBFP_Send 做内容级 URL 过滤（非仅 IP/端口） | DISASM | p18 | CLOSED | ME | — |
| C002 | 内容URL提取：strncmp(data+4, "http://"/"https:/", 7) | DISASM | p18 | CLOSED | ME | — |
| C003 | 连接跟踪表 0x10015200（32槽×16B，socket+标志） | DISASM | p18 | CLOSED | ME | — |
| C004 | 协议头字符串（http://等）由 XNet2 下发，XNet2 侧确有 | STR | p18 | CLOSED | ME | — |
| C005 | 名单A匹配用 strstr（0x1000a8a0）遍历 144B 记录 | DISASM | p18 | CLOSED | ME | — |
| C006 | 端口/协议指纹：strncmp(data+4, 0x102225d4, 4) | DISASM | p18 | CLOSED | ME | — |

## 十三、入站内容过滤（P5 成果，本轮新增）

| id | fact | method | evidence | status | src | claim_id |
|---|---|---|---|---|---|---|
| R001 | DBFP_Recv 做关键词内容过滤（HTTP 响应体） | DISASM | p19 | CLOSED | ME | — |
| R002 | HTTP 端口集：{80,81,8080,8081,8888,3128} | DISASM | p19 | CLOSED | ME | — |
| R003 | 名单C(0x1003db84)是入站关键词黑名单 | DISASM | p19 | CLOSED | ME | — |
| R004 | 关键词记录 144B 含 '[' 分隔符（分类/多词） | DISASM | p19 | CLOSED | ME | — |
| R005 | 拦截方式：memcpy 替换响应内容（非断连） | DISASM | p19 | CLOSED | ME | — |
| R006 | 出站=返回0+10054，入站=替换响应（方向差异） | DISASM | p18/p19 | CLOSED | ME | — |

## 十四、名单B语义（P6 成果，本轮新增）

| id | fact | method | evidence | status | src | claim_id |
|---|---|---|---|---|---|---|
| LB001 | 名单B(0x102186d8) = 监控目标进程名单 | DISASM | p20 | CLOSED | ME | — |
| LB002 | 进程匹配：0x10003499 遍历Toolhelp快照，进程名小写化 strstr 名单B | DISASM | p20 | CLOSED | ME | — |
| LB003 | 命中结果存 PID 数组 0x10218548（100×4B）+ 计数 0x10060e00 | DISASM | p20 | CLOSED | ME | — |
| LB004 | 0x10003340 判定当前进程PID是否在命中结果中 | DISASM | p20 | CLOSED | ME | — |
| LB005 | 名单语义：A=出站URL，B=监控进程，C=入站关键词（D=动态，见 C-022） | DISASM | p18/p19/p20/p24 | CLOSED | ME | — |

## 十五、关键词记录结构（P7 成果，本轮新增）

| id | fact | method | evidence | status | src | claim_id |
|---|---|---|---|---|---|---|
| K001 | 144B 记录 = 规则字符串(+0) + 有效标志(+0x84) | DISASM | p21 | CLOSED | ME | — |
| K002 | 规则语法：{}组(AND)、[]字符类(OR)、,词分隔 | DISASM | p21 | CLOSED | ME | — |
| K003 | 容量：A=100(0x64@0x2d34)、C=1000(0x3e8@0x1acd)、D=1500(0x5dc@0x27b5)、B无显式常量 | DISASM | p21 | CLOSED | ME | — |
| K003b | **更正**：初版把 A 记作 1500（实为 D 的容量），B 记作 100 | DISASM | p21 | CLOSED | ME | — |
| K004 | 单条添加上限11000（命令码0x32） | DISASM | p21 | CLOSED | ME | — |
| K005 | 匹配引擎5组嵌套，strstr 匹配，全组命中才返回1 | DISASM | p21 | CLOSED | ME | — |

## 十六、图像过滤（P8 成果，本轮新增）

| id | fact | method | evidence | status | src | claim_id |
|---|---|---|---|---|---|---|
| IMG01 | xnet2 → CImage.dll 3函数：CAPSCREEN/CHECKEX2/DES2GO | IMP | p22 | CLOSED | ME | — |
| IMG02 | CHECKEX2 魔法数 0x7ab7(31415)，错则 Sleep(10s)+返回100 | DISASM | p22 | CLOSED | ME | — |
| IMG03 | 核心检测 0x10004a60 有 3 种模式（不同浮点阈值） | DISASM | p22 | CLOSED | ME | — |
| IMG04 | 颜色直方图 0x10008e70（256桶，RGB步进3） | DISASM | p22 | CLOSED | ME | — |
| IMG05 | 宽高比检查 0x10008e20 | DISASM | p22 | CLOSED | ME | — |
| IMG06 | FImage.dll 引用 XFimage.xml（haarcascade_frontalface_alt2） | STR | p22 | CLOSED | ME | — |
| IMG07 | CImage.dll 用 OpenCV 轮廓/形态学/霍夫线全套 | IMP | p22 + **p26** | CLOSED | ME | p26 补证：非死导入，17 个 CHECK* 经 2-3 层调用可达 |
| IMG08 | CImage.dll 有 17 个 CHECK* 导出（非仅 CHECKEX2） | PE-HDR | **p26** | CLOSED | ME | 导出表实测，编号 1–8、10–13，无 9 |
| IMG09 | 15 个 CHECK* 函数 3 层内可达 OpenCV 桩 | DISASM | **p26** | CLOSED | ME | capstone 精确反汇编 + BFS |
| IMG10 | 主图像判定在 CHECKEX8x / CHECKEX13x / CAPSCREEN | DISASM | p26 | CLOSED | ME | 三处规模 845/1363/1353 条指令 |
| IMG18 | `CHECKEX13x` 主判定 = `0x100078e0`，逻辑已还原 | DISASM | **p27** | CLOSED | ME | 门控/双比值/分支/返回码全还原 |
| IMG19 | 灰度权重 0.11/0.30/0.59 + 0.5（BGR 序） | BYTES | **p27** | CLOSED | ME | `0xd678`/`0xd670`/`0xd668`/`0xd660` |
| IMG20 | 中心 8×8 窗口标准差阈值 1.4 | BYTES | **p27** | CLOSED | ME | `0xd658`，`0x10002790` 是总体标准差 |
| IMG21 | 8 档纹理分级边界 20/40/75/155/190/270/295/316 | BYTES | **p27** | CLOSED | ME | `0x100027e0` 分支链实测 |
| IMG22 | 分级结果按 `9*g1+3*g2+g3` 打包（0–71） | DISASM | **p27** | CLOSED | ME | `0x10002955` lea 链 |
| IMG23 | `0x10005150` 与 `0x10008e70` 是同一算法的两份拷贝 | DISASM | **p27** | CLOSED | ME | 同用 `0xd518`/`0xd540`/210 分界 |
| IMG24 | `CHECKEX13x` 不调用轮廓/霍夫，那是别的 CHECKEX* | DISASM | **p27** | CLOSED | ME | 推翻 p22 的合并表述 |
| IMG12 | `0x10001ba0`（ratio1≥0.4 分支）语义 | — | — | **OPEN** | — | 决定分支 A 行为 |
| IMG13 | 返回 `0x64` 在正常路径的含义 | — | — | **OPEN** | — | 与反调试返回值同值，易混淆 |
| IMG14 | 8 项权重表 `0x1000d110` 的来源 | — | — | **OPEN** | — | 无字符串/配置引用 |
| IMG15 | `ratio1`/`ratio2` 的物理含义 | — | — | **OPEN** | — | 公式未完全推导 |
| IMG11 | 其余 16 个 CHECKEX 的参数契约与返回码 | — | — | **OPEN** | — | 仅 CHECKEX2 已逆向（首参 0x7ab7） |

## 十七、名单D与查询命令（P10 成果，本轮新增）

| id | fact | method | evidence | status | src | claim_id |
|---|---|---|---|---|---|---|
| LD01 | 第4名单D @0x10060e0c，容量1500，计数0x1022812c | DISASM | p24 | CLOSED | ME | — |
| LD02 | 名单D有完整增删接口（添加@0x279b去重，删除@0x2896 memmove） | DISASM | p24 | CLOSED | ME | — |
| LD03 | 命令码0xfa0 = 查询名单D命中（strstr，0=命中） | DISASM | p24 | CLOSED | ME | — |
| LD04 | 命令码0xbc2 = strlen（标准优化实现0x7efefeff） | DISASM | p24 | CLOSED | ME | — |
| LD05 | XNet2按状态码100/300查询0xfa0/0xfa1 | DISASM | p24 | CLOSED | ME | — |
| LD06 | 完整名单体系 = 4个（A/B/C/D） | DISASM | p16/p20/p24 | CLOSED | ME | — |

## 十八、UI照片/手册交叉验证（P11 成果，本轮新增）

| id | fact | method | evidence | status | src | claim_id |
|---|---|---|---|---|---|---|
| UI01 | 手册16项功能点全部能映射到逆向机制路径，无缺口 | CROSSCHECK | p25 | CLOSED | ME | — |
| UI02 | "灵敏度低/中/高"UI项 = 0x10004a60 的3种检测模式 | CROSSCHECK | p25 | CLOSED | ME | — |
| UI03 | "自动增加黑名单"UI项 = 名单D运行时添加接口 | CROSSCHECK | p25 | CLOSED | ME | — |
| UI04 | image.dat(HTTP200+60x100GIF) = 网页小图替换源；大提示窗=gn.exe弹窗 | CROSSCHECK | p25 | CLOSED | ME | — |
