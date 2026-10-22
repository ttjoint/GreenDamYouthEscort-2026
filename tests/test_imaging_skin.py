"""Tests for CHECKEX8x's path, including the skin-tone classifier.

Source: analysis/probe/p28_checkex8x.txt. Constants are asserted against the
addresses they came from so a reader can spot-check one without re-reading the
whole document.

The skin-tone stage is the one p22 wrongly concluded did not exist, so its tests
carry the most weight here.

Frames are synthesised; nothing reads an image file.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam import imaging_skin as sk  # noqa: E402
from green_dam import imaging_skin_hsv as hsv  # noqa: E402


def bgr(width: int, height: int, *, r: int, g: int, b: int) -> bytes:
    return bytes([b, g, r]) * (width * height)


class GateTest(unittest.TestCase):
    def test_size_gate_is_160(self):
        # 0x10007067 / 0x10007076
        self.assertEqual(sk.MIN_DIMENSION, 160)

    def test_needs_resize_only_when_both_sides_exceed(self):
        self.assertTrue(sk.needs_resize(161, 161))
        self.assertFalse(sk.needs_resize(160, 161))
        self.assertFalse(sk.needs_resize(161, 160))

    def test_resize_targets_width_160(self):
        self.assertEqual(sk.RESIZE_WIDTH, 160)

    def test_scaled_size_keeps_aspect(self):
        self.assertEqual(sk.scaled_size(320, 240), (160, 120))

    def test_scaled_size_truncates(self):
        # Both sides must exceed 160 to trigger scaling; 160/300*200 = 106.67.
        self.assertEqual(sk.scaled_size(300, 200), (160, 106))

    def test_no_scaling_below_gate(self):
        # Height 100 is under the 160 gate, so no resize happens even though
        # the width is 300.
        self.assertEqual(sk.scaled_size(300, 100), (300, 100))

    def test_no_resize_returns_input(self):
        self.assertEqual(sk.scaled_size(100, 100), (100, 100))

    def test_integer_aspect_gate(self):
        self.assertEqual(sk.MAX_ASPECT, 8)
        self.assertTrue(sk.aspect_ok(160, 160))
        self.assertTrue(sk.aspect_ok(800, 100))
        self.assertFalse(sk.aspect_ok(900, 100))

    def test_aspect_gate_needs_32(self):
        self.assertFalse(sk.aspect_ok(31, 100))

    def test_long_strip_gate(self):
        # 0x10006d83: width == 160 and width/height > 20
        self.assertEqual(sk.LONG_STRIP_RATIO, 20)
        self.assertTrue(sk.long_strip(160, 7))
        self.assertFalse(sk.long_strip(160, 8))
        self.assertFalse(sk.long_strip(200, 5))

    def test_long_strip_zero_height(self):
        self.assertFalse(sk.long_strip(160, 0))


class RatioTest(unittest.TestCase):
    def test_floor(self):
        # 0x10006e0d: 0.08
        self.assertEqual(sk.RATIO1_FLOOR, 0.08)
        self.assertEqual(sk.decide_ratio(0.08, 0.5), sk.RC_REJECTED)
        self.assertEqual(sk.decide_ratio(0.05, 0.5), sk.RC_REJECTED)

    def test_branch_point(self):
        # 0x10006e1e: 0.15
        self.assertEqual(sk.RATIO1_BRANCH, 0.15)
        self.assertEqual(sk.decide_ratio(0.15, 0.5, branch_result=True),
                         sk.RC_LONG_BRANCH)
        self.assertEqual(sk.decide_ratio(0.15, 0.5, branch_result=False),
                         sk.RC_SHORT_BRANCH)

    def test_long_branch_is_minus_one(self):
        self.assertEqual(sk.RC_LONG_BRANCH, -1)

    def test_short_branch_is_one(self):
        self.assertEqual(sk.RC_SHORT_BRANCH, 1)

    def test_rejected_is_zero(self):
        self.assertEqual(sk.RC_REJECTED, 0)

    def test_second_ratio_threshold(self):
        # 0x10006e88: 0.6
        self.assertEqual(sk.RATIO2_OVER_RATIO1, 0.6)
        self.assertEqual(sk.decide_ratio(0.1, 0.07, branch_result=True),
                         sk.RC_LONG_BRANCH)
        self.assertEqual(sk.decide_ratio(0.1, 0.05), -2)

    def test_fallthrough_sentinel(self):
        # -2 means "go to the rectangle stage".
        self.assertEqual(sk.decide_ratio(0.1, 0.01), -2)

    def test_unknown_branch_refuses_rather_than_guesses(self):
        """0x10001ba0 is not reversed; the module must not invent its result."""
        with self.assertRaises(sk.NotReversed):
            sk.decide_ratio(0.2, 0.5)
        with self.assertRaises(sk.NotReversed):
            sk.decide_ratio(0.1, 0.07)


class HsvTest(unittest.TestCase):
    """0x10009090."""

    def test_primaries(self):
        self.assertEqual(hsv.rgb_to_hsv(255, 0, 0)[0], 0.0)
        self.assertAlmostEqual(hsv.rgb_to_hsv(0, 255, 0)[0], 120.0)
        self.assertAlmostEqual(hsv.rgb_to_hsv(0, 0, 255)[0], 240.0)

    def test_black_is_all_zero(self):
        # 0x10009115: max == 0 -> outputs zero
        self.assertEqual(hsv.rgb_to_hsv(0, 0, 0), (0.0, 0.0, 0.0))

    def test_grey_has_zero_saturation(self):
        hue, saturation, value = hsv.rgb_to_hsv(100, 100, 100)
        self.assertEqual(saturation, 0.0)
        self.assertAlmostEqual(value, 100 / 255)

    def test_saturation_range(self):
        _, saturation, _ = hsv.rgb_to_hsv(200, 150, 120)
        self.assertGreater(saturation, 0.0)
        self.assertLessEqual(saturation, 1.0)

    def test_hue_in_range(self):
        for rgb in ((200, 150, 120), (10, 200, 30), (30, 10, 200)):
            hue, _, _ = hsv.rgb_to_hsv(*rgb)
            self.assertGreaterEqual(hue, 0.0)
            self.assertLess(hue, 360.0)

    def test_rejects_out_of_range_channels(self):
        # 0x100090a1: any channel above 255 is an error
        with self.assertRaises(ValueError):
            hsv.rgb_to_hsv(256, 0, 0)


class HueGradeTest(unittest.TestCase):
    def test_bounds_match_the_binary(self):
        self.assertEqual(hsv.HUE_GRADE_BOUNDS,
                         (20.0, 40.0, 75.0, 155.0, 190.0, 270.0, 295.0, 316.0))

    def test_progression(self):
        cases = [(0.0, 0), (19.9, 0), (20.0, 1), (75.0, 3), (155.0, 4),
                 (270.0, 6), (295.0, 7), (359.9, 7)]
        for hue, expected in cases:
            self.assertEqual(hsv.hue_grade(hue), expected, msg=f"hue {hue}")

    def test_never_exceeds_seven(self):
        self.assertLessEqual(hsv.hue_grade(360.0), 7)

    def test_full_circle_constant(self):
        self.assertEqual(hsv.HUE_FULL_CIRCLE, 360.0)

    def test_low_band(self):
        self.assertEqual(hsv.low_band_grade(0.1), 0)
        self.assertEqual(hsv.low_band_grade(0.2), 1)
        self.assertEqual(hsv.low_band_grade(0.7), 2)

    def test_packed(self):
        self.assertEqual(hsv.packed_grade(0, 0, 0), 0)
        self.assertEqual(hsv.packed_grade(7, 2, 2), 71)


class BucketTest(unittest.TestCase):
    def test_ten_buckets(self):
        # 0x100096d6..0x1000971e
        self.assertEqual(len(hsv.SKIN_HUE_BUCKETS), 10)

    def test_exact_values(self):
        self.assertEqual(hsv.SKIN_HUE_BUCKETS,
                         frozenset({2, 4, 5, 7, 8, 11, 13, 14, 16, 17}))

    def test_skipped_buckets(self):
        for skipped in (0, 1, 3, 6, 9, 10, 12, 15):
            self.assertNotIn(skipped, hsv.SKIN_HUE_BUCKETS)

    def test_lookup(self):
        self.assertGreaterEqual(hsv.hue_bucket(5), 0)
        self.assertEqual(hsv.hue_bucket(3), -1)


class SkinPixelTest(unittest.TestCase):
    def test_order_is_required(self):
        # 0x1000974e / 0x1000975e: R > G > B
        self.assertTrue(hsv.is_skin_pixel(200, 150, 120))
        self.assertFalse(hsv.is_skin_pixel(120, 150, 200))

    def test_grey_fails_saturation(self):
        self.assertFalse(hsv.is_skin_pixel(128, 128, 128))

    def test_saturation_threshold(self):
        # 0x1000978b: 0.09
        self.assertEqual(hsv.SKIN_MIN_SATURATION, 0.09)

    def test_pure_red_is_not_skin(self):
        # Saturated but its hue grade does not land on a kept bucket.
        self.assertFalse(hsv.is_skin_pixel(255, 0, 0))

    def test_typical_skin_tones(self):
        for rgb in ((200, 150, 120), (240, 200, 170), (220, 170, 140)):
            self.assertTrue(hsv.is_skin_pixel(*rgb), msg=f"RGB{rgb}")

    def test_blue_is_not_skin(self):
        self.assertFalse(hsv.is_skin_pixel(10, 50, 200))


class SkinPassTest(unittest.TestCase):
    def test_all_skin(self):
        stats = hsv.skin_pass(bgr(4, 4, r=200, g=150, b=120), 4, 4)
        self.assertEqual(stats.pixels, 16)
        self.assertEqual(stats.skin, 16)
        self.assertAlmostEqual(stats.ratio, 1.0)

    def test_all_grey(self):
        stats = hsv.skin_pass(bgr(4, 4, r=128, g=128, b=128), 4, 4)
        self.assertEqual(stats.skin, 0)
        self.assertEqual(stats.rejected_by_order, 16)

    def test_counts_add_up(self):
        stats = hsv.skin_pass(bgr(4, 4, r=200, g=150, b=120), 4, 4)
        self.assertEqual(stats.skin + stats.rejected_by_order
                         + stats.rejected_by_saturation
                         + stats.rejected_by_hue, stats.pixels)

    def test_short_buffer_raises(self):
        with self.assertRaises(ValueError):
            hsv.skin_pass(b"\x00\x00", 2, 2)

    def test_stats_are_log_safe(self):
        stats = hsv.skin_pass(bgr(4, 4, r=200, g=150, b=120), 4, 4)
        blob = json.dumps(stats.to_dict())
        self.assertIn("ratio", blob)
        # No pixel values in the summary.
        self.assertNotIn("200", blob)


class NotReversedTest(unittest.TestCase):
    def test_rect_analysis_raises(self):
        """The rectangle stage is not ported; it must not silently pass."""
        with self.assertRaises(sk.NotReversed):
            sk.rect_analysis()

    def test_rect_analysis_message_names_the_address(self):
        with self.assertRaises(sk.NotReversed) as ctx:
            sk.rect_analysis()
        self.assertIn("0x10006ee6", str(ctx.exception))

    def test_not_reversed_is_not_implemented(self):
        # A caller catching NotImplementedError also catches this.
        self.assertTrue(issubclass(sk.NotReversed, NotImplementedError))


class ReturnCodeTest(unittest.TestCase):
    def test_codes_match_the_binary(self):
        self.assertEqual(sk.RC_ANTI_DEBUG, 100)
        self.assertEqual(sk.RC_NO_IMAGE_OBJECT, -21)
        self.assertEqual(sk.RC_LOAD_FAILED, -20)
        self.assertEqual(sk.RC_LONG_STRIP, 10)
        self.assertEqual(sk.RC_RECT_ANALYSIS, 3)


if __name__ == "__main__":
    unittest.main()
