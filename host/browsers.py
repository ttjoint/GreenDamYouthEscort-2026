"""Which browsers are installed, and which policy mechanism each one obeys.

The host cannot assume a single browser. This machine has Chrome, Edge and
Firefox, and a household with children commonly has more than one - the child
uses whichever the parent does not, so covering only the default browser covers
nothing.

Three families, three different mechanisms, and they are not variations of one:

``chromium``  Chrome, Edge, Brave, Vivaldi, Opera. All read the same policy
              schema from ``HKLM\\SOFTWARE\\Policies\\<vendor>\\<Product>``. The
              extension list lives under ``ExtensionInstallForcelist`` and the
              per-extension behaviour under ``ExtensionSettings``.
``firefox``   Reads ``<install>\\distribution\\policies.json``, a file, not the
              registry. ``ExtensionSettings`` there uses a different schema with
              the same name, which is a reliable source of confusion.
``unknown``   Installed but not one this module knows how to configure. Reported
              rather than guessed at: writing a Chromium key into a vendor whose
              schema differs would look like success and enforce nothing.

**Detection is by registry, not by path.** ``App Paths`` is how Windows itself
resolves an executable name; a hard-coded ``C:\\Program Files\\...`` misses the
32-bit tree, per-user installs, and every non-default drive. Verified on this
machine: Chrome lives under ``Program Files``, Edge under ``Program Files (x86)``,
and guessing either one alone would have missed the other.

**Nothing here writes.** This module answers "what is installed and what would be
written"; :mod:`host.policy.registry` performs the write. That split is what lets
the whole detection layer be tested without touching a live policy store.
"""
from __future__ import annotations

import winreg
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "Family",
    "Browser",
    "detect",
    "chromium_vendors",
    "APP_PATHS_KEY",
    "FIREFOX_POLICY_RELATIVE",
]

APP_PATHS_KEY = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths")

#: Where Firefox reads policy from, relative to its installation directory.
#: The directory usually does not exist; creating it is part of installing.
FIREFOX_POLICY_RELATIVE = Path("distribution") / "policies.json"

#: Chromium-family products: (executable, policy vendor key, display name).
#:
#: The vendor key is what appears under ``SOFTWARE\\Policies``. Edge is under
#: ``Microsoft\\Edge`` while Chrome is under ``Google\\Chrome`` - the nesting
#: differs, which is why this is a table and not a rule.
CHROMIUM_PRODUCTS: tuple[tuple[str, str, str], ...] = (
    ("chrome.exe", r"Google\Chrome", "Google Chrome"),
    ("msedge.exe", r"Microsoft\Edge", "Microsoft Edge"),
    ("brave.exe", r"BraveSoftware\Brave-Browser", "Brave"),
    ("vivaldi.exe", r"Vivaldi Technologies\Vivaldi", "Vivaldi"),
    ("opera.exe", r"Opera Software\Opera Stable", "Opera"),
    ("chromium.exe", r"Chromium", "Chromium"),
)

#: Firefox and its direct forks. Forks read the same ``distribution`` file, so
#: they share the family; the executable differs only for detection.
FIREFOX_PRODUCTS: tuple[tuple[str, str], ...] = (
    ("firefox.exe", "Mozilla Firefox"),
    ("librewolf.exe", "LibreWolf"),
    ("waterfox.exe", "Waterfox"),
)


class Family(str):
    """A policy mechanism, not a vendor. ``str`` so it logs and JSON-dumps."""

    __slots__ = ()


CHROMIUM = Family("chromium")
FIREFOX = Family("firefox")
UNKNOWN = Family("unknown")


@dataclass(frozen=True)
class Browser:
    """One installed browser and how to configure it."""

    family: Family
    executable: Path
    #: Policy location: a registry key for Chromium, a file path for Firefox.
    #: Registry targets include the hive (``HKLM\...``) because Chromium reads
    #: both ``HKEY_LOCAL_MACHINE`` and ``HKEY_CURRENT_USER`` and merges them, so
    #: the scope is part of the target rather than a separate field that could
    #: disagree with it. ``None`` for ``unknown``.
    policy_target: str | None
    display_name: str = ""
    #: Why this browser is reported as ``unknown``, when it is.
    detail: str = ""

    @property
    def configurable(self) -> bool:
        """Whether this module knows how to install the extension into it."""
        return self.family != UNKNOWN and self.policy_target is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "family": str(self.family),
            "executable": str(self.executable),
            "policy_target": self.policy_target,
            "display_name": self.display_name,
            "configurable": self.configurable,
            "detail": self.detail,
        }

    def with_scope(self, scope: str) -> "Browser":
        """Same browser, different policy hive. ``scope`` is ``"HKLM"`` or ``"HKCU"``.

        A machine-wide policy is the default because it covers every account and
        cannot be undone by the child from a non-elevated shell. A per-user
        policy needs no elevation, which is the only way an unprivileged install
        can work - so the choice belongs to the caller, not to detection.

        Firefox is returned unchanged: its policy is a file under the
        installation directory, so "scope" has no meaning for it. Silently
        rewriting a file path into a registry path would be a bug that only
        appears on a machine with Firefox.
        """
        if scope not in {"HKLM", "HKCU"}:
            raise ValueError(f"scope must be 'HKLM' or 'HKCU', got {scope!r}")
        if self.family != CHROMIUM or not self.policy_target:
            return self
        _hive, _, rest = self.policy_target.partition("\\")
        return Browser(family=self.family, executable=self.executable,
                       policy_target=scope + "\\" + rest,
                       display_name=self.display_name, detail=self.detail)


