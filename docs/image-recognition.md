# 图像识别：原厂真在做，代码在这里

这一层长期被我错误地归为“可以不做的”。事实是它**明确接在调用链上**，
而且判定逻辑可以从反汇编里读出来。本文记录读出来的部分。

## 1. 调用路径是确凿的

`XNet2.exe` 的静态导入表里有 `CImage.dll`：

| 函数 | 用途 |
|---|---|
| `CAPSCREEN` | 截屏 |
| `CHECKEX2` | 判定 |
| `DES2GO` | 区域描述 |

这不是“可能被调用”，是**链接期确定的导入**。同时 `CImage.dll`
导入 `FImage.dll` 的 `FaceDectect`/`FaceDectect3`/`FaceDectect4`，
`FImage.dll` 再导入 OpenCV 的 `cvHaarDetectObjects`
（Haar 级联人脸检测器）。

```
XNet2.exe
  └─ CImage.dll ── CHECKEX2 / CAPSCREEN / DES2GO      Windows       ├─ FImage.dll ── FaceDectect* ── cvHaarDetectObjects
       ├─ Xcv.dll   ── cvCanny / cvFindContours / cvDilate…
       ├─ xcore.dll ── cvCreateImage / cvGetSize / cvCopy…
       └─ Xtool.dll ── cvLoadImage
```

