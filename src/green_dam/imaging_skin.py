"""CHECKEX8x's gates and ratio branches, ported from CImage.dll.

Source: ``analysis/probe/p28_checkex8x.txt``. Every constant here was read out
of the binary and names the address it came from.

Where the pieces live
---------------------
======================  ====================================================
``imaging``             CAPSCREEN: float aspect 10.0, high-tone histogram
``imaging_checkex``     CHECKEX13x: grey, centre stddev 1.4, texture grades
``imaging_skin``        this file: CHECKEX8x gates and the ratio branch
``imaging_skin_hsv``    CHECKEX8x's skin classifier
======================  ====================================================

The three paths are different algorithms, not parameterisations of one. Keeping
them apart stops a reader treating one threshold as a correction of another.

What is not implemented
-----------------------
The rectangle analysis at ``0x10006ee6``. It feeds ``0.54`` and ``252`` into
``0x100094f0``, whose meaning is not recovered, and constructs a C++ object at
``0x10005250``. :func:`rect_analysis` raises rather than guessing.
"""
from __future__ import annotations

#: ``0x10007067``/``0x10007076``: both sides must exceed this before scaling.
MIN_DIMENSION = 160

#: ``0x1000792d``-style integer aspect gate, reused by this path.
MAX_ASPECT = 8

#: ``0x10007086``/``0x1000708c``: images above the gate are scaled so the width
#: becomes this. ``[0x1000d648]`` = 160.0.
RESIZE_WIDTH = 160

#: ``0x10006d83``: when the width is exactly 160 and width/height exceeds this,
#: the frame is rejected with code 10 before any further work.
LONG_STRIP_RATIO = 20

#: ``0x10006e0d``: ``[0x1000d650]`` = 0.08. Below this ratio1, return 0.
RATIO1_FLOOR = 0.08

#: ``0x10006e1e``: ``[0x1000d420]`` = 0.15. At or above, take the short branch.
RATIO1_BRANCH = 0.15

#: ``0x10006e88``: ``[0x1000d550]`` = 0.6. ratio2/ratio1 compared against this.
RATIO2_OVER_RATIO1 = 0.6

#: Return codes, from the branches at 0x10006d99 onward.
RC_REJECTED = 0
RC_SHORT_BRANCH = 1
RC_LONG_BRANCH = -1
RC_LONG_STRIP = 10
RC_RECT_ANALYSIS = 3
RC_NO_IMAGE_OBJECT = -21
RC_LOAD_FAILED = -20
RC_ANTI_DEBUG = 100

#: Sentinel from :func:`decide_ratio` meaning "continue to the rectangle stage".
FALLTHROUGH = -2


class NotReversed(NotImplementedError):
    """Raised for a stage whose semantics were not recovered."""


def aspect_ok(width: int, height: int) -> bool:
    """``0x10006db0``..``0x10006dd8``: >=32 either way, integer ratio <= 8."""
    if width < 32 or height < 32:
        return False
    return (height // width) <= MAX_ASPECT and (width // height) <= MAX_ASPECT


def needs_resize(width: int, height: int) -> bool:
    """``0x10007067``..``0x10007080``: both sides above 160 means scale down."""
    return width > MIN_DIMENSION and height > MIN_DIMENSION


def scaled_size(width: int, height: int) -> tuple[int, int]:
    """Target size after ``0x10007082``..``0x1000708c``.

    The original scales so the width becomes 160 and derives the height by
    ``160 / width * height``, truncated. Called only when
    :func:`needs_resize` is true; otherwise the input is returned unchanged.
    """
    if not needs_resize(width, height):
        return width, height
    return int(RESIZE_WIDTH), int(RESIZE_WIDTH / width * height)


def long_strip(width: int, height: int) -> bool:
    """``0x10006d6e``..``0x10006d86``: width 160 and width/height > 20.

    Such a frame returns code 10 immediately. The gate only applies when the
    width is exactly the resize target, which is why it sits before the general
    dimension checks.
    """
    if width != int(RESIZE_WIDTH) or height == 0:
        return False
    return (width // height) > LONG_STRIP_RATIO


def decide_ratio(ratio1: float, ratio2: float,
                 *, branch_result: bool | None = None) -> int:
    """The ratio branches at ``0x10006e09``..``0x10006eca``.

    Returns :data:`RC_REJECTED`, :data:`RC_SHORT_BRANCH`,
    :data:`RC_LONG_BRANCH`, or :data:`FALLTHROUGH`.

    ``branch_result`` stands in for ``0x10001ba0``, whose meaning is not
    recovered (IMG12). A caller must supply it rather than this module guessing;
    reaching a branch that needs it without one raises :class:`NotReversed`.
    """
    if ratio1 <= RATIO1_FLOOR:
        return RC_REJECTED

    def _branch() -> int:
        if branch_result is None:
            raise NotReversed(
                "0x10001ba0's result is required here; its semantics are not "
                "recovered (IMG12)")
        return RC_LONG_BRANCH if branch_result else RC_SHORT_BRANCH

    if ratio1 >= RATIO1_BRANCH:
        return _branch()

    if ratio1 == 0:
        return RC_REJECTED
    if ratio2 / ratio1 >= RATIO2_OVER_RATIO1:
        return _branch()

    return FALLTHROUGH


def rect_analysis(*_args, **_kwargs):
    """``0x10006ee6``..``0x10006f8a``: not implemented, deliberately.

    This stage feeds ``0.54`` and ``252`` into ``0x100094f0`` and constructs a
    C++ object at ``0x10005250``. Neither the constants' meaning nor the class
    contract is recovered, so there is nothing to port - only to guess. Raising
    keeps a caller from believing this check ran.
    """
    raise NotReversed(
        "rectangle analysis (0x10006ee6) is not reversed: 0.54/252 semantics "
        "unknown and 0x10005250 is an unreversed C++ object (IMG28, IMG29)")
