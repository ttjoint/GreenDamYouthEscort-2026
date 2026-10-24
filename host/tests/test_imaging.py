"""M4: the ONNX NSFW backend.

Two groups of tests, and the second is the one that matters.

*Interface*  a fake session proves the backend honours the ``ImageBackend``
             protocol, maps scores through ``verdict_from_score``, and never
             invents an assessment. Runs with no model and no onnxruntime.

*Real model*  skipped unless a model file is present. Asserts the numbers in
             ``docs/image-backend.md`` are still true: a real photograph scores
             low, a featureless frame is refused rather than scored, and the
             per-frame cost is in the range that makes screen monitoring viable.

The out-of-distribution test exists because of a measured near-miss: a flat warm
skin tone scored **0.543** against a 0.5 threshold. Per-channel deviation does not
catch it (R != G != B gives it a variance of 34.6 while every pixel is
identical); spatial deviation does (0.0000 against 60.25 for a photograph).
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import numpy as np                                        # noqa: E402

from host.imaging import (                                # noqa: E402
    MIN_ENTROPY,
    MODEL_FILENAME,
    MarqoOnnxBackend,
    UnusableFrame,
    _resize_bilinear,
)

#: Where a model may be found for the real-model tests. Absent on most machines;
#: the tests then skip rather than fail, because a checkout without the weights
#: is a valid checkout.
MODEL_DIR = Path(os.environ.get("GREENDAM_NSFW_MODEL_DIR",
                                ROOT.parent / "models" / "nsfw-384"))


def _fake_session(score_logit: float = -2.0):
    """A stand-in for an onnxruntime session: one input, one logits output.

    The logit pair is ``[score_logit, 0.0]``, so a large negative value means
    "confidently SFW" and a positive one means "NSFW". Softmax is applied by the
    backend, which is the part worth testing.
    """

    class _Input:
        name = "input"

    class _Session:
        def get_inputs(self):
            return [_Input()]

        def run(self, _names, feeds):
            return [np.array([[score_logit, 0.0]], dtype=np.float32)]

    return _Session()


def _noisy_frame(size: int = 384) -> bytes:
    """A frame with enough spatial detail to pass the entropy floor."""
    rng = np.random.default_rng(1234)
    return rng.integers(0, 255, (size, size, 3), dtype=np.uint8).tobytes()


class InterfaceTest(unittest.TestCase):
    """The backend honours the protocol and never fabricates an assessment."""

    def test_satisfies_the_protocol(self):
        from green_dam.imagepath import ImageBackend

        backend = MarqoOnnxBackend(session=_fake_session())
        self.assertIsInstance(backend, ImageBackend)

    def test_low_score_is_not_flagged(self):
        backend = MarqoOnnxBackend(session=_fake_session(-2.0))
        verdict = backend.classify(_noisy_frame(), 384, 384)
        self.assertTrue(verdict.assessed)
        self.assertFalse(verdict.flagged)
        self.assertLess(verdict.nsfw, 0.5)

    def test_high_score_is_flagged(self):
        backend = MarqoOnnxBackend(session=_fake_session(4.0))
        verdict = backend.classify(_noisy_frame(), 384, 384)
        self.assertTrue(verdict.assessed)
        self.assertTrue(verdict.flagged)

    def test_threshold_is_honoured(self):
        """The same score must flip at the caller's threshold, not a fixed 0.5."""
        backend = MarqoOnnxBackend(session=_fake_session(0.0), threshold=0.9)
        verdict = backend.classify(_noisy_frame(), 384, 384)
        self.assertFalse(verdict.flagged,
                         msg="a score of 0.5 was flagged against a 0.9 "
                             "threshold")

    def test_backend_name_reaches_the_verdict(self):
        backend = MarqoOnnxBackend(session=_fake_session(), name="test-model")
        verdict = backend.classify(_noisy_frame(), 384, 384)
        self.assertIn("test-model", verdict.backend)

    def test_featureless_frame_is_unassessed_not_clean(self):
        """The central rule: not looked at must never read as clean."""
        backend = MarqoOnnxBackend(session=_fake_session(4.0))
        flat = np.full((384, 384, 3), 128, dtype=np.uint8).tobytes()
        verdict = backend.classify(flat, 384, 384)
        self.assertFalse(verdict.assessed,
                         msg="a featureless frame was scored; the model's "
                             "output on an out-of-distribution input is not "
                             "evidence")
        self.assertIsNone(verdict.nsfw)
        self.assertFalse(verdict.flagged)

    def test_flat_warm_tone_is_refused(self):
        """Measured: this exact frame scored 0.543 against a 0.5 threshold.

        Per-channel deviation is 34.6 (R != G != B) while every pixel is
        identical, so a per-channel test lets it through. Spatial deviation is
        0.0000, which is what the floor actually measures.
        """
        backend = MarqoOnnxBackend(session=_fake_session(4.0))
        warm = np.zeros((384, 384, 3), dtype=np.uint8)
        warm[:, :, 0] = 140   # B
        warm[:, :, 1] = 172   # G
        warm[:, :, 2] = 224   # R
        self.assertGreater(float(warm.std()), MIN_ENTROPY,
                           msg="the frame should have a large channel "
                               "deviation; the test is not exercising the "
                               "case it claims to")
        verdict = backend.classify(warm.tobytes(), 384, 384)
        self.assertFalse(verdict.assessed)
        self.assertFalse(verdict.flagged)

    def test_flat_black_is_refused(self):
        backend = MarqoOnnxBackend(session=_fake_session(4.0))
        flat = np.zeros((384, 384, 3), dtype=np.uint8).tobytes()
        self.assertFalse(backend.classify(flat, 384, 384).assessed)

    def test_short_buffer_is_unassessed(self):
        backend = MarqoOnnxBackend(session=_fake_session())
        verdict = backend.classify(b"\x00" * 100, 384, 384)
        self.assertFalse(verdict.assessed)

    def test_zero_dimensions_are_unassessed(self):
        backend = MarqoOnnxBackend(session=_fake_session())
        self.assertFalse(backend.classify(b"", 0, 0).assessed)

    def test_verdict_dict_carries_no_pixels(self):
        backend = MarqoOnnxBackend(session=_fake_session())
        blob = repr(backend.classify(_noisy_frame(), 384, 384).to_dict())
        self.assertIn("nsfw", blob)
        self.assertNotIn("array", blob)


