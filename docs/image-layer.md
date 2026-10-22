# M4 图像层（已实现：两个实测函数）

`src/green_dam/imaging.py`。测试 `tests/test_imaging.py`，27 项。

## 1. 先说清楚范围

复刻的是 `CImage.dll` 里**能逐字节读懂逻辑**的两个函数。**不含 Haar 级联
分类器**——那是 `FImage.dll` 背后由 OpenCV 运行时加载的训练模型，不是逻辑。
详见第 5 节。

## 2. 宽高比检查（`0x10008e20`）

```
if width < 32 or height < 32:   return True    # 太小，不判
return max(w,h) / min(w,h) < 10.0
```

| 常量 | 地址 | 值 | 字节 |
|---|---|---|---|
| 最小边 | 硬编码 `cmp ...,0x20` | 32 | — |
| 比例阈值 | `[0x1000dc48]` | **10.0** | `0000000000002440` |

原厂先比较宽高再相除（`0x8e32`–`0x8e44`），保证长边/短边。这里照做。

## 3. 高亮度检查（`0x10008e70`）

```
hist[256] = 0.0
for each pixel:
    hist[R] += 1.0          # R = ptr+2，步进 3（BGR）
normalise by width*height
high = sum(hist[210..255])
low  = sum(hist[0..209])
return high / (low + high)
```

| 常量 | 地址 | 值 |
|---|---|---|
| 累加步长 | `[0x1000d518]` | **1.0** |
| 分段边界 | 硬编码 `0xd2`/`0x2e` | 210 / 46 桶 |
| 判定阈值 | `[0x1000d540]` | **0.9** |

### 这个检查不是肤色检测

本仓库早先把 `0x10008e70` 记作"肤色占比"，**是错的**。实测：它把**最亮的
R 桶**（≥210）求和，占比 ≥ 0.9 时命中。这是**过曝 / 近白屏检测**。

`analysis/probe/p22` 已按实测更正，并删掉了原先"这些是 Haar 参数"的推断
（未能在代码中找到依据）。

## 4. 三者独立，任一命中即可疑

| 步骤 | 判据 | 状态 |
|---|---|---|
| 宽高比 | 长/短 ≥ 10.0 | DONE |
| 高亮度 | R≥210 占比 ≥ 0.9 | DONE |
| Haar 人脸 | `XFimage.xml` | **未做**（见下） |

## 5. 为什么不做 Haar

`FImage.dll` 通过 OpenCV 运行时加载 `XFimage.xml`（`haarcascade_frontalface_alt2`
的衍生版）。那是**训练出来的模型**：复刻它等于复刻 OpenCV 的级联求值器加
模型权重，是另一个工程，不是读两个算术函数。

**选择：明确缺失，而不是留空壳。**

`ScreenResult.checks_run` 列出**实际执行了哪些检查**，`fully_screened` 只有
在两个检查都真跑过时才为真。这样调用方无法把"两项通过"误读成"图像干净"。

原厂返回一个裸 `int`，"干净"和"没查全"无法区分——这和事件日志里
`gated` vs `allow` 是同一个失败模式。

## 6. 策略开关

图像过滤受两个槽位控制（`sysdat`）：

| 槽 | 名称 | 效果 |
|---|---|---|
| 2 | `disable_filtering` | 总开关，关闭则一律不筛 |
| 13 | `enable_image_filter` | 图像过滤开关 |
| 5 | `image_filter_level` | 级别（低/中/高） |

**出厂数据里槽 13 = 0**，所以默认是关闭的。`Filter.check_image()` 会返回
`detail="image_filter_disabled"` 并记一条 `gated` 事件——**不是**报告"干净"。

这也修正了一处早期误读：`status` 显示 `image filter: False (level 2)` 是
正确的（级别设了"中"，但总开关没开），不是 bug。

## 7. 与策略层的接线

`ScreenResult` **不携带动作**。是否把可疑帧变成 block/warn/log 是策略，不是
测量。所以：

```python
result = f.check_image(width=..., height=..., red_channel=...)
assessment = f.check(image=result, image_action="block")
```

`image_action=None`（默认）时，帧被记录为**证据**但无动作——与词表命中
"有证据、无动作"的处理一致（原厂 分→动作 映射未还原）。

`assess()` 的优先级：网络阻断 > 其余动作中较强的一个（`ACTION_STRENGTH`）；
平手时保留文本（词表命中指名了具体东西，图像只给了个指标）。

## 8. CLI

```bash
$ green-dam screen --width 1000 --height 32 --red 255
suspect        : True
detail         : aspect
fully screened : True
fired          : ['aspect', 'high_tone']
aspect_ratio   : 31.2500
high_tone_ratio: 1.0000
```

`--red` 用一个值填满 R 平面；省略则跳过亮度检查（`fully screened: False`）。
**不读任何图片文件**——只用尺寸和合成平面，所以测试完全确定、无需素材。

## 9. 验收（27 项）

| 组 | 覆盖 |
|---|---|
| 常量 | 32 / 10.0 / 210 / 0.9 全部钉住 |
| 宽高比 | 长边除短边、零尺寸报错、小图不判、正常通过、细长失败（边界 10.0） |
| 高亮度 | 全亮/全暗/边界 210/一半/短缓冲/零像素 |
| BGR 提取 | 取第三字节、起始偏移、短缓冲 |
| 筛选 | 小图非可疑但非完整、缺 R 平面为 partial、干净、单项命中、双项命中、指标只含两个比值、渐变低于阈值 |

## 10. 未做

| 项 | 说明 |
|---|---|
| Haar 级联 | 训练模型，非逻辑；见第 5 节 |
| 轮廓/形态学/霍夫线 | `CImage.dll` 确实 import 了 `cvFindContours` 等，但未找到调用它们判定"不良"的路径 |
| 抓屏（`CAPSCREEN`） | 属 M5/系统集成，需平台 API |
| 真实图片解码 | 本模块吃已解码的平面数据；解码器是传输层/输入层的事 |
