# 图像后端接口（M4 / F4）

`src/green_dam/imagepath.py`。测试 `test_imagepath.py` 41 项 +
`test_cli.py::ImageCliTest` 5 项。

## 1. 为什么是接口而不是实现

契约测试 `DependencyTest` 禁止引擎导入 `numpy`/`cv2`/`onnxruntime`/`torch`，
架构 §A2 要求核心纯标准库。

**这不是 M4 的障碍，而是 M4 被规定的形态**：引擎决定"拿到判定后怎么做"，
产生判定的模型由宿主提供。

```
M4 ──► ImageBackend.classify(buffer, w, h) -> ImageVerdict
         ├── ReferenceBackend   — 零依赖，只做算术检查
         └── （宿主）OnnxBackend — 满足同一 Protocol，引擎永不导入
```

与 M3 的 `SemanticBackend` 是同一模式。

## 2. 核心设计：`assessed` 是独立字段

```python
@dataclass(frozen=True)
class ImageVerdict:
    nsfw: float | None      # None = 没有模型评估过
    assessed: bool          # 是否有模型跑过
    flagged: bool
    reason: str
```

**为什么 `nsfw=None` 不够**：模型失败时也可能返回 `None`，而"没模型"
和"模型失败"要导致不同的决策。

**为什么不能用 `nsfw=0.0` 表示"没评估"**：那正是本项目反复避免的错误——
把"没看"报成"看了没问题"。

CLI 直接体现这个区别：

```
$ green-dam image --r 100
assessed    : False
nsfw        : -
reason      : not_assessed

$ green-dam image --score 0.8
assessed    : True
nsfw        : 0.8
flagged     : True
reason      : above_threshold
```

## 3. `ReferenceBackend` 明确不做什么

它只跑已移植的算术检查（`imaging`/`imaging_checkex`/`imaging_skin`），
**永远返回 `nsfw=None`**。

理由：那些检查**不是 NSFW 分类器**。从中编一个 0–1 的分数是虚假声明。

测试 `test_never_claims_an_nsfw_score` 钉住这一点。

## 4. 阈值与分数

```python
DEFAULT_NSFW_THRESHOLD = 0.5
```

`verdict_from_score()` 是**唯一**的阈值逻辑，宿主后端调用它，避免每个后端
各写一遍。分数越界（<0 或 >1）抛 `ValueError` 而不是夹取——静默夹取会
掩盖后端的 bug。

## 5. `ClassifierChain`：保守合并

| 情形 | 结果 |
|---|---|
| 任一后端 `flagged` | **立即返回 flagged**（阳性声明不需佐证） |
| 全部未评估 | **未评估**（不是"干净"） |
| 有评估但都不 flag | 报最高分，`below_threshold` |

**关键**：无法评估的后端**贡献 0，而不是 0.0 分**。否则一个坏后端会让整条链
报"干净"。

`metrics` 按后端名加前缀（`reference.skin_ratio`），两个后端报同名键不会
互相覆盖——这是写测试时发现的真 bug（原先 `Chain` 重建 verdict 时丢掉了
后端的 metrics）。

## 6. `Filter` 接线

```python
Filter(image_backend=...).check_image_verdict(buffer, width=..., height=...)
```

与 `check_image()` 分开：

| 方法 | 返回 | 语义 |
|---|---|---|
| `check_image` | `ScreenResult` | **测量**（算术检查跑了什么） |
| `check_image_verdict` | `ImageVerdict` | **判定**（可能含模型分数） |

无后端时返回 `reason="no_model"`，不是干净结果。
门控关闭时返回门控原因（`image_filter_disabled`），与 `no_model` 可区分。

## 7. 验收（46 项）

| 组 | 覆盖 |
|---|---|
| Protocol | 假后端/参考后端/不评估后端都满足 |
| 分数→判定 | 阈值、含边界、`None` 不等于 0、越界拒绝、自定义阈值 |
| 参考后端 | **永不声称 NSFW 分数**、报告算术检查、skin 默认关、短缓冲报错 |
| R 平面 | BGR 提取正确、短缓冲报错 |
| 高亮度 | 常量、全白/全暗、比例、空平面 |
| 链 | 空链=no_model、先 flag 者胜、全清报最高、**不评估不等于干净**、metrics 合并 |
| 日志安全 | 摘要无像素值、JSON 友好、None 存活 |
| **依赖边界** | 源码不含任何第三方 import、宿主后端可注入 |
| Filter | 无后端=no_model、分数到达判定、门控关闭、门控原因可区分 |
| CLI | 未评估/flag/clean/skin/JSON |

