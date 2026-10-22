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
| 真实 ONNX 后端 | 需 `onnxruntime` + 模型文件；**属宿主**，不在引擎 |
| 模型选择与授权 | 未定；需评估可商用许可的模型 |
| 人脸检测（Haar） | 训练模型，非逻辑（`image-layer.md` §5.1） |
| 原厂轮廓判定复刻 | 架构目标是分类器而非复刻（`image-layer.md` §5.2） |
| 图像解码 | 引擎吃已解码 BGR；解码属输入层 |
