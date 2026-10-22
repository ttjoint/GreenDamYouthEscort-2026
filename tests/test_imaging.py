"""Tests for image screening.

All test images are synthesised from arithmetic - solid colours, gradients,
stripes. No real photograph, and nothing from the original product, is used.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam.imaging import (  # noqa: E402
    HIGH_TONE_FLOOR,
    HIGH_TONE_THRESHOLD,
    MAX_ASPECT_RATIO,
    MIN_DIMENSION,
    Check,
    aspect_ok,
    aspect_ratio,
    high_tone_ratio,
    red_channel_from_bgr,
    screen,
)


def _solid_red(value: int, count: int) -> bytes:
    return bytes([value]) * count


def _bgr_solid(width: int, height: int, b: int, g: int, r: int) -> bytes:
    return bytes([b, g, r]) * (width * height)


class ConstantsTest(unittest.TestCase):
    """Every constant is read from the binary; pin them so they cannot drift."""

    def test_min_dimension(self):
        self.assertEqual(MIN_DIMENSION, 32)      # cmp ...,0x20 @ 0x8e24

    def test_aspect_threshold(self):
        self.assertEqual(MAX_ASPECT_RATIO, 10.0)  # [0x1000dc48]

    def test_high_tone_floor(self):
        self.assertEqual(HIGH_TONE_FLOOR, 210)    # 0xd2 split in 0x8f07

    def test_high_tone_threshold(self):
        self.assertEqual(HIGH_TONE_THRESHOLD, 0.9)  # [0x1000d540]


class AspectTest(unittest.TestCase):
    def test_ratio_is_long_over_short(self):
        self.assertEqual(aspect_ratio(100, 50), 2.0)
        self.assertEqual(aspect_ratio(50, 100), 2.0)

    def test_ratio_rejects_zero(self):
        for w, h in ((0, 10), (10, 0), (-1, 10)):
            with self.assertRaises(ValueError):
                aspect_ratio(w, h)

    def test_small_frames_are_not_judged(self):
        # The original returned 1 ("not abnormal") for anything under 32 px.
        self.assertTrue(aspect_ok(31, 1000))
        self.assertTrue(aspect_ok(1000, 31))

    def test_normal_frame_passes(self):
        self.assertTrue(aspect_ok(640, 480))
        self.assertTrue(aspect_ok(100, 100))

    def test_thin_frame_fails(self):
        self.assertFalse(aspect_ok(400, 40))     # ratio exactly 10.0
        self.assertFalse(aspect_ok(1000, 32))    # ratio 31.25


class HighToneTest(unittest.TestCase):
    def test_all_bright(self):
        self.assertEqual(high_tone_ratio(_solid_red(255, 100), 100), 1.0)

    def test_all_dark(self):
        self.assertEqual(high_tone_ratio(_solid_red(0, 100), 100), 0.0)

    def test_boundary_value_counts_as_high(self):
        # The original summed 0xd2..0xff inclusive, so 210 counts.
        self.assertEqual(high_tone_ratio(_solid_red(210, 10), 10), 1.0)
        self.assertEqual(high_tone_ratio(_solid_red(209, 10), 10), 0.0)

    def test_half(self):
        data = _solid_red(255, 50) + _solid_red(0, 50)
        self.assertEqual(high_tone_ratio(data, 100), 0.5)

    def test_rejects_short_buffer(self):
        with self.assertRaises(ValueError):
            high_tone_ratio(b"\xff" * 5, 10)

    def test_rejects_zero_pixels(self):
        with self.assertRaises(ValueError):
            high_tone_ratio(b"", 0)


class BgrExtractionTest(unittest.TestCase):
    def test_picks_third_byte(self):
        buf = bytes([1, 2, 3, 4, 5, 6])          # two BGR pixels
        self.assertEqual(red_channel_from_bgr(buf, 2, 1), bytes([3, 6]))

    def test_start_offset(self):
        buf = bytes([0, 0, 0]) + bytes([1, 2, 3])
        self.assertEqual(red_channel_from_bgr(buf, 1, 1, start=3), bytes([3]))

    def test_rejects_short_buffer(self):
        with self.assertRaises(ValueError):
            red_channel_from_bgr(b"\x00" * 5, 2, 1)


class ScreenTest(unittest.TestCase):
    def test_too_small_is_not_suspect_but_not_fully_screened(self):
        got = screen(width=10, height=10)
        self.assertFalse(got.suspect)
        self.assertFalse(got.fully_screened)
        self.assertEqual(got.detail, "too_small")
        self.assertEqual(got.checks_run, (Check.TOO_SMALL,))

    def test_normal_frame_without_channel_is_partial(self):
        got = screen(width=640, height=480)
        self.assertFalse(got.suspect)
        # No R plane means the high-tone check never ran: partial, not clean.
        self.assertFalse(got.fully_screened)
        self.assertNotIn(Check.HIGH_TONE, got.checks_run)
        self.assertEqual(got.detail, "partial")

    def test_normal_frame_with_channel_is_fully_screened(self):
        w, h = 64, 64
        buf = _bgr_solid(w, h, 10, 10, 10)
        got = screen(width=w, height=h,
                     red_channel=red_channel_from_bgr(buf, w, h))
        self.assertTrue(got.fully_screened)

    def test_clean_frame(self):
        w, h = 64, 64
        buf = _bgr_solid(w, h, 10, 10, 10)       # dark
        got = screen(width=w, height=h,
                     red_channel=red_channel_from_bgr(buf, w, h))
        self.assertFalse(got.suspect)
        self.assertEqual(got.checks_run, (Check.ASPECT, Check.HIGH_TONE))
        self.assertEqual(got.detail, "clean")

    def test_thin_frame_fires_aspect(self):
        got = screen(width=1000, height=32)
        self.assertTrue(got.suspect)
        self.assertIn(Check.ASPECT, got.fired)
        self.assertEqual(got.detail, "aspect")

    def test_bright_frame_fires_high_tone(self):
        w, h = 64, 64
        buf = _bgr_solid(w, h, 255, 255, 255)    # near-white
        got = screen(width=w, height=h,
                     red_channel=red_channel_from_bgr(buf, w, h))
        self.assertTrue(got.suspect)
        self.assertIn(Check.HIGH_TONE, got.fired)

    def test_both_can_fire(self):
        w, h = 640, 32
        buf = _bgr_solid(w, h, 255, 255, 255)
        got = screen(width=w, height=h,
                     red_channel=red_channel_from_bgr(buf, w, h))
        self.assertIn(Check.ASPECT, got.fired)
        self.assertIn(Check.HIGH_TONE, got.fired)
        self.assertEqual(got.fired[0], Check.ASPECT)   # order preserved

    def test_metrics_are_numbers_not_pixels(self):
        w, h = 64, 64
        buf = _bgr_solid(w, h, 0, 0, 255)
        got = screen(width=w, height=h,
                     red_channel=red_channel_from_bgr(buf, w, h))
        self.assertEqual(got.metrics["aspect_ratio"], 1.0)
        self.assertEqual(got.metrics["high_tone_ratio"], 1.0)
        # Nothing image-derived beyond the two ratios.
        self.assertEqual(set(got.metrics), {"aspect_ratio", "high_tone_ratio"})

    def test_gradient_below_threshold_is_clean(self):
        w, h = 64, 64
        # Ramp so roughly a quarter of pixels are >= 210.
        red = bytes((i * 4) % 256 for i in range(w * h))
        got = screen(width=w, height=h, red_channel=red)
        self.assertFalse(got.suspect)
        self.assertLess(got.metrics["high_tone_ratio"], HIGH_TONE_THRESHOLD)


if __name__ == "__main__":
    unittest.main()