## 8. 未做

| 项 | 说明 |
|---|---|
| ~~真实 ONNX 后端~~ | **已做**：`host/imaging/`，见附录 |
| ~~模型选择与授权~~ | **已定**：Marqo 384 ONNX，Apache-2.0，见附录 |
| 人脸检测（Haar） | 训练模型，非逻辑（`image-layer.md` §5.1） |
| 原厂轮廓判定复刻 | 架构目标是分类器而非复刻（`image-layer.md` §5.2） |
| 图像解码 | 引擎吃已解码 BGR；解码属输入层 |

---

# 附录：宿主侧实现（H5）

以下记录 `host/imaging/` 的实现决策与实测数据。引擎侧接口设计见上文。

## 1. 候选筛选（2026-10 实测）

筛选门槛，按顺序，任何一条不过就排除：

1. 许可允许再分发（排除 CC-BY-NC、AGPL、OpenRAIL）
2. 权重可离线分发（不能是 API）
3. 体积可接受（部署包基线 61 MB）
4. CPU 延迟支持实时（屏幕监控 1 fps）
5. 输入输出签名标准（ONNX 可直接跑，无需导出）

| 模型 | 许可 | 权重 | 结论 |
|---|---|---|---|
| Falconsai/nsfw_image_detection | Apache-2.0 | **327 MB** | 体积不可接受 |
| AdamCodd/vit-base-nsfw-detector | Apache-2.0 | 328 MB（int8: 84 MB） | 体积不可接受 |
| Freepik/nsfw_image_detector | MIT | 165 MB | 体积不可接受 |
| giacomoarienti/nsfw-classifier | **CC-BY-NC-ND-4.0** | — | **许可排除**（禁商用、禁改） |
| NudeNet v3.4 (320n.onnx) | **AGPL-3.0** | 11.6 MB | **许可排除**（传染性） |
| taufiqdp/mobilenetv4…nsfw | Apache-2.0 | 9.5 MB | 输入 rank-3 NHWC、5 类，接口非标准 |
| **Marqo/nsfw-image-detection-384** | **Apache-2.0** | **21.4 MB** | ✅ **采用** |

**采用的形式**：`KanariKanaru/nsfw-image-detection-384-onnx`，Marqo 权重的 ONNX
导出（opset 17，float32），许可继承 Apache-2.0。该仓库声明"format conversion,
not a newly trained model"，链条干净。

---

## 2. 实测数字

### 2.1 精度（真实图片，非合成）

| 图片 | NSFW 概率 |
|---|---|
| beignets-task-guide.png（食物） | 0.058 |
| pipeline-cat-chonk.jpeg（猫） | 0.059 |
| coco_sample.png（场景） | 0.045 |

### 2.2 变换鲁棒性（同一张真实照片）

这是原厂 HSV 方案完全缺失的性质。

| 变换 | NSFW 概率 | 变化 |
|---|---|---|
| 原始 | 0.060 | — |
| 灰度 | 0.078 | +0.018 |
| 亮度 40% | 0.072 | +0.012 |
| 亮度 160% | 0.055 | −0.005 |
| 高斯模糊 σ=3 | 0.078 | +0.018 |
| 缩到 64×64 再放大 | 0.066 | +0.006 |
| 竖版裁切 | 0.057 | −0.003 |

**全部远低于 0.5 阈值，且波动 <0.02。** 原厂的 `R>G>B` 判据在灰度图上永远
不触发（`R=G=B`），这是它最严重的缺陷。

### 2.3 延迟

| 阶段 | 耗时 |
|---|---|
| 预处理（裁切 + 双线性缩放 + 归一化） | 28 ms |
| 推理（ViT-tiny 384²） | 60 ms |
| **端到端（含 softmax）** | **中位数 90 ms**（min 78 / max 129，n=30） |

> **修正记录**：早期记录的 37 ms 是**裸推理 + 随机噪声输入**的数字，不含预处理，
> 也不代表真实图片。真实端到端是 90 ms。1 fps 采样下 CPU 占用约 9%。

`ascontiguousarray` 是必需的：非连续视图会让 onnxruntime 每次调用复制一遍，
实测 72 ms vs 60 ms。

---

## 3. 限制一：分布外输入（OOD）

**这是实测发现的真实缺陷，不是理论担忧。**

纯色帧不在训练分布内，模型会给出无意义的分数：

