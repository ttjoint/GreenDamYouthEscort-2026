"""Skin-tone classification, ported from CImage.dll's CHECKEX8x.

Source: ``analysis/probe/p28_checkex8x.txt``.

Why this exists as its own module
---------------------------------
p22 concluded the product had no skin detection, because the function it
examined (``0x10008e70``) turned out to be a high-tone check. That conclusion was
wrong: skin detection exists, in ``0x100096c0``, and it works in HSV rather than
by histogramming.

Splitting it out keeps that correction visible. A reader looking for skin
detection finds a module named for it, instead of having to know that it hides
inside an image module under a name that says nothing about skin.

The three conditions, from the original
---------------------------------------
1. ``R > G > B`` (``0x1000974e``/``0x1000975e``) - the cheap test, applied before
   any HSV work.
2. saturation above 0.09 (``0x1000978b``).
3. the packed hue grade lands on one of ten kept buckets
   (``0x100096d6``..``0x1000971e``).

All three must hold. Failing any one sets the pixel to black in place.
"""
from __future__ import annotations

from dataclasses import dataclass

#: ``0x1000978b``: ``[0x1000d530]`` = 0.09. Saturation must exceed this.
SKIN_MIN_SATURATION = 0.09

#: ``0x100096d6``..``0x1000971e``: the hue buckets kept as skin. The original
#: stores ten dwords: 2, 4, 5, 7, 8, 0xb, 0xd, 0xe, 0x10, 0x11. Buckets 0, 1, 3,
#: 6, 9, 10, 12, 15 are dropped.
SKIN_HUE_BUCKETS = frozenset({2, 4, 5, 7, 8, 11, 13, 14, 16, 17})

#: ``0x10009360``: the hue grade bounds, identical to the texture bounds in
#: ``imaging_checkex``. A hue of 0..360 is graded 0..7 by these.
HUE_GRADE_BOUNDS = (20.0, 40.0, 75.0, 155.0, 190.0, 270.0, 295.0, 316.0)

#: ``[0x1000d4f8]`` and ``[0x1000d500]``: a full hue circle and one band.
HUE_FULL_CIRCLE = 360.0
HUE_BAND = 60.0

#: ``0x1000d4c0`` / ``0x1000d4b8``: the two low-band bounds, same as
#: ``imaging_checkex``. Used for the second and third packed grades.
LOW_BAND_LOWER = 0.2
LOW_BAND_UPPER = 0.7


@dataclass(frozen=True)
class SkinStats:
    """Counts from one skin pass. No pixel data, so this is log-safe."""

    pixels: int
    skin: int
    rejected_by_order: int
    rejected_by_saturation: int
    rejected_by_hue: int

    @property
    def ratio(self) -> float:
        return self.skin / self.pixels if self.pixels else 0.0

    def to_dict(self) -> dict:
        return {
            "pixels": self.pixels,
            "skin": self.skin,
            "rejected_order": self.rejected_by_order,
            "rejected_saturation": self.rejected_by_saturation,
            "rejected_hue": self.rejected_by_hue,
            "ratio": round(self.ratio, 4),
        }


def hue_grade(hue: float) -> int:
    """``0x10009360``: grade a hue 0..360 into 0..7.

    Same bounds as the texture grade, different input. Values below 20 are
    grade 0; at or above 316 they are grade 7.
    """
    grade = 0
    for bound in HUE_GRADE_BOUNDS:
        if hue >= bound:
            grade += 1
        else:
            break
    return min(grade, len(HUE_GRADE_BOUNDS) - 1)


def low_band_grade(value: float) -> int:
    """Second and third inputs of the packed grade, bounds 0.2 and 0.7."""
    if value < LOW_BAND_LOWER:
        return 0
    if value < LOW_BAND_UPPER:
        return 1
    return 2


def packed_grade(g1: int, g2: int, g3: int) -> int:
    """``0x10009463``..``0x1000949a``: ``9*g1 + 3*g2 + g3``."""
    return 9 * g1 + 3 * g2 + g3


def hue_bucket(packed: int) -> int:
    """Which of the ten kept buckets a packed grade lands in.

    The original compares the packed value against the ten dwords in order and
    keeps the pixel if any matches. This returns the bucket index when it does,
    or -1 when it does not, so the caller can count rejections.
    """
    for index, value in enumerate(sorted(SKIN_HUE_BUCKETS)):
        if value == packed:
            return index
    return -1


def rgb_to_hsv(r: int, g: int, b: int) -> tuple[float, float, float]:
    """``0x10009090``: hue in degrees, saturation, value.

    The original returns all zero when max is zero (``0x10009115``), which this
    matches. Channels outside 0..255 are rejected the way ``0x100090a1`` does.
    """
    if not (0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255):
        raise ValueError(f"channels must be 0..255, got {(r, g, b)}")

    hi = max(r, g, b)
    lo = min(r, g, b)
    if hi == 0:
        return 0.0, 0.0, 0.0

    value = hi / 255.0
    saturation = (hi - lo) / hi
    if hi == lo:
        return 0.0, saturation, value

    delta = hi - lo
    if hi == r:
        hue = 60.0 * (((g - b) / delta) % 6)
    elif hi == g:
        hue = 60.0 * ((b - r) / delta + 2)
    else:
        hue = 60.0 * ((r - g) / delta + 4)
    return hue % 360.0, saturation, value


def is_skin_pixel(r: int, g: int, b: int) -> bool:
    """The three conditions from ``0x100096c0``.

    ``0x1000974e``/``0x1000975e`` require ``R > G > B``; ``0x1000978b`` requires
    saturation above 0.09; the packed hue grade must land on one of the ten kept
    buckets. All three must hold.
    """
    if not (r > g > b):
        return False
    hue, saturation, value = rgb_to_hsv(r, g, b)
    if saturation <= SKIN_MIN_SATURATION:
        return False
    packed = packed_grade(hue_grade(hue), low_band_grade(saturation),
                          low_band_grade(value))
    return hue_bucket(packed) >= 0


def skin_pass(buffer: bytes, width: int, height: int, *,
              start: int = 0) -> SkinStats:
    """Classify every pixel of a BGR frame.

    Mirrors the loop at ``0x10009746``: stride 3, three bytes per pixel, and the
    cheap ``R > G > B`` test before the HSV conversion.
    """
    needed = width * height * 3
    if len(buffer) - start < needed:
        raise ValueError(
            f"buffer has {len(buffer) - start} usable bytes, need {needed} "
            f"for {width}x{height} BGR")

    skin = order = saturation = hue_reject = 0
    for i in range(start, start + needed, 3):
        b, g, r = buffer[i], buffer[i + 1], buffer[i + 2]
        if not (r > g > b):
            order += 1
            continue
        hue, sat, val = rgb_to_hsv(r, g, b)
        if sat <= SKIN_MIN_SATURATION:
            saturation += 1
            continue
        packed = packed_grade(hue_grade(hue), low_band_grade(sat),
                              low_band_grade(val))
        if hue_bucket(packed) < 0:
            hue_reject += 1
            continue
        skin += 1

    return SkinStats(pixels=width * height, skin=skin,
                     rejected_by_order=order,
                     rejected_by_saturation=saturation,
                     rejected_by_hue=hue_reject)