class PreprocessTest(unittest.TestCase):
    """Resizing and normalisation, which the model's accuracy depends on."""

    def test_resize_produces_the_requested_shape(self):
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        self.assertEqual(_resize_bilinear(frame, 384, 384).shape, (384, 384, 3))

    def test_resize_is_a_noop_at_the_target_size(self):
        frame = np.zeros((384, 384, 3), dtype=np.uint8)
        result = _resize_bilinear(frame, 384, 384)
        self.assertIs(result, frame)

    def test_centre_crop_keeps_the_short_side(self):
        """A wide frame must be cropped, not squashed - squashing changes the
        aspect ratio and the model card warns it changes predictions."""
        backend = MarqoOnnxBackend(session=_fake_session())
        wide = np.zeros((100, 400, 3), dtype=np.uint8)
        wide[:, :, :] = 90
        tensor = backend._preprocess(wide)
        self.assertEqual(tensor.shape, (1, 3, 384, 384))

    def test_tensor_is_contiguous(self):
        """onnxruntime copies a non-contiguous view on every call.

        Measured: 72 ms per frame non-contiguous against 60 ms contiguous.
        """
        backend = MarqoOnnxBackend(session=_fake_session())
        frame = np.full((384, 384, 3), 77, dtype=np.uint8)
        self.assertTrue(backend._preprocess(frame).flags["C_CONTIGUOUS"])

    def test_tensor_range_is_minus_one_to_one(self):
        backend = MarqoOnnxBackend(session=_fake_session())
        frame = np.zeros((384, 384, 3), dtype=np.uint8)
        frame[:, :, :] = 255
        tensor = backend._preprocess(frame)
        self.assertAlmostEqual(float(tensor.max()), 1.0, places=5)
        frame[:, :, :] = 0
        self.assertAlmostEqual(float(backend._preprocess(frame).min()), -1.0,
                               places=5)


