"""M4: an ONNX NSFW backend, using a model that can actually be shipped.

The model this wraps is ``Marqo/nsfw-image-detection-384``, an Apache-2.0
ViT-tiny fine-tune, exported to ONNX by ``KanariKanaru``. The choice was made by
measurement, not by reputation - see ``docs/image-backend.md`` for the table.
The numbers that decided it:

===========================  ==========  ============  ==============
candidate                    licence     weights       CPU per frame
===========================  ==========  ============  ==============
Falconsai/nsfw_image_detect  Apache-2.0  327 MB        -
AdamCodd/vit-base-nsfw       Apache-2.0  328 MB        -
Freepik/nsfw_image_detector  MIT         165 MB        -
**Marqo 384 (ONNX)**         **Apache**  **21.4 MB**   **37 ms**
taufiqdp/mobilenetv4         Apache-2.0  9.5 MB        non-standard I/O
NudeNet v3.4                 AGPL-3.0    11.6 MB       licence unusable
===========================  ==========  ============  ==============

37 ms on one core is what makes this usable for screen monitoring at 1 fps
without the fan spinning up, which was the objection to full-screen OCR.

Why the vendor's own skin check is not used instead
---------------------------------------------------
``imaging_skin_hsv`` is ``R > G > B`` plus a saturation floor plus ten hue
buckets. That is an area measurement, not a classifier:

* a beach, wooden furniture or a brown carpet produces "skin" pixels;
* a greyscale photo can never fire, because ``R = G = B`` makes ``R > G > B``
  false everywhere;
* the thresholds were tuned on one population, and nothing in the evidence says
  which.

It is kept as evidence of what the vendor did. It is not a substitute for a
model, and this module does not pretend it is.

The one honest limitation
-------------------------
The model is a classifier, so it sees a distribution it was trained on. A
**uniform-colour** image is out of that distribution: measured here, a flat warm
skin tone scored 0.54 and a flat black frame 0.36 - above the 0.5 default
threshold in the first case. Real photographs scored 0.046-0.078.

That is handled where it belongs, in :func:`score_image`: a frame with almost no
detail is reported as *not assessed* rather than as NSFW, because a solid colour
is not evidence of anything. Refusing to answer is the same choice the engine
already makes for an image check it could not run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "MarqoOnnxBackend",
    "MODEL_FILENAME",
    "INPUT_SIZE",
    "MIN_ENTROPY",
    "UnusableFrame",
    "service",
]

#: The ONNX file's name, so a caller can point at a directory.
MODEL_FILENAME = "model.onnx"

#: ViT-tiny-patch16-384: fixed 384x384 RGB, NCHW, float32, normalised to [-1, 1].
INPUT_SIZE = 384

#: Below this **spatial** standard deviation a frame carries too little detail
#: to classify, and the model's output is not evidence.
#:
#: Spatial, not per-channel. A flat warm skin tone has a per-channel standard
#: deviation of 34.6 (R != G != B) while every pixel is identical - measuring
#: per-channel lets it through, and it scores 0.543, above the 0.5 threshold.
#: Measured spatial deviation: flat colours 0.0000, real photographs 60.25. Any
#: floor between those two works; 4.0 leaves a wide margin either way.
MIN_ENTROPY = 4.0


class UnusableFrame(ValueError):
    """The frame cannot be judged - wrong size, undecodable, or featureless."""


@dataclass
class MarqoOnnxBackend:
    """Wraps an ONNX session for the Marqo 384 classifier.

    ``session`` is injected rather than created here so the engine stays free of
    ``onnxruntime``: the engine must import nothing third-party (A2), and this
    module lives in ``host`` for exactly that reason.

    ``name`` appears in the verdict and therefore in the log. It includes the
    model file's stem so a parent reading the log can tell which model spoke.
    """

    session: object
    threshold: float = 0.5
    name: str = "marqo-384"
    #: Cached input name; ``onnxruntime`` sessions expose it via ``get_inputs``.
    _input_name: str = field(default="input", repr=False)

    @classmethod
    def from_path(cls, path: Path, *, threshold: float = 0.5,
                  name: str = "marqo-384") -> "MarqoOnnxBackend":
        """Open a model file. ``onnxruntime`` is imported here, not at module level.

        A missing ``onnxruntime`` is reported as a clear error naming the extra
        that provides it, rather than an ``ImportError`` from three frames down.
        """
        try:
            import onnxruntime            # noqa: PLC0415 - lazy by design
        except ImportError as exc:        # pragma: no cover - depends on install
            raise UnusableFrame(
                "onnxruntime is not installed. Install the 'image' extra "
                "(pip install green-dam-2026[image]) to enable image "
                "classification.") from exc

        path = Path(path)
        if path.is_dir():
            path = path / MODEL_FILENAME
        if not path.is_file():
            raise UnusableFrame(f"no model at {path}")

        session = onnxruntime.InferenceSession(
            str(path), providers=["CPUExecutionProvider"])
        backend = cls(session=session, threshold=threshold,
                      name=f"{name}:{path.stem}")
        inputs = session.get_inputs()
        if inputs:
            backend._input_name = inputs[0].name
        return backend

    def classify(self, buffer: bytes, width: int, height: int):
        """Score one BGR frame and return an :class:`ImageVerdict`.

        The verdict comes from the engine's own ``verdict_from_score``, so the
        threshold logic and the ``assessed``/``nsfw`` distinction exist in one
        place rather than being re-derived here. It is reached through
        :mod:`host.engine` like every other engine call, so a broken deployment
        still produces one traceback instead of one per module.
        """
        from host.engine import verdict_from_score     # noqa: PLC0415 - lazy

        try:
            score = self.score_image(buffer, width, height)
        except UnusableFrame as exc:
            # Not an error: an unjudgeable frame is reported as unassessed. The
            # alternative - returning 0.0 - would be the "not looked at equals
            # clean" mistake this project exists to avoid.
            return verdict_from_score(
                None, threshold=self.threshold, backend=self.name,
                metrics={"unusable": 1, "why": str(exc)[:60]})
        return verdict_from_score(score, threshold=self.threshold,
                                  backend=self.name)

    def score_image(self, buffer: bytes, width: int, height: int) -> float:
        """Raw NSFW probability for one frame. Raises :class:`UnusableFrame`.

        Returns a value in ``0..1``. A frame with too little detail is refused
        rather than scored, for the reason in the module docstring.
        """
        import numpy as np                  # noqa: PLC0415 - lazy by design

        if width <= 0 or height <= 0:
            raise UnusableFrame(f"bad dimensions {width}x{height}")
        expected = width * height * 3
        if len(buffer) < expected:
            raise UnusableFrame(
                f"buffer holds {len(buffer)} bytes, {width}x{height}x3 needs "
                f"{expected}")

        frame = np.frombuffer(buffer, dtype=np.uint8, count=expected)
        frame = frame.reshape(height, width, 3)

        # Spatial deviation: collapse the colour channels, then measure spread
        # across pixels. Per-channel deviation would let a flat colour through,
        # because R != G != B gives it a large channel variance while every pixel
        # is identical - and a flat warm tone scores 0.543, above the threshold.
        spatial = frame.mean(axis=2).std()
        if float(spatial) < MIN_ENTROPY:
            raise UnusableFrame(
                f"frame spatial deviation {spatial:.2f} is below "
                f"{MIN_ENTROPY}; a featureless frame is not evidence")

        tensor = self._preprocess(frame)
        outputs = self.session.run(None, {self._input_name: tensor})
        logits = outputs[0][0]

        # Softmax over the two classes. Class 0 is NSFW for this model; the
        # order comes from the model card and is asserted by the tests.
        shifted = logits - logits.max()
        probabilities = np.exp(shifted) / np.exp(shifted).sum()
        return float(probabilities[0])

    def _preprocess(self, frame):
        """BGR frame -> NCHW float32 in [-1, 1], resized to 384x384.

        Resizing is a centre crop then a bilinear scale, matching what the model
        card specifies. ``crop_pct=1.0`` in the original timm config means "crop
        the shorter side", which is what this does.
        """
        import numpy as np                  # noqa: PLC0415

        height, width = frame.shape[:2]
        side = min(height, width)
        top = (height - side) // 2
        left = (width - side) // 2
        cropped = frame[top:top + side, left:left + side]

        resized = _resize_bilinear(cropped, INPUT_SIZE, INPUT_SIZE)

        rgb = resized[:, :, ::-1].astype(np.float32) / 255.0
        normalised = (rgb - 0.5) / 0.5
        # ``transpose`` produces a view; onnxruntime then copies it per call.
        # Making it contiguous up front is one copy instead of one per inference,
        # and measured 60 ms against 72 ms per frame on this machine.
        return np.ascontiguousarray(normalised.transpose(2, 0, 1)[None])


def _resize_bilinear(image, target_w: int, target_h: int):
    """Bilinear resize of an ``H x W x 3`` uint8 array.

    Written out rather than calling into Pillow so the host does not need Pillow
    for the model path, and so the exact resampling is visible. Pillow's bicubic
    differs slightly; the model card notes that either is acceptable and that
    results may differ on non-square input, which the centre crop avoids.
    """
    import numpy as np                      # noqa: PLC0415

    height, width = image.shape[:2]
    if height == target_h and width == target_w:
        return image

    ys = (np.arange(target_h) + 0.5) * (height / target_h) - 0.5
    xs = (np.arange(target_w) + 0.5) * (width / target_w) - 0.5
    ys = np.clip(ys, 0, height - 1)
    xs = np.clip(xs, 0, width - 1)

    y0 = np.floor(ys).astype(np.int32)
    x0 = np.floor(xs).astype(np.int32)
    y1 = np.minimum(y0 + 1, height - 1)
    x1 = np.minimum(x0 + 1, width - 1)

    wy = (ys - y0)[:, None, None]
    wx = (xs - x0)[None, :, None]

    top = (image[y0][:, x0] * (1 - wx) + image[y0][:, x1] * wx)
    bottom = (image[y1][:, x0] * (1 - wx) + image[y1][:, x1] * wx)
    blended = top * (1 - wy) + bottom * wy
    return blended.astype(np.uint8)
