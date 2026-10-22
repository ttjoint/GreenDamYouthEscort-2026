"""Tests for the image backend interface.

The load-bearing property is that "no model ran" stays distinguishable from
"the model said clean". Every test that asserts an unassessed verdict exists for
that reason.

A fake backend stands in for a host's ONNX session, which is how this interface
gets exercised without importing onnxruntime - the dependency contract forbids
that in the engine, and the interface is what makes the prohibition workable.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam import imagepath as ip  # noqa: E402


def bgr(width: int, height: int, *, r: int = 0, g: int = 0,
        b: int = 0) -> bytes:
    return bytes([b, g, r]) * (width * height)


class FakeBackend:
    """Stands in for a host backend wrapping an ONNX session."""

    name = "fake-onnx"

    def __init__(self, score: float | None):
        self.score = score
        self.calls = 0

    def classify(self, buffer, width, height):
        self.calls += 1
        return ip.verdict_from_score(self.score, backend=self.name,
                                     metrics={"bytes": len(buffer)})


class UnassessingBackend:
    name = "unassessing"

    def classify(self, buffer, width, height):
        return ip.ImageVerdict(reason=ip.NOT_ASSESSED, backend=self.name)


class ProtocolTest(unittest.TestCase):
    def test_fake_satisfies_protocol(self):
        self.assertIsInstance(FakeBackend(0.1), ip.ImageBackend)

    def test_reference_satisfies_protocol(self):
        self.assertIsInstance(ip.ReferenceBackend(), ip.ImageBackend)

    def test_unassessing_satisfies_protocol(self):
        self.assertIsInstance(UnassessingBackend(), ip.ImageBackend)


class VerdictFromScoreTest(unittest.TestCase):
    def test_default_threshold(self):
        self.assertEqual(ip.DEFAULT_NSFW_THRESHOLD, 0.5)

    def test_above_threshold_flags(self):
        got = ip.verdict_from_score(0.9, backend="m")
        self.assertTrue(got.assessed)
        self.assertTrue(got.flagged)
        self.assertEqual(got.reason, ip.ABOVE_THRESHOLD)

    def test_below_threshold_does_not_flag(self):
        got = ip.verdict_from_score(0.1, backend="m")
        self.assertTrue(got.assessed)
        self.assertFalse(got.flagged)
        self.assertEqual(got.reason, ip.BELOW_THRESHOLD)

    def test_threshold_is_inclusive(self):
        self.assertTrue(ip.verdict_from_score(0.5).flagged)

    def test_none_score_is_unassessed_not_zero(self):
        """The distinction this module exists for."""
        got = ip.verdict_from_score(None, backend="m")
        self.assertIsNone(got.nsfw)
        self.assertFalse(got.assessed)
        self.assertFalse(got.flagged)
        self.assertEqual(got.reason, ip.NOT_ASSESSED)

    def test_unassessed_differs_from_zero_score(self):
        zero = ip.verdict_from_score(0.0)
        none = ip.verdict_from_score(None)
        self.assertEqual(zero.nsfw, 0.0)
        self.assertTrue(zero.assessed)
        self.assertIsNone(none.nsfw)
        self.assertFalse(none.assessed)

    def test_rejects_out_of_range(self):
        for bad in (-0.1, 1.1):
            with self.assertRaises(ValueError):
                ip.verdict_from_score(bad)

    def test_custom_threshold(self):
        got = ip.verdict_from_score(0.3, threshold=0.2)
        self.assertTrue(got.flagged)

    def test_metrics_carried(self):
        got = ip.verdict_from_score(0.1, metrics={"ms": 12})
        self.assertEqual(got.metrics["ms"], 12)


class ReferenceBackendTest(unittest.TestCase):
    def test_never_claims_an_nsfw_score(self):
        """The arithmetic checks are not a classifier."""
        got = ip.ReferenceBackend().classify(bgr(64, 64, r=100), 64, 64)
        self.assertIsNone(got.nsfw)
        self.assertFalse(got.assessed)
        self.assertFalse(got.flagged)

    def test_reports_the_arithmetic_checks(self):
        got = ip.ReferenceBackend().classify(bgr(64, 64, r=100), 64, 64)
        self.assertIsNotNone(got.screen)
        self.assertGreater(got.metrics["checks_run"], 0)

    def test_skin_off_by_default(self):
        got = ip.ReferenceBackend().classify(bgr(64, 64, r=200, g=150, b=120),
                                             64, 64)
        self.assertIsNone(got.skin)

    def test_skin_opt_in(self):
        backend = ip.ReferenceBackend(run_skin=True)
        got = backend.classify(bgr(64, 64, r=200, g=150, b=120), 64, 64)
        self.assertIsNotNone(got.skin)
        self.assertIn("skin_ratio", got.metrics)

    def test_short_buffer_raises(self):
        with self.assertRaises(ValueError):
            ip.ReferenceBackend().classify(b"\x00\x00", 8, 8)

    def test_backend_name_recorded(self):
        got = ip.ReferenceBackend().classify(bgr(64, 64), 64, 64)
        self.assertEqual(got.backend, "reference")


class RedPlaneTest(unittest.TestCase):
    def test_extracts_r_from_bgr(self):
        # b=1, g=2, r=3 repeated -> plane is all 3s
        plane = ip._red_plane(bytes([1, 2, 3]) * 4, 2, 2)
        self.assertEqual(plane, bytes([3, 3, 3, 3]))

    def test_short_buffer_raises(self):
        with self.assertRaises(ValueError):
            ip._red_plane(b"\x00\x00\x00", 2, 2)


class HighToneTest(unittest.TestCase):
    def test_floor_and_threshold_constants(self):
        self.assertEqual(ip.HIGH_TONE_FLOOR, 210)
        self.assertAlmostEqual(ip.HIGH_TONE_THRESHOLD, 0.9)

    def test_all_white_is_overexposed(self):
        self.assertTrue(ip.looks_overexposed(bytes([255] * 100)))

    def test_all_dark_is_not(self):
        self.assertFalse(ip.looks_overexposed(bytes([10] * 100)))

    def test_fraction(self):
        plane = bytes([255] * 9 + [0])
        self.assertAlmostEqual(ip.high_tone_fraction(plane), 0.9)

    def test_empty_plane(self):
        self.assertEqual(ip.high_tone_fraction(b""), 0.0)


class ChainTest(unittest.TestCase):
    def test_empty_chain_reports_no_model(self):
        got = ip.ClassifierChain().classify(bgr(8, 8), 8, 8)
        self.assertEqual(got.reason, ip.NO_MODEL)
        self.assertFalse(got.assessed)

    def test_first_flag_wins(self):
        chain = ip.ClassifierChain([FakeBackend(0.9), FakeBackend(0.1)])
        got = chain.classify(bgr(8, 8), 8, 8)
        self.assertTrue(got.flagged)
        self.assertEqual(got.metrics["backends_run"], 1)

    def test_all_clean_reports_highest(self):
        chain = ip.ClassifierChain([FakeBackend(0.1), FakeBackend(0.3)])
        got = chain.classify(bgr(8, 8), 8, 8)
        self.assertFalse(got.flagged)
        self.assertTrue(got.assessed)
        self.assertAlmostEqual(got.nsfw, 0.3)
        self.assertEqual(got.metrics["backends_run"], 2)

    def test_unassessing_backend_does_not_make_chain_clean(self):
        """A backend that cannot assess contributes nothing, not a zero."""
        chain = ip.ClassifierChain([UnassessingBackend()])
        got = chain.classify(bgr(8, 8), 8, 8)
        self.assertFalse(got.assessed)
        self.assertEqual(got.reason, ip.NOT_ASSESSED)

    def test_mixed_unassessing_and_clean(self):
        chain = ip.ClassifierChain([UnassessingBackend(), FakeBackend(0.2)])
        got = chain.classify(bgr(8, 8), 8, 8)
        self.assertTrue(got.assessed)
        self.assertAlmostEqual(got.nsfw, 0.2)

    def test_add_returns_self(self):
        chain = ip.ClassifierChain()
        self.assertIs(chain.add(FakeBackend(0.1)), chain)

    def test_reference_alone_stays_unassessed(self):
        chain = ip.ClassifierChain([ip.ReferenceBackend()])
        got = chain.classify(bgr(64, 64, r=100), 64, 64)
        self.assertFalse(got.assessed)


class LogSafetyTest(unittest.TestCase):
    def test_dict_has_no_pixels(self):
        got = ip.ReferenceBackend(run_skin=True).classify(
            bgr(64, 64, r=200, g=150, b=120), 64, 64)
        blob = json.dumps(got.to_dict())
        self.assertIn("assessed", blob)
        # 200/150/120 are the pixel values; none may appear.
        for value in ("200", "150", "120"):
            self.assertNotIn(value, blob)

    def test_dict_is_json_friendly(self):
        json.dumps(ip.verdict_from_score(0.42).to_dict())

    def test_none_score_survives_json(self):
        payload = json.loads(json.dumps(ip.verdict_from_score(None).to_dict()))
        self.assertIsNone(payload["nsfw"])


class DependencyBoundaryTest(unittest.TestCase):
    def test_module_imports_nothing_third_party(self):
        """The interface must stay importable without onnxruntime."""
        source = (ROOT / "src" / "green_dam" / "imagepath.py").read_text(
            encoding="utf-8")
        for forbidden in ("import numpy", "import cv2", "import onnxruntime",
                          "import torch", "from numpy", "from cv2",
                          "from onnxruntime"):
            self.assertNotIn(forbidden, source,
                             msg=f"imagepath must not import {forbidden}")

    def test_host_backend_can_be_supplied_without_engine_knowing(self):
        """The point of the Protocol: the engine never names the host's type."""
        backend = FakeBackend(0.7)
        got = ip.ClassifierChain([backend]).classify(bgr(8, 8), 8, 8)
        self.assertTrue(got.flagged)
        self.assertEqual(got.backend, "fake-onnx")


