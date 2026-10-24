"""What this machine can actually do, measured rather than assumed.

The host has a long list of optional pieces - pywin32, cryptography, psutil,
pywebview, pystray, onnxruntime, Pillow, uiautomation, the WinRT OCR binding -
and ``docs/dependency-audit.md`` says which module each one serves. A missing
one is not an error: it degrades exactly one feature, and the module it belongs
to reports why.

Why this module exists rather than scattered ``try: import`` blocks: the
alternative is that the first symptom of a missing dependency is a traceback
from deep inside a request handler, hours later. Probing once, up front, and
reporting the result is the difference between "M5 is unavailable because the
WinRT OCR binding is missing" and "something went wrong".

The probe imports each candidate by name inside a ``try``. Import failures are
caught, but *other* exceptions are not swallowed silently - a package that is
installed but broken is a different fact from one that is absent, and the two
need different fixes. Both are reported, distinguished by :attr:`Feature.state`.
"""
from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field

__all__ = [
    "Feature",
    "Capabilities",
    "REQUIRED",
    "OPTIONAL",
    "probe",
]


@dataclass(frozen=True)
class Feature:
    """One optional capability, and whether this machine has it.

    ``state`` is one of:

    ``present``    imported successfully
    ``absent``     not installed - the feature degrades, which is expected
    ``broken``     installed but failed to import - needs a fix, not a shrug
    ``wrong_os``   not even attempted, because the platform is not Windows
    """

    name: str
    module: str
    #: Which module in ``docs/architecture.md`` this serves ("M4", "M5", ...).
    serves: str
    state: str
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.state == "present"

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "module": self.module,
                "serves": self.serves, "state": self.state,
                "detail": self.detail}


#: Without these the product does not run at all. Absence is still not an
#: exception here - ``probe()`` reports it, and the caller decides. A probe that
#: raised would make "show me what is missing" impossible on the machine that
#: most needs it.
REQUIRED: tuple[tuple[str, str, str], ...] = (
    ("pywin32", "win32api", "M7"),
    ("cryptography", "cryptography", "M2"),
    ("psutil", "psutil", "M6"),
)

#: Absence degrades one feature; see the ``serves`` column.
OPTIONAL: tuple[tuple[str, str, str], ...] = (
    ("pywebview", "webview", "M9"),
    ("pystray", "pystray", "M9"),
    ("Pillow", "PIL", "M4"),
    ("numpy", "numpy", "M4"),
    ("onnxruntime", "onnxruntime", "M4"),
    ("uiautomation", "uiautomation", "M5"),
    ("WinRT OCR", "winrt.windows.media.ocr", "M5"),
)


def _windows() -> bool:
    """Is this Windows? Checked without importing a platform module at top level.

    ``sys.platform`` is a string comparison, so this stays importable and
    testable on any CI runner without pulling in anything platform-specific.
    """
    return sys.platform.startswith("win")


def _try(module: str) -> tuple[str, str]:
    """Import ``module``; return ``(state, detail)``.

    A ``ModuleNotFoundError`` for the module itself means absent. A
    ``ModuleNotFoundError`` naming a *different* module means the package is
    installed but one of its own dependencies is missing - that is ``broken``,
    and the detail says which name was missing, because that is the fix.
    """
    try:
        importlib.import_module(module)
    except ModuleNotFoundError as exc:
        missing = getattr(exc, "name", None) or ""
        if missing and not module.startswith(missing) and missing != module:
            return "broken", f"installed, but needs {missing!r}"
        return "absent", "not installed"
    except Exception as exc:                     # noqa: BLE001 - reported, not hidden
        return "broken", f"{type(exc).__name__}: {exc}"
    return "present", ""


@dataclass
class Capabilities:
    """The result of one probe. Plain data, safe to log and to JSON-dump."""

    features: tuple[Feature, ...] = field(default_factory=tuple)
    platform: str = ""

    def get(self, name: str) -> Feature | None:
        for feature in self.features:
            if feature.name == name:
                return feature
        return None

    def usable(self, name: str) -> bool:
        """True when ``name`` is present. Unknown names are not usable."""
        feature = self.get(name)
        return feature is not None and feature.usable

    def by_state(self, state: str) -> tuple[Feature, ...]:
        return tuple(f for f in self.features if f.state == state)

    @property
    def missing_required(self) -> tuple[Feature, ...]:
        """Required pieces that are not present. Non-empty means the host cannot run.

        Filtered by *name*, not by ``serves``: M7 has both a required member
        (pywin32) and none of the optional ones, while M9 is entirely optional.
        Grouping by module would mark every optional M7 feature as required.
        """
        required = frozenset(name for name, _m, _s in REQUIRED)
        return tuple(f for f in self.features
                     if f.name in required and not f.usable)

    def summary(self) -> dict[str, object]:
        """Counts and names only - the same shape the engine uses for status."""
        return {
            "platform": self.platform,
            "present": [f.name for f in self.by_state("present")],
            "absent": [f.name for f in self.by_state("absent")],
            "broken": [f.name for f in self.by_state("broken")],
            "wrong_os": [f.name for f in self.by_state("wrong_os")],
            "missing_required": [f.name for f in self.missing_required],
        }


def probe() -> Capabilities:
    """Import-test every candidate and report. Never raises.

    On a non-Windows machine the Windows-only candidates are reported as
    ``wrong_os`` rather than ``absent``: "we did not look" and "we looked and it
    was not there" are different facts, and conflating them is the error this
    project keeps finding in the original product's documentation.
    """
    on_windows = _windows()
    features: list[Feature] = []
    for name, module, serves in REQUIRED + OPTIONAL:
        if not on_windows and serves in {"M2", "M5", "M6", "M7", "M9"}:
            features.append(Feature(name=name, module=module, serves=serves,
                                    state="wrong_os",
                                    detail="not probed: this is not Windows"))
            continue
        state, detail = _try(module)
        features.append(Feature(name=name, module=module, serves=serves,
                                state=state, detail=detail))
    return Capabilities(features=tuple(features), platform=sys.platform)