def _app_path(executable: str) -> Path | None:
    """Resolve an executable name the way Windows does, or None.

    Checks the 64-bit and 32-bit views and the per-user hive, because a browser
    installed per-user is invisible in ``HKLM`` and is exactly the case a
    "detect the family" routine gets wrong.
    """
    keys = (
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_CURRENT_USER, 0),
    )
    for hive, flags in keys:
        try:
            with winreg.OpenKey(hive, rf"{APP_PATHS_KEY}\{executable}",
                                0, winreg.KEY_READ | flags) as handle:
                value, _kind = winreg.QueryValueEx(handle, "")
        except OSError:
            continue
        if value:
            path = Path(value)
            if path.is_file():
                return path
    return None


def _policy_key_exists(vendor: str) -> bool:
    """Whether the policy root for ``vendor`` already exists.

    Not required for a browser to be configurable - the key can be created. Used
    only to distinguish "we would create this" from "this is already managed by
    something else", which is worth reporting because a second management tool
    writing the same key is a conflict the parent should know about.
    """
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Policies\{vendor}"):
            return True
    except OSError:
        return False


def _detect_chromium(executable: str, vendor: str,
                     name: str) -> Browser | None:
    path = _app_path(executable)
    if path is None:
        return None
    return Browser(family=CHROMIUM, executable=path,
                   policy_target=rf"HKLM\SOFTWARE\Policies\{vendor}",
                   display_name=name)


def _detect_firefox(executable: str, name: str) -> Browser | None:
    path = _app_path(executable)
    if path is None:
        return None
    return Browser(family=FIREFOX, executable=path,
                   policy_target=str(path.parent / FIREFOX_POLICY_RELATIVE),
                   display_name=name)


def detect() -> tuple[Browser, ...]:
    """Every browser this machine has, in a stable order.

    Chromium family first because it is the common case, then Firefox, then any
    executable that looks like a browser but is not one this module knows. The
    last group is reported rather than dropped: a parent whose browser is not
    covered needs to know that, and silently ignoring it produces a filter that
    appears to work.
    """
    found: list[Browser] = []
    seen: set[Path] = set()

    for executable, vendor, name in CHROMIUM_PRODUCTS:
        browser = _detect_chromium(executable, vendor, name)
        if browser is not None and browser.executable not in seen:
            seen.add(browser.executable)
            found.append(browser)

    for executable, name in FIREFOX_PRODUCTS:
        browser = _detect_firefox(executable, name)
        if browser is not None and browser.executable not in seen:
            seen.add(browser.executable)
            found.append(browser)

    for executable, name in _OTHER_KNOWN:
        path = _app_path(executable)
        if path is None or path in seen:
            continue
        seen.add(path)
        found.append(Browser(
            family=UNKNOWN, executable=path, policy_target=None,
            display_name=name,
            detail="installed, but this product has no policy mechanism for it"))

    return tuple(found)


#: Browsers that exist but that this product cannot configure. Listed so they are
#: reported as uncovered instead of being invisible.
_OTHER_KNOWN: tuple[tuple[str, str], ...] = (
    ("iexplore.exe", "Internet Explorer"),
    ("safari.exe", "Safari for Windows"),
)


def chromium_vendors() -> tuple[str, ...]:
    """Every Chromium policy vendor key, for the uninstall sweep.

    Uninstall must clear all of them, not just the ones detected now: a browser
    uninstalled between install and uninstall leaves its policy key behind, and a
    leftover ``ExtensionInstallForcelist`` is a browser that tries to load a
    missing extension forever.
    """
    return tuple(vendor for _exe, vendor, _name in CHROMIUM_PRODUCTS)
