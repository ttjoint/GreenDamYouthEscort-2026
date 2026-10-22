"""Image classification as a pluggable backend.

Why this is an interface and not an implementation
--------------------------------------------------
``tests/test_contracts.py::DependencyTest`` forbids ``numpy``, ``cv2``,
``onnxruntime`` and ``torch`` anywhere in the engine, and architecture §A2 makes
the core pure standard library. That is not an obstacle to M4 - it is the design
M4 was specified against: the engine decides *what to do with a verdict*, and the
model that produces the verdict is supplied by the host.

So this module defines the contract and one reference implementation that needs
nothing. A host with onnxruntime supplies its own backend satisfying
:class:`ImageBackend`; the engine never imports it.

What the reference backend does, and does not do
------------------------------------------------
:class:`ReferenceBackend` implements only what can be computed from pixels with
the standard library: the arithmetic checks already ported in :mod:`imaging`,
:mod:`imaging_checkex` and :mod:`imaging_skin`. It returns ``nsfw=None`` -
"not assessed" - because none of those checks is an NSFW classifier, and
returning a number in [0, 1] would imply a capability that does not exist.

``assessed`` is a separate field from ``nsfw`` for exactly that reason: a caller
must be able to tell "the model says 0.05" from "no model ran". Collapsing those
two into ``nsfw=0.0`` is the same error as reporting an unread page as clean.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .imaging import HIGH_TONE_FLOOR, HIGH_TONE_THRESHOLD, ScreenResult
from .imaging_skin_hsv import SkinStats, skin_pass

#: Default decision threshold for an NSFW score. 0.5 is the conventional choice
#: for a binary classifier and is deliberately exposed rather than buried, so a
#: host can tune it without editing code.
DEFAULT_NSFW_THRESHOLD = 0.5

#: Reason tokens, stable so they can appear in logs.
NO_MODEL = "no_model"
BELOW_THRESHOLD = "below_threshold"
ABOVE_THRESHOLD = "above_threshold"
#: The reference backend cannot assess NSFW content at all.
NOT_ASSESSED = "not_assessed"


@dataclass(frozen=True)
class ImageVerdict:
    """What an image backend reports.

    ``nsfw`` is None when no model assessed the frame. ``assessed`` says whether
    a model ran; it is not derivable from ``nsfw`` being None because a model
    could legitimately return None on failure, and the two cases lead to
    different decisions.
    """

    #: NSFW probability in 0..1, or None when nothing assessed it.
    nsfw: float | None = None
    #: True only when a model produced ``nsfw``.
    assessed: bool = False
    #: True when the frame is judged to contain nudity above the threshold.
    flagged: bool = False
    #: Short token explaining the verdict; stable for logging.
    reason: str = NOT_ASSESSED
    #: Backend name, so a log line says which model spoke.
    backend: str = ""
    #: Arithmetic checks that ran, for callers that want the detail.
    screen: ScreenResult | None = None
    #: Skin-pass counts, when that check ran.
    skin: SkinStats | None = None
    #: Extra numbers a host backend wants to expose. Never pixel data.
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Counts, flags and scores only - safe to log."""
        return {
            "nsfw": None if self.nsfw is None else round(self.nsfw, 4),
            "assessed": self.assessed,
            "flagged": self.flagged,
            "reason": self.reason,
            "backend": self.backend,
            "metrics": {k: round(v, 4) if isinstance(v, float) else v
                        for k, v in self.metrics.items()},
        }


@runtime_checkable
class ImageBackend(Protocol):
    """A source of image verdicts.

    Deliberately tiny: one method, one dataclass. A host backend wrapping an
    ONNX session implements ``name`` and ``classify`` and nothing else.
    """

    name: str

    def classify(self, buffer: bytes, width: int, height: int) -> ImageVerdict:
        """Judge one BGR frame."""
        ...


def verdict_from_score(score: float | None, *,
                       threshold: float = DEFAULT_NSFW_THRESHOLD,
                       backend: str = "",
                       metrics: dict | None = None) -> ImageVerdict:
    """Turn a model score into a verdict, keeping "not assessed" distinct.

    A host backend calls this so the threshold logic exists once. Passing None
    yields an unassessed verdict rather than a zero score.
    """
    if score is None:
        return ImageVerdict(nsfw=None, assessed=False, flagged=False,
                            reason=NOT_ASSESSED, backend=backend,
                            metrics=dict(metrics or {}))

    if not 0.0 <= score <= 1.0:
        raise ValueError(f"NSFW score must be in 0..1, got {score!r}")

    flagged = score >= threshold
    return ImageVerdict(
        nsfw=score,
        assessed=True,
        flagged=flagged,
        reason=ABOVE_THRESHOLD if flagged else BELOW_THRESHOLD,
        backend=backend,
        metrics=dict(metrics or {}),
    )


