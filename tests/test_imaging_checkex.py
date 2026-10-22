"""Tests for the CHECKEX13x judgement path, ported from CImage.dll.

Source: analysis/probe/p27_checkex13x.txt. Every constant asserted here was
read out of the binary; the test names say which address it came from so a
future reader can check one without re-reading the whole document.

Frames are synthesised. No image file is read and nothing here touches disk.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam import imaging  # noqa: E402
from green_dam import imaging_checkex as im  # noqa: E402


def bgr(width: int, height: int, *, b: int = 0, g: int = 0,
        r: int = 0) -> bytes:
    """A flat BGR frame of one colour."""
    return bytes([b, g, r]) * (width * height)


class GateTest(unittest.TestCase):
    """0x1000790e..0x1000793e: dimension and integer aspect gates."""

    def test_min_dimension_is_32(self):
        self.assertEqual(im.CHECKEX_MIN_DIMENSION, 32)

    def test_rejects_below_minimum(self):
        self.assertFalse(im.checkex_aspect_ok(31, 100))
        self.assertFalse(im.checkex_aspect_ok(100, 31))

    def test_accepts_at_minimum(self):
        self.assertTrue(im.checkex_aspect_ok(32, 32))

    def test_integer_aspect_threshold_is_8(self):
        # Read from `cmp eax, 8` at 0x1000792d / 0x1000793b.
        self.assertEqual(im.CHECKEX_MAX_ASPECT, 8)

    def test_square_passes(self):
        self.assertTrue(im.checkex_aspect_ok(100, 100))

    def test_eight_to_one_passes(self):
        # 800 // 100 == 8, and the test is `jg` (greater than), so 8 is allowed.
        self.assertTrue(im.checkex_aspect_ok(800, 100))

    def test_nine_to_one_fails(self):
        self.assertFalse(im.checkex_aspect_ok(900, 100))
        self.assertFalse(im.checkex_aspect_ok(100, 900))

    def test_uses_truncated_division(self):
        # 899 // 100 == 8, so 899x100 still passes even though the true ratio
        # is 8.99. Integer division is what the original does.
        self.assertTrue(im.checkex_aspect_ok(899, 100))
        self.assertFalse(im.checkex_aspect_ok(900, 100))

    def test_differs_from_the_float_check(self):
        """The two aspect checks are separate, not contradictory.

        ``imaging.aspect_ok`` (0x10008e20) uses a float 10.0; this one uses
        integer 8.
        A 9:1 frame passes the first and fails the second.
        """
        self.assertTrue(imaging.aspect_ok(900, 100))
        self.assertFalse(im.checkex_aspect_ok(900, 100))

    def test_zero_dimension_is_not_ok(self):
        self.assertFalse(im.checkex_aspect_ok(0, 100))
        self.assertFalse(im.checkex_aspect_ok(100, 0))


class GreyTest(unittest.TestCase):
    """0x10007270: BGR -> grey with 0.11/0.30/0.59 and +0.5."""

    def test_weights(self):
        self.assertAlmostEqual(im.GREY_WEIGHT_B, 0.11)
        self.assertAlmostEqual(im.GREY_WEIGHT_G, 0.30)
        self.assertAlmostEqual(im.GREY_WEIGHT_R, 0.59)
        self.assertAlmostEqual(im.GREY_ROUNDING, 0.5)

    def test_weights_are_not_the_ntsc_values(self):
        # The original uses 2-digit approximations, not 0.114/0.299/0.587.
        self.assertNotAlmostEqual(im.GREY_WEIGHT_B, 0.114, places=4)
        self.assertNotAlmostEqual(im.GREY_WEIGHT_R, 0.587, places=4)

    def test_black(self):
        self.assertEqual(im.grey_plane(bgr(2, 2), 2, 2), [0.0, 0.0, 0.0, 0.0])

    def test_white(self):
        # 0.11 + 0.30 + 0.59 = 1.00, *255 = 255, +0.5 -> 255.5 -> 255
        got = im.grey_plane(bgr(1, 1, b=255, g=255, r=255), 1, 1)
        self.assertEqual(got, [255.0])

    def test_pure_red_is_dominated_by_the_r_weight(self):
        got = im.grey_plane(bgr(1, 1, r=255), 1, 1)
        self.assertEqual(got, [float(int(255 * 0.59 + 0.5))])

    def test_pure_blue(self):
        got = im.grey_plane(bgr(1, 1, b=255), 1, 1)
        self.assertEqual(got, [float(int(255 * 0.11 + 0.5))])

    def test_rounds_half_up(self):
        # 0.5 is added before truncation, so this is round-half-up.
        got = im.grey_plane(bytes([0, 0, 1]), 1, 1)
        self.assertEqual(got, [float(int(1 * 0.59 + 0.5))])

    def test_short_buffer_raises(self):
        with self.assertRaises(ValueError):
            im.grey_plane(b"\x00\x00", 2, 2)

    def test_length_matches_pixel_count(self):
        self.assertEqual(len(im.grey_plane(bgr(4, 3), 4, 3)), 12)


class CentreWindowTest(unittest.TestCase):
    """0x10007487..0x100074b6: centre-3 .. centre+4, clamped."""

    def test_window_size(self):
        x0, y0, x1, y1 = im.centre_window(100, 100)
        self.assertEqual((x1 - x0 + 1, y1 - y0 + 1), (8, 8))

    def test_window_is_centred(self):
        x0, y0, x1, y1 = im.centre_window(100, 100)
        self.assertEqual((x0, y0), (47, 47))
        self.assertEqual((x1, y1), (54, 54))

    def test_clamps_at_edges(self):
        x0, y0, x1, y1 = im.centre_window(8, 8)
        self.assertGreaterEqual(x0, 0)
        self.assertGreaterEqual(y0, 0)
        self.assertLess(x1, 8)
        self.assertLess(y1, 8)

    def test_offsets_are_3_and_4(self):
        self.assertEqual(im.CENTRE_WINDOW_BEFORE, 3)
        self.assertEqual(im.CENTRE_WINDOW_AFTER, 4)


class StddevTest(unittest.TestCase):
    """0x10002790: population stddev of the centre window."""

    def test_threshold_is_1_4(self):
        self.assertAlmostEqual(im.CENTRE_STDDEV_THRESHOLD, 1.4)

    def test_flat_frame_has_zero_stddev(self):
        grey = [128.0] * (40 * 40)
        self.assertEqual(im.centre_stddev(grey, 40, 40), 0.0)

    def test_population_not_sample(self):
        """Dividing by n-1 would change borderline frames.

        The window has 64 pixels, so the difference is small but real; the
        assertion recomputes the population formula independently rather than
        trusting the implementation.
        """
        grey = [0.0] * (40 * 40)
        x0, y0, _x1, _y1 = im.centre_window(40, 40)
        grey[y0 * 40 + x0] = 2.0
        values = [grey[y * 40 + x] for y in range(y0, im.centre_window(40, 40)[3] + 1)
                  for x in range(x0, im.centre_window(40, 40)[2] + 1)]
        mean = sum(values) / len(values)
        expected = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
        self.assertAlmostEqual(im.centre_stddev(grey, 40, 40), expected)

    def test_noisy_frame_has_positive_stddev(self):
        grey = [float(i % 200) for i in range(40 * 40)]
        self.assertGreater(im.centre_stddev(grey, 40, 40), 0.0)


class GradeTest(unittest.TestCase):
    """0x100027e0: the eight bounds and the packed result."""

    def test_bounds_match_the_binary(self):
        self.assertEqual(
            im.TEXTURE_GRADE_BOUNDS,
            (20.0, 40.0, 75.0, 155.0, 190.0, 270.0, 295.0, 316.0))

    def test_grade_progression(self):
        cases = [(0.0, 0), (19.9, 0), (20.0, 1), (39.9, 1), (40.0, 2),
                 (75.0, 3), (155.0, 4), (190.0, 5), (270.0, 6), (295.0, 7),
                 (316.0, 7), (1000.0, 7)]
        for value, expected in cases:
            self.assertEqual(im.texture_grade(value), expected,
                             msg=f"value {value}")

    def test_grade_never_exceeds_seven(self):
        for value in (316.0, 1e6, float("inf")):
            self.assertLessEqual(im.texture_grade(value), 7)

    def test_weights_match_the_binary(self):
        self.assertEqual(len(im.TEXTURE_GRADE_WEIGHTS), 8)
        for grade in (0, 1, 3, 6):
            self.assertEqual(im.texture_grade_weight(grade), 0.0,
                             msg=f"grade {grade} should have zero weight")
        for grade in (2, 4, 5, 7):
            self.assertGreater(im.texture_grade_weight(grade), 0.0)

    def test_low_band(self):
        self.assertEqual(im.LOW_BAND_LOWER, 0.2)
        self.assertEqual(im.LOW_BAND_UPPER, 0.7)
        self.assertEqual(im.low_band_grade(0.1), 0)
        self.assertEqual(im.low_band_grade(0.2), 1)
        self.assertEqual(im.low_band_grade(0.5), 1)
        self.assertEqual(im.low_band_grade(0.7), 2)

    def test_packed_grade_is_base_three(self):
        # 0x10002955: 3*(3*g1 + g2) + g3
        self.assertEqual(im.packed_grade(0, 0, 0), 0)
        self.assertEqual(im.packed_grade(1, 0, 0), 9)
        self.assertEqual(im.packed_grade(0, 1, 0), 3)
        self.assertEqual(im.packed_grade(0, 0, 1), 1)
        self.assertEqual(im.packed_grade(7, 2, 2), 71)

    def test_packed_grade_range(self):
        self.assertEqual(im.packed_grade(0, 0, 0), 0)
        self.assertEqual(im.packed_grade(7, 2, 2), 71)


class TextureOkTest(unittest.TestCase):
    def test_below_threshold_fails(self):
        self.assertFalse(im.texture_ok(1.39))

    def test_zero_weight_grade_fails(self):
        # stddev 1.5 is above 1.4 but grades 0, so the weight is zero.
        self.assertEqual(im.texture_grade(1.5), 0)
        self.assertFalse(im.texture_ok(1.5))

    def test_zero_weight_mid_grade_fails(self):
        # 80 grades 3, whose weight is 0.0.
        self.assertEqual(im.texture_grade(80.0), 3)
        self.assertFalse(im.texture_ok(80.0))

    def test_weighted_grade_passes(self):
        # 200 grades 5, whose weight is 0.749...
        self.assertEqual(im.texture_grade(200.0), 5)
        self.assertTrue(im.texture_ok(200.0))


class ConstantsTest(unittest.TestCase):
    def test_judge_dimension_gate(self):
        # 0x10007f88: `mov ecx, 0x50`
        self.assertEqual(im.CHECKEX_MIN_JUDGE_DIMENSION, 0x50)

    def test_high_tone_constants_unchanged(self):
        # These come from p22/p27 and must not drift.
        self.assertEqual(imaging.HIGH_TONE_FLOOR, 210)
        self.assertAlmostEqual(imaging.HIGH_TONE_THRESHOLD, 0.9)

    def test_float_aspect_unchanged(self):
        self.assertAlmostEqual(imaging.MAX_ASPECT_RATIO, 10.0)


if __name__ == "__main__":
    unittest.main()