if __name__ == "__main__":
    unittest.main()


class FilterIntegrationTest(unittest.TestCase):
    """check_image_verdict keeps 'no model' distinct from 'model says clean'."""

    def _filter(self, backend=None, slot13=1, slot5=2):
        from green_dam.filter import Filter
        from green_dam.sysdat import PolicyArray
        values = [0] * 67
        values[12] = slot13     # enable_image_filter
        values[4] = slot5       # image_filter_level
        return Filter(legacy=PolicyArray(values=tuple(values)),
                      image_backend=backend)

    def test_no_backend_is_unassessed(self):
        got = self._filter().check_image_verdict(bgr(8, 8), width=8, height=8)
        self.assertFalse(got.assessed)
        self.assertEqual(got.reason, ip.NO_MODEL)

    def test_backend_score_reaches_verdict(self):
        got = self._filter(FakeBackend(0.8)).check_image_verdict(
            bgr(8, 8), width=8, height=8)
        self.assertTrue(got.assessed)
        self.assertTrue(got.flagged)
        self.assertAlmostEqual(got.nsfw, 0.8)

    def test_gate_closes_the_path(self):
        got = self._filter(FakeBackend(0.8), slot13=0).check_image_verdict(
            bgr(8, 8), width=8, height=8)
        self.assertFalse(got.assessed)
        self.assertNotEqual(got.reason, ip.NO_MODEL)

    def test_gate_reason_is_a_switch_not_a_model_failure(self):
        got = self._filter(FakeBackend(0.8), slot13=0).check_image_verdict(
            bgr(8, 8), width=8, height=8)
        self.assertEqual(got.reason, "image_filter_disabled")
