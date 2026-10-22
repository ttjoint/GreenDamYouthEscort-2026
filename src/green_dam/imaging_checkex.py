"""CHECKEX13x's judgement path, ported from CImage.dll.

Why this is separate from :mod:`imaging`
---------------------------------------
``imaging`` holds the two checks that ``CAPSCREEN`` uses: the float aspect ratio
(``0x10008e20``) and the high-tone histogram (``0x10008e70``). This module holds
the different set that ``CHECKEX13x`` uses. They are not variations of one
another - they disagree on purpose:

* ``imaging.MAX_ASPECT_RATIO`` is a float 10.0
* ``CHECKEX_MAX_ASPECT`` here is an integer 8

Keeping them in one file invited the reading that the 10.0 was wrong. It is not;
it belongs to a different function. Splitting the modules makes that structural
rather than something a comment has to explain.

Source: ``analysis/probe/p27_checkex13x.txt``. Every constant below was read out
of the binary, and the docstring on each says which address it came from.
"""
from __future__ import annotations

#: ``0x1000790e``/``0x1000791f``: both dimensions must reach this.
CHECKEX_MIN_DIMENSION = 32

#: ``0x1000792d``/``0x1000793b``: integer long/short ratio must not exceed this.
#: Note this is 8, not 10, and the comparison is on truncated integers.
CHECKEX_MAX_ASPECT = 8

#: ``0x10007f88``: before the main judgement, both sides must reach 0x50.
CHECKEX_MIN_JUDGE_DIMENSION = 0x50

#: Grey weights from ``0x10007270``. BGR order, read off
#: ``[0x1000d678]``=0.11, ``[0x1000d670]``=0.30, ``[0x1000d668]``=0.59.
GREY_WEIGHT_B = 0.11
GREY_WEIGHT_G = 0.30
GREY_WEIGHT_R = 0.59
GREY_ROUNDING = 0.5

#: ``0x1000752a``: stddev of the centre 7x7 window is compared against this.
CENTRE_STDDEV_THRESHOLD = 1.4

#: ``0x10007487``..``0x100074b6``: the window is centre-3 .. centre+4.
CENTRE_WINDOW_BEFORE = 3
CENTRE_WINDOW_AFTER = 4

#: ``0x100027e0``: eight upper bounds producing a grade 0..7. Ordered ascending
#: as the code tests them; a value below the first bound is grade 0.
TEXTURE_GRADE_BOUNDS = (20.0, 40.0, 75.0, 155.0, 190.0, 270.0, 295.0, 316.0)

#: ``[0x1000d4c0]`` and ``[0x1000d4b8]``: the two bounds for the 0..2 grades.
LOW_BAND_LOWER = 0.2
LOW_BAND_UPPER = 0.7

#: ``0x1000d110``: per-grade weight. Grades 0, 1, 3 and 6 are zero, and a zero
#: weight fails the check at ``0x1000745f``.
TEXTURE_GRADE_WEIGHTS = (
    0.0,
    0.0,
    0.062198932,
    0.0,
    0.229786405,
    0.749116911,
    0.0,
    0.022231397,
)