@dataclass
class ReferenceBackend:
    """The zero-dependency backend: arithmetic checks only, no NSFW model.

    Useful for two things: it exercises the interface without any dependency, and
    it gives a host something to fall back to when no model is installed. It is
    explicit about not classifying NSFW content.
    """

    name: str = "reference"
    #: Run the skin pass. Off by default because it is the most expensive check
    #: and, on its own, is not a judgement.
    run_skin: bool = False
    threshold: float = DEFAULT_NSFW_THRESHOLD

    def classify(self, buffer: bytes, width: int, height: int) -> ImageVerdict:
        from .imaging import screen

        result = screen(width=width, height=height,
                        red_channel=_red_plane(buffer, width, height))
        stats = skin_pass(buffer, width, height) if self.run_skin else None

        metrics: dict = {
            "checks_run": len(result.checks_run),
            "fully_screened": 1 if result.fully_screened else 0,
            "suspect": 1 if result.suspect else 0,
        }
        if stats is not None:
            metrics["skin_ratio"] = stats.ratio

        # No NSFW assessment: the arithmetic checks are not a classifier, and
        # inventing a score from them would be a false claim.
        return ImageVerdict(
            nsfw=None,
            assessed=False,
            flagged=False,
            reason=NOT_ASSESSED,
            backend=self.name,
            screen=result,
            skin=stats,
            metrics=metrics,
        )


def _red_plane(buffer: bytes, width: int, height: int) -> bytes:
    """Extract the R channel from interleaved BGR.

    ``imaging.screen`` takes a contiguous plane; the high-tone check is defined
    on R (``0x10008e70`` reads ``ptr+2`` at stride 3), so extracting R here keeps
    the interface BGR-consistent for a host that already has a decoded frame.
    """
    needed = width * height * 3
    if len(buffer) < needed:
        raise ValueError(
            f"buffer has {len(buffer)} bytes, need {needed} for "
            f"{width}x{height} BGR")
    return bytes(buffer[i] for i in range(2, needed, 3))


def high_tone_fraction(red_plane: bytes) -> float:
    """Share of R values at or above the high-tone floor.

    Exposed because the reference backend's screen result does not carry it, and
    a host may want the number without a full :class:`ScreenResult`.
    """
    if not red_plane:
        return 0.0
    high = sum(1 for v in red_plane if v >= HIGH_TONE_FLOOR)
    return high / len(red_plane)


def looks_overexposed(red_plane: bytes,
                      threshold: float = HIGH_TONE_THRESHOLD) -> bool:
    """Whether the R plane is mostly near-white, per ``0x10008e70``."""
    return high_tone_fraction(red_plane) >= threshold


@dataclass
class ClassifierChain:
    """Runs several backends and combines them conservatively.

    Order matters only for short-circuiting: the first backend that *flags*
    wins, because a flag is a positive claim and does not need corroboration.
    A backend that cannot assess contributes nothing rather than a zero, so an
    unassessed chain reports unassessed rather than clean.
    """

    backends: list = field(default_factory=list)

    def add(self, backend) -> "ClassifierChain":
        self.backends.append(backend)
        return self

    def classify(self, buffer: bytes, width: int, height: int) -> ImageVerdict:
        if not self.backends:
            return ImageVerdict(reason=NO_MODEL, backend="")

        def merged(verdicts: list) -> dict:
            """Combine backend metrics, namespaced by backend so two backends
            reporting the same key do not silently overwrite each other."""
            out: dict = {"backends_run": len(verdicts)}
            for verdict in verdicts:
                for key, value in verdict.metrics.items():
                    if key in ("backends_run",):
                        continue
                    out[f"{verdict.backend}.{key}" if verdict.backend else key]                         = value
            return out

        verdicts: list[ImageVerdict] = []
        for backend in self.backends:
            verdict = backend.classify(buffer, width, height)
            verdicts.append(verdict)
            if verdict.flagged:
                return ImageVerdict(
                    nsfw=verdict.nsfw,
                    assessed=True,
                    flagged=True,
                    reason=ABOVE_THRESHOLD,
                    backend=verdict.backend,
                    screen=verdict.screen,
                    skin=verdict.skin,
                    metrics=merged(verdicts),
                )

        # Nothing flagged. If any backend assessed, report the highest score;
        # otherwise stay explicitly unassessed.
        assessed = [v for v in verdicts if v.assessed]
        if not assessed:
            return ImageVerdict(
                nsfw=None, assessed=False, flagged=False, reason=NOT_ASSESSED,
                backend=",".join(v.backend for v in verdicts if v.backend),
                screen=next((v.screen for v in verdicts if v.screen), None),
                skin=next((v.skin for v in verdicts if v.skin), None),
                metrics=merged(verdicts),
            )

        best = max(assessed, key=lambda v: v.nsfw or 0.0)
        return ImageVerdict(
            nsfw=best.nsfw,
            assessed=True,
            flagged=False,
            reason=BELOW_THRESHOLD,
            backend=best.backend,
            screen=next((v.screen for v in verdicts if v.screen), None),
            skin=next((v.skin for v in verdicts if v.skin), None),
            metrics=merged(verdicts),
        )