| 输入 | NSFW 概率 | 超过 0.5 阈值？ |
|---|---|---|
| 纯暖肤色 (224,172,140) | **0.543** | **是** |
| 纯黑 | 0.359 | 否 |
| 纯白 | 0.204 | 否 |
| 中棕 (120,85,65) | 0.382 | 否 |

**暖肤色纯色会被误判为 NSFW。** 一张纯色背景图、一个加载中的占位符、一张
被遮罩的图片，都可能触发。

### 修法：空间方差前置检查

```python
spatial = frame.mean(axis=2).std()   # 先压掉颜色通道，再算像素间方差
if spatial < 4.0:
    raise UnusableFrame(...)         # 不评分
```

**为什么是空间方差而不是通道方差**：暖肤色纯色的通道方差是 34.6
（R≠G≠B），能轻松通过通道检查；但它的**空间**方差是 0.0000，因为每个像素
完全相同。真实照片的空间方差是 60.25。两者相差六个数量级，任何阈值都能分开。

实测对照：

| 输入 | 通道 std | 空间 std |
|---|---|---|
| 纯暖肤色 | 34.62 | **0.0000** |
| 纯黑 | 0.00 | 0.0000 |
| 真实照片 | 60.76 | **60.25** |

**拒绝评分而非给 0 分**：这与项目一贯的"没查 ≠ 干净"原则一致。返回
`assessed=False, nsfw=None`，调用方必须当成"未判定"处理。

---

## 4. 限制二：模型本身的局限

模型卡自己声明：

> NSFW content is subjective and contextual, this model is intended to help
> identify this content, use at your own risk.

训练集 220,000 张（100k NSFW + 100k SFW），含真实照片、插画、Rule 34、梗图、
AI 生成图。测试集 20,000 张，98.56% 准确率。

**我们无法验证这个数字**，因为我们没有那个测试集。我们能验证的只有上面
§2.1/§2.2 的少数样本。这意味着：

- 98.56% 是**厂商声明**，记录在此但不是本项目背书
- 实际部署应按更保守的阈值调优（`threshold` 参数）
- 误判率与漏判率都没有独立评估

---

## 5. 与原厂方案的对比

原厂 `imaging_skin_hsv.py` 的判据：

```
R > G > B  且  饱和度 > 0.09  且  色相 ∈ {2,4,5,7,8,11,13,14,16,17}（共 10/18 桶）
```

这是**肤色面积测量**，不是分类器。后果：

| 场景 | 原厂方案 | Marqo 384 |
|---|---|---|
| 灰度照片 | **永不触发**（`R=G=B`） | 正常判定（0.078） |
| 沙滩 / 木质家具 / 棕色地毯 | 大量"肤色"像素 | 正常判定 |
| 深肤色用户 | 判据不分深浅，但阈值按浅肤色调 | 训练集包含多样性 |
| 插画 / 二次元 | 取决于配色 | 训练集含 Rule 34 |
| 亮度变化 | 直接影响判据 | 波动 <0.02 |

原厂方案保留在 `src/green_dam/imaging_skin_hsv.py`，作为**证据**（记录厂商
做了什么），不作为产品判定。`ReferenceBackend` 仍会跑它的算术检查，但
`NullBackend` 保证 `assessed=False`。

---

## 6. 部署与接口

```python
# 无模型（默认）
from green_dam.imagepath import default_backends
chain = default_backends()          # (ReferenceBackend, NullBackend)
                                    # 每一帧都是 assessed=False

# 有模型
from host.imaging import MarqoOnnxBackend
backend = MarqoOnnxBackend.from_path(Path("model.onnx"))
chain = (backend,) + default_backends()

# 家长放置自己的模型
backend = MarqoOnnxBackend.from_path(Path(r"D:\my-model\model.onnx"))
```

体积影响：

| 配置 | 部署包 |
|---|---|
| 基础（无 M4） | 61 MB |
| + 模型（21.4 MB） | 82 MB |
| + onnxruntime（38 MB） | **120 MB** |

`onnxruntime` 比模型本身还大。因此 M4 应作为**可选包**分发，而不是默认包含。

---

## 7. 未做的事

- **没有做独立的偏倚评估**。要评估，需要构造跨肤色、跨画风、跨性别的测试集，
  这不是本仓库能提供的。模型卡没有提供，我们也没有编造。
- **没有做对抗鲁棒性测试**。一张精心扰动的图能不能绕过，未知。
- **没有验证 98.56% 这个数字**。见 §4。
- **没有做视频流优化**。当前是逐帧调用，90 ms/帧。视频场景需要跳帧策略，
  未实现。

这些缺口都记录在此，而不是用"应该没问题"掩盖。
