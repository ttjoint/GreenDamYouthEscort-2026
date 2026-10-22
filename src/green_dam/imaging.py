"""Image screening, ported from the two functions that were actually decodable.

Scope, stated plainly: this reproduces the two checks whose logic could be read
out of ``CImage.dll`` byte by byte. It does **not** reproduce the Haar cascade
classifier, because that lives in ``FImage.dll`` behind an OpenCV runtime and is
a trained model, not logic - see "What is deliberately absent" below.

The two checks (``analysis/probe/p22``, constants read from the binary)
-----------------------------------------------------------------------
``aspect_ok`` - from ``0x10008e20``::

    if width < 32 or height < 32: return True      # too small to judge
    return max(w, h) / min(w, h) < 10.0            # 10.0 = [0x1000dc48]

``high_tone_ratio`` - from ``0x10008e70``::

    # R channel of a BGR buffer, one byte per pixel, step 3
    hist[256] over R; normalise by pixel count
    high = sum(hist[210..255]); low = sum(hist[0..209])
    return high / (low + high)

    suspect when high / (low + high) >= 0.9        # 0.9 = [0x1000d540]

What the second check actually is
---------------------------------
An earlier note in this repository called it "skin-tone detection". It is not:
it sums the *brightest* R buckets and fires when they dominate, which is an
over-exposure / near-white-frame test. The doc has been corrected; this module
implements the measured behaviour, not the earlier guess.

What is deliberately absent
---------------------------
The Haar cascade. ``FImage.dll`` loads ``XFimage.xml`` (a
``haarcascade_frontalface_alt2`` derivative) through an OpenCV runtime. That is a
*trained model*: porting it means porting OpenCV's cascade evaluator plus the
model weights, which is a different project from reading two arithmetic
functions. It is left out rather than stubbed, and ``ScreenResult`` says so
explicitly via ``checks_run`` so a caller cannot mistake "two checks passed" for
"the image is clean".

That distinction is the point of this module. The original returned a bare int,
so "clean" and "not fully checked" were indistinguishable - the same failure
mode as ``gated`` versus ``allow`` in the event log.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

#: Below this, the original refused to judge an image (``0x8e24``/``0x8e2d``).
MIN_DIMENSION = 32

#: Long-side / short-side ratio at or above which the frame is "too thin".
#: Read from ``[0x1000dc48]``: 10.0.
MAX_ASPECT_RATIO = 10.0

#: R bucket at and above which a pixel counts as "high tone". The original
#: summed ``0..0xd1`` and ``0xd2..0xff``, i.e. the split is at 210.
HIGH_TONE_FLOOR = 210

#: Share of high-tone pixels at or above which the frame is suspect.
#: Read from ``[0x1000d540]``: 0.9.
HIGH_TONE_THRESHOLD = 0.9

class Check(str, Enum):
    """Which check produced a verdict. Values are stable log tokens."""

    TOO_SMALL = "too_small"
    ASPECT = "aspect"
    CHECKEX_ASPECT = "checkex_aspect"
    TEXTURE = "texture"
    HIGH_TONE = "high_tone"


@dataclass(frozen=True)
class ScreenResult:
    """Outcome of screening one frame.

    ``suspect`` means at least one check fired. ``checks_run`` lists what was
    actually evaluated, so a caller can tell a fully-screened image from one
    that was too small to judge. ``detail`` is a short machine token.
    """

    suspect: bool
    fired: tuple[Check, ...] = ()
    checks_run: tuple[Check, ...] = ()
    detail: str = ""
    #: Numeric values the checks computed, for logging without content.
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def fully_screened(self) -> bool:
        """True when every check this module implements actually ran.

        A frame that was too small, or one supplied without its R plane, was
        not fully screened - and a caller must not read that as "clean".
        """
        return (Check.TOO_SMALL not in self.checks_run
                and Check.HIGH_TONE in self.checks_run)


def aspect_ratio(width: int, height: int) -> float:
    """Long side over short side. Matches ``0x10008e20``'s compare-then-divide."""
    if width <= 0 or height <= 0:
        raise ValueError(f"dimensions must be positive: {width}x{height}")
    long_side, short_side = max(width, height), min(width, height)
    return long_side / short_side


def aspect_ok(width: int, height: int) -> bool:
    """True when the frame is worth judging at all (``0x10008e20``)."""
    if width < MIN_DIMENSION or height < MIN_DIMENSION:
        return True                      # original: return 1, i.e. "not abnormal"
    return aspect_ratio(width, height) < MAX_ASPECT_RATIO


def high_tone_ratio(red_channel: bytes, pixel_count: int) -> float:
    """Share of pixels whose R value is >= :data:`HIGH_TONE_FLOOR`.

    ``red_channel`` is one byte per pixel (the caller has already de-interleaved
    a BGR buffer, as the original did with ``ptr + 2`` and a stride of 3).
    """
    if pixel_count <= 0:
        raise ValueError("pixel_count must be positive")
    if len(red_channel) < pixel_count:
        raise ValueError(
            f"red_channel has {len(red_channel)} bytes, need {pixel_count}")
    high = sum(1 for value in red_channel[:pixel_count]
               if value >= HIGH_TONE_FLOOR)
    return high / pixel_count


def screen(
    *,
    width: int,
    height: int,
    red_channel: bytes | None = None,
) -> ScreenResult:
    """Run the ported checks on one frame.

    ``red_channel`` is optional. When absent the high-tone check is skipped and
    ``checks_run`` says so - the result is then a partial screen, never a claim
    of cleanliness.
    """
    fired: list[Check] = []
    run: list[Check] = []
    metrics: dict[str, float] = {}

    if width < MIN_DIMENSION or height < MIN_DIMENSION:
        # Not a check that ran; a check that refused to run. Recorded so a
        # caller can tell why nothing was evaluated.
        return ScreenResult(
            suspect=False,
            fired=(),
            checks_run=(Check.TOO_SMALL,),
            detail="too_small",
            metrics={"width": float(width), "height": float(height)},
        )

    run.append(Check.ASPECT)
    ratio = aspect_ratio(width, height)
    metrics["aspect_ratio"] = ratio
    if ratio >= MAX_ASPECT_RATIO:
        fired.append(Check.ASPECT)

    if red_channel is not None:
        run.append(Check.HIGH_TONE)
        share = high_tone_ratio(red_channel, width * height)
        metrics["high_tone_ratio"] = share
        if share >= HIGH_TONE_THRESHOLD:
            fired.append(Check.HIGH_TONE)

    detail = fired[0].value if fired else (
        "partial" if Check.HIGH_TONE not in run else "clean")
    return ScreenResult(
        suspect=bool(fired),
        fired=tuple(fired),
        checks_run=tuple(run),
        detail=detail,
        metrics=metrics,
    )


def red_channel_from_bgr(buffer: bytes, width: int, height: int,
                         *, start: int = 0) -> bytes:
    """Extract the R plane from an interleaved BGR buffer.

    The original read ``ptr + 2`` with a stride of 3 (``0x8e99``/``0x8ea6``),
    i.e. it assumed 3-byte BGR with no row padding. This helper makes that
    assumption explicit instead of burying it in pointer arithmetic.
    """
    needed = width * height * 3
    if len(buffer) - start < needed:
        raise ValueError(
            f"buffer has {len(buffer) - start} usable bytes, need {needed} "
            f"for {width}x{height} BGR")
    return bytes(buffer[start + 2:start + needed:3])