def checkex_aspect_ok(width: int, height: int) -> bool:
    """``0x100078e0``'s gate: integer aspect must not exceed 8 either way.

    Uses truncated integer division, as the original does with ``idiv``. A
    3:1 image passes; a 9:1 image fails even though it is under the float 10.0
    used by ``aspect_ok``. Both checks exist in the original.
    """
    if width < CHECKEX_MIN_DIMENSION or height < CHECKEX_MIN_DIMENSION:
        return False
    if height == 0 or width == 0:
        return False
    return (height // width) <= CHECKEX_MAX_ASPECT and         (width // height) <= CHECKEX_MAX_ASPECT


def grey_plane(buffer: bytes, width: int, height: int, *,
               start: int = 0) -> list[float]:
    """Convert interleaved BGR to a grey plane, matching ``0x10007270``.

    The original stores the result as ``double`` per pixel and adds 0.5 before
    truncating, which is round-half-up rather than a cast. Reproducing the
    arithmetic exactly matters because the downstream stddev is compared against
    a tight threshold (1.4).
    """
    needed = width * height * 3
    if len(buffer) - start < needed:
        raise ValueError(
            f"buffer has {len(buffer) - start} usable bytes, need {needed} "
            f"for {width}x{height} BGR")
    out: list[float] = []
    for i in range(start, start + needed, 3):
        b = buffer[i]
        g = buffer[i + 1]
        r = buffer[i + 2]
        value = (b * GREY_WEIGHT_B + g * GREY_WEIGHT_G + r * GREY_WEIGHT_R
                 + GREY_ROUNDING)
        out.append(float(int(value)))
    return out


def centre_window(width: int, height: int) -> tuple[int, int, int, int]:
    """The 7x7 centre box as ``(x0, y0, x1, y1)``, inclusive.

    Bounds are clamped the way ``0x10007487``..``0x100074b6`` clamp them, so a
    small image gets a window smaller than 7x7 rather than an out-of-range read.
    """
    cx, cy = width // 2, height // 2
    x0 = max(0, cx - CENTRE_WINDOW_BEFORE)
    x1 = min(width - 1, cx + CENTRE_WINDOW_AFTER)
    y0 = max(0, cy - CENTRE_WINDOW_BEFORE)
    y1 = min(height - 1, cy + CENTRE_WINDOW_AFTER)
    return x0, y0, x1, y1


def centre_stddev(grey: list[float], width: int, height: int) -> float:
    """Population stddev of the centre window, matching ``0x10002790``.

    ``0x10002790`` computes ``sqrt(sum((x-mean)^2)/n)`` with ``n`` the pixel
    count - population, not sample. Dividing by ``n-1`` would change the result
    enough to cross the 1.4 threshold on borderline frames.
    """
    x0, y0, x1, y1 = centre_window(width, height)
    values = [grey[y * width + x] for y in range(y0, y1 + 1)
              for x in range(x0, x1 + 1)]
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return variance ** 0.5


def texture_grade(value: float) -> int:
    """Map a value to grade 0..7 using ``0x100027e0``'s bounds.

    Read off the branch chain at ``0x100027e4``..``0x100028df``: each bound
    reached raises the grade, and the chain tests them in ascending order. A
    value below 20 is grade 0; at or above 316 it is grade 7.
    """
    grade = 0
    for bound in TEXTURE_GRADE_BOUNDS:
        if value >= bound:
            grade += 1
        else:
            break
    return min(grade, len(TEXTURE_GRADE_BOUNDS) - 1)


def low_band_grade(value: float) -> int:
    """Map a value to grade 0..2 using the second half of ``0x100027e0``.

    Bounds are ``[0x1000d4c0]``=0.2 and ``[0x1000d4b8]``=0.7, from the chain at
    ``0x100028e7``. Used for the second and third inputs of the packed code.
    """
    if value < LOW_BAND_LOWER:
        return 0
    if value < LOW_BAND_UPPER:
        return 1
    return 2


def packed_grade(g1: int, g2: int, g3: int) -> int:
    """Combine three grades the way ``0x10002955`` does.

    The original computes ``3*(3*g1 + g2) + g3`` across
    ``lea eax,[esi+ecx*2]`` / ``lea ecx,[edx+eax*2]``. That is base-3 packing of
    a 0..7 value and two 0..2 values, giving 0..71.
    """
    return 9 * g1 + 3 * g2 + g3


def texture_grade_weight(grade: int) -> float:
    """Weight for a grade from ``0x1000d110``; zero means the check fails."""
    if not 0 <= grade < len(TEXTURE_GRADE_WEIGHTS):
        return 0.0
    return TEXTURE_GRADE_WEIGHTS[grade]


def texture_ok(stddev: float) -> bool:
    """Whether the centre-window texture passes both tests.

    ``0x1000752a`` rejects stddev < 1.4, then ``0x1000745f`` rejects a grade
    whose weight is zero. Both must pass, so this returns False if either fails.
    """
    if stddev < CENTRE_STDDEV_THRESHOLD:
        return False
    return texture_grade_weight(texture_grade(stddev)) > 0.0