注意目录：`CImage.dll` 在 `Windows\`，其余四个在 `Windows\System32\`。

`CImage.dll` 导入的 OpenCV 函数全集（从 `.rdata` 的导入名表读出）：

```
cvLoadImage        cvCreateImage    cvReleaseImage   cvGetSize
cvCopy             cvSetZero        cvNot            cvSub
cvLine             cvDrawContours   cvGetSeqElem     cvFindContours
cvApproxPoly       cvContourArea    cvArcLength      cvBoundingRect
cvMoments          cvHoughLines2    cvGetQuadrangleSubPix
cvCanny            cvCvtColor       cvResize         cvErode
cvDilate           cvMorphologyEx   cvCreateMemStorage
cvReleaseMemStorage cvClearMemStorage
```

这套组合是**肤色分割 + 边缘/轮廓 + 人脸检测**，不是简单的“看图有肉色”。

## 2. `CAPSCREEN`：真的抓全屏

`CAPSCREEN` @ `0x10004970` 本身只是壳，它调用内部例程 `0x100048e0`：

```
sub  esp, 0x230
call 0x100048e0              ; 内部截图例程
...
push 0xcc0020                ; SRCCOPY
push 0
push 0
push esi                     ; hdcSrc
push ebp                     ; height
push ebx                     ; width
push 0
push 0                       ; x, y
push edi                     ; hdcDst
call [0x1000c024]            ; BitBlt
```

`SRCCOPY`（`0xcc0020`）出现在**内部例程 `0x100048e0`** @ `0x10004926`，
`CAPSCREEN` 只在 `0x10004988` 处 `call 0x100048e0`。所以这一层是**把屏幕像素原样拷进
内存位图**，然后交给判定函数。不是读取窗口标题，不是钩键盘——是看像素。

`CAPSCREEN` 在 `0x100049a0` 附近还调用了 `0x1000c03c`，参数
`push 0x80` + 栈上 512 字节缓冲，形如取系统目录/临时目录路径，
用于落盘或传递文件名。

## 3. `CHECKEX*`：返回值语义 + 魔法值

所有 `CHECKEX2` … `CHECKEX13x` 的入口形状一致：

```
cmp  dword ptr [esp + N], 0x7ab7     ; 31415
je   <continue>
...
mov  eax, 0x64                       ; 100
ret  0xc
```

- `0x7ab7` = **31415**，一个魔数，参数校验用（不匹配直接返回）。
- 校验失败返回 **`0x64` = 100**。
- 判定成功路径返回 **`0x32` = 50**（`CHECKEX2` @ `0x10005a88`）。
- 其余分支返回 0 或 1（`0x10004c02` 处 `mov eax, 1`）。

所以返回值是**分档的**，`100` 与 `50` 是两个不同的结论档位，
不是布尔值。这一点对复刻很重要：不能把非零一律当作“命中”。

## 4. `0x10004a60`：肤色像素占比阈值

`CHECKEX2` 在参数校验通过后调 `0x10004a60`，那里是真正的判定。
它按一个模式参数选三组阈值对，每组是**两个双精度浮点数**：

| 模式参数 | 下阈 | 上阈 | 另一阈值 |
|---|---|---|---|
| `1` | `0.12` | `0.30` | `0.50` |
| `3` | `0.06` | `0.16` | `0.57` |
| 其它 | `0.09` | `0.20` | `0.54` |

常量以 `BYTES_LE` 双精度存在指令流里，例如模式 1 的
`0xeb851eb8 / 0x3fbeb851` 解出来就是 `0.12`。

判定流程：

```
call 0x10008de0          ; 构造区间对象（下阈, 上阈）
call 0x10008e20          ; 若失败 -> 返回 0
call 0x10008e70          ; 若失败 -> 返回 0
call 0x10008f60          ; 取实测比例
fcom   [esp+0x1c]        ; 与下阈比较
test   ah, 0x41 / je     ; 低于下阈 -> 返回 0
fcomp  [esp+0x24]        ; 与上阈比较
test   ah, 1   / jne     ; 高于上阈 -> 返回 1
...
call 0x100094f0          ; 区间内 -> 再做一次加权评估
push 0xfc                ; 容量 0xfc，用满 248 字节
push 0x10010020          ; 表地址
```

`push 0xfc` 表示可用 248 字节（`0xf8`）；`0x10010020` 处是一张
**31 个双精度系数**的表，取值集中在 `0.0`–`1.0` 与少量负值（`-0.79`、`-0.71`、
`-0.16` …）。这是颜色通道的线性判别权重。

**结论**：这一层的算法是
「统计画面中落在肤色区间内的像素比例 → 与上下阈比较 → 区间内再用
加权系数做细分评估」。三档阈值对应三档严格程度。

## 5. `DES2GO`：区域描述

`0x10005ae0`，`ret 0x18` 即 **6 个参数**，本地栈帧 `0x720` 字节：

```
sub  esp, 0x720
call 0x10008580
; 从栈上依次取 6 个参数
call 0x10008000          ; 构造描述对象
call 0x100085b0
```

`DES2GO` 名字含义未证实。可确定的只是：它收 6 个参数、
用 `0x720` 字节工作区、调用一个构造例程和一个收尾例程，
返回后立即 `xor eax, eax`（恒返回 0）。**它的具体语义我没有证据，
不要凭名字猜。**

## 6. 已知与未知

### 已证实

| 事实 | 依据 |
|---|---|
| 截屏用的是 `BitBlt(SRCCOPY)` | `0xcc0020` @ `CAPSCREEN` |
| 判定阈值是三档肤色比例区间 | 常量 `0.12/0.30`、`0.06/0.16`、`0.09/0.20` |
| 有 31 项颜色权重表 | `push 0xfc` + `0x10010020`（248 字节） |
| 有人脸检测 | `cvHaarDetectObjects` + `FaceDectect*` |
| 返回值分 0/1/50/100 四档 | 各分支 `mov eax` 常量 |
| 参数魔数 31415 | 所有 `CHECKEX*` 的 `cmp …, 0x7ab7` |

### 未证实

- `0x7ab7` 这个魔数的来源和含义；
- `100` / `50` 分别对应什么处置动作（拦截？告警？）；
- `DES2GO` 的语义；
- `CHECKEX3`–`CHECKEX13x` 与 `CHECKEX2` 的具体差异
  （已知它们参数个数与帧大小不同，但阈值表是否相同未逐一对账）；
- 判定结果回传给 `XNet2.exe` 之后做什么。

## 7. 分类器数据是齐的

`FImage.dll` 里 `cvHaarDetectObjects` 旁边就是字符串 `XFimage.xml`，
而这个文件**确实随产品发出**：

`Windows\System32\XFimage.xml`，858,866 字节。

实测结构：

| 项 | 值 |
|---|---|
| 声明 | `<haarcascade_frontalface_alt2 type_id="opencv-haar-classifier">` |
| 窗口 | `20 20` |
| stage 数 | 20 |
| tree 数 | 1,047 |
| tilted 特征 | 0（全为 0） |

这是 OpenCV 自带的 `haarcascade_frontalface_alt2` 正脸检测器，
数据完整、无裁剪。**人脸检测这一环不缺任何依赖。**

## 8. 复刻代价评估

不再是“缺依赖”，而是“工作量”：

| 环节 | 状态 |
|---|---|
| 人脸检测 | ✅ 分类器齐全（`XFimage.xml`） |
| 肤色分割 + 阈值 | ✅ 三档阈值已解（0.12/0.30 等） |
| 颜色权重表 | ⚠️ 位置与长度已确认（31 项 @ `0x10010020`），未逐项对账语义 |
| 图像解码 | ⚠️ 原厂用 `cximage`；可用 Pillow 替代 |
| OpenCV 算子 | ⚠️ 需等价实现（`cvCvtColor`/`cvCanny`/`cvFindContours`…） |
| 返回值 → 处置动作 | ❌ 未解（100 与 50 分别代表什么） |

诚实结论：**这一层是可以复刻的**，主要成本在 OpenCV 算子的等价实现
和权重表语义核对上，不在数据缺失。比起 HNC 句类分析（数据完全没有），
图像识别的数据条件是**所有未复刻模块里最好的**。

先把权重表和返回值语义解出来，再决定用 OpenCV 绑定还是纯 Python 实现。