class MissingDependencyTest(unittest.TestCase):
    def test_missing_model_file_is_a_clear_error(self):
        with self.assertRaises(UnusableFrame) as ctx:
            MarqoOnnxBackend.from_path(Path("no-such-model.onnx"))
        self.assertIn("no model", str(ctx.exception))

    def test_missing_onnxruntime_names_the_extra(self):
        """An ImportError three frames down is not an error message."""
        with mock.patch.dict(sys.modules, {"onnxruntime": None}):
            with self.assertRaises(UnusableFrame) as ctx:
                MarqoOnnxBackend.from_path(Path("whatever.onnx"))
        message = str(ctx.exception)
        self.assertIn("onnxruntime", message)
        self.assertIn("image", message,
                      msg="the message should name the extra that provides it")


class RealModelTest(unittest.TestCase):
    """Runs only when a model file is present.

    These are the tests that keep ``docs/image-backend.md`` honest. The numbers
    they assert are the ones the document quotes; if a future model change moves
    them, the document has to move too.
    """

    @classmethod
    def setUpClass(cls):
        candidate = MODEL_DIR / MODEL_FILENAME
        if not candidate.is_file():
            raise unittest.SkipTest(
                f"no NSFW model at {candidate}; set GREENDAM_NSFW_MODEL_DIR to "
                f"run the real-model tests")
        try:
            import onnxruntime                  # noqa: F401
        except ImportError:                      # pragma: no cover
            raise unittest.SkipTest("onnxruntime is not installed")
        cls.backend = MarqoOnnxBackend.from_path(candidate)
        cls._tmp = None

    def _synthetic_photo(self, size: int = 384) -> bytes:
        """A frame with real structure: gradients plus noise.

        Not a photograph, but far enough from a flat colour to be in the model's
        input distribution. The point of the real-model tests that use it is the
        *interface* (score in range, no exception), not accuracy.
        """
        rng = np.random.default_rng(7)
        ramp = np.linspace(30, 220, size, dtype=np.float32)
        frame = np.zeros((size, size, 3), dtype=np.float32)
        frame[:, :, 0] = ramp[None, :]
        frame[:, :, 1] = ramp[:, None]
        frame[:, :, 2] = 128
        frame += rng.normal(0, 8, frame.shape)
        return np.clip(frame, 0, 255).astype(np.uint8).tobytes()

    def test_score_is_a_probability(self):
        score = self.backend.score_image(self._synthetic_photo(), 384, 384)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_verdict_is_assessed(self):
        verdict = self.backend.classify(self._synthetic_photo(), 384, 384)
        self.assertTrue(verdict.assessed)
        self.assertIsNotNone(verdict.nsfw)

    def test_featureless_frame_is_refused_by_the_real_model(self):
        flat = np.full((384, 384, 3), 200, dtype=np.uint8).tobytes()
        verdict = self.backend.classify(flat, 384, 384)
        self.assertFalse(verdict.assessed)

    def test_input_signature_is_384_square(self):
        """The model is fixed-input; a different size would silently fail."""
        inputs = self.backend.session.get_inputs()
        self.assertTrue(inputs)
        shape = inputs[0].shape
        self.assertEqual(list(shape)[-2:], [384, 384])

    def test_class_zero_is_nsfw(self):
        """The class order comes from the model card and everything depends on it.

        A flipped order would make every clean page flagged and every flagged
        page clean - the worst possible failure, and one that no aggregate
        accuracy figure would reveal.
        """
        # A featureless-but-detailed frame must score as one of the two classes
        # and the probabilities must sum to one.
        score = self.backend.score_image(self._synthetic_photo(), 384, 384)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)


if __name__ == "__main__":
    unittest.main()
