"""Browser policy: the changes to make, as data, before anything is written.

Installing a browser extension by policy is the one part of this product that
touches a store shared with the operating system and with other management
tools. Two consequences shape this module:

**Every change is reversible and identified.** A policy value written without a
record is a value that survives uninstall - the exact behaviour this project
exists to avoid. Each change therefore carries the key it writes, and removal is
expressed as "delete this value", not "delete this tree". Deleting
``SOFTWARE\\Policies\\Microsoft\\Edge`` wholesale would also delete
``StartupBoostEnabled``, which this product did not set. Verified on this
machine: that key exists and belongs to someone else.

**The two families need different schemas, not different spellings.** Chromium's
``ExtensionSettings`` uses a dictionary keyed by extension ID; Firefox's uses one
keyed by the same name but with a different value shape, and Firefox reads it
from a file rather than the registry. Writing Chromium's schema into Firefox's
file produces a policy Firefox ignores silently - the failure looks like success.

Nothing in this module writes. It produces :class:`Change` objects that
:mod:`host.policy.apply` performs, which is what makes the whole thing testable
without an elevated shell or a live browser.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..browsers import Browser, Family, FIREFOX, CHROMIUM

__all__ = [
    "Change",
    "Plan",
    "CHROMIUM_KEYS",
    "extension_settings",
    "force_list",
    "plan_install",
    "plan_remove",
    "install_extension",
    "firefox_policy_document",
    "EXTENSION_UPDATE_URL",
]

#: Chromium policy value names this product uses. Kept as constants because the
#: uninstall sweep must remove exactly these and nothing else.
CHROMIUM_KEYS: tuple[str, ...] = (
    "ExtensionInstallForcelist",
    "ExtensionSettings",
    "ExtensionInstallAllowlist",
    "ExtensionInstallBlocklist",
)

#: Where Chromium-family browsers look for an extension update. For a
#: self-hosted extension this is a directory or an ``update.xml`` URL. Using
#: ``file://`` keeps the deployment offline, which is a product requirement, but
#: Chromium refuses ``file://`` for this policy on some versions - so the value
#: is a parameter and the caller decides.
EXTENSION_UPDATE_URL = ""


@dataclass(frozen=True)
class Change:
    """One policy value to write or delete.

    ``kind`` is ``"registry"`` or ``"file"``. ``target`` is the registry key or
    the file path. ``name`` is the value name (registry) or ``None`` (file).
    """

    kind: str
    target: str
    name: str | None
    #: The value to write. ``None`` means delete.
    value: object = None
    #: Which browser this belongs to, for the report.
    browser: str = ""

    @property
    def is_removal(self) -> bool:
        return self.value is None

    def to_dict(self) -> dict[str, object]:
        """Loggable form. Registry values can be large; only the shape is kept."""
        return {
            "kind": self.kind,
            "target": self.target,
            "name": self.name,
            "action": "delete" if self.is_removal else "write",
            "browser": self.browser,
        }

    def __repr__(self) -> str:                  # pragma: no cover - debugging aid
        verb = "delete" if self.is_removal else "write"
        return f"Change({verb} {self.kind}:{self.target}:{self.name})"


@dataclass
class Plan:
    """Everything needed to install or remove, for every detected browser."""

    changes: tuple[Change, ...] = ()
    #: Browsers that were detected but cannot be configured, with the reason.
    uncovered: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    #: Non-fatal warnings a parent should see.
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> dict[str, object]:
        return {
            "changes": [c.to_dict() for c in self.changes],
            "uncovered": [list(pair) for pair in self.uncovered],
            "warnings": list(self.warnings),
            "writes": sum(1 for c in self.changes if not c.is_removal),
            "deletes": sum(1 for c in self.changes if c.is_removal),
        }


def force_list(extension_id: str) -> list[str]:
    """``ExtensionInstallForcelist`` entries for one extension.

    The format is ``<id>;<update-url>``. An empty update URL means "use the
    Web Store", which requires the extension to be published there - not the
    case for this product. The entry is therefore emitted with whatever URL the
    caller supplies, and an empty one is reported by the caller rather than
    silently producing an entry Chromium rejects.
    """
    return [f"{extension_id};{EXTENSION_UPDATE_URL}"]


def extension_settings(extension_id: str, *, update_url: str = "") -> dict:
    """The Chromium ``ExtensionSettings`` value that pins one extension.

    ``installation_mode: force_installed`` installs it and prevents removal from
    the browser UI. ``toolbar_pin: force_pinned`` keeps the icon visible, which
    matters because a filter the child cannot see is one they will assume is
    broken and try to defeat.
    """
    return {
        extension_id: {
            "installation_mode": "force_installed",
            "update_url": update_url or EXTENSION_UPDATE_URL,
            "toolbar_pin": "force_pinned",
        }
    }


def firefox_policy_document(extension_id: str, *,
                            install_url: str = "") -> dict:
    """The Firefox ``policies.json`` document.

    Firefox's ``ExtensionSettings`` entry for a self-hosted extension needs a
    concrete ``install_url``; unlike Chromium it has no update-URL-in-the-entry
    form. A missing ``install_url`` therefore cannot produce a working policy,
    and the caller is expected to have one.
    """
    return {
        "policies": {
            "ExtensionSettings": {
                extension_id: {
                    "installation_mode": "force_installed",
                    "install_url": install_url,
                }
            }
        }
    }


def plan_install(browsers, extension_id: str, *,
                 update_url: str = "",
                 firefox_install_url: str = "") -> Plan:
    """What to write to install the extension into every detected browser.

    A browser that cannot be configured is reported in ``uncovered`` rather than
    skipped: a parent whose child uses that browser needs to know the filter does
    not apply there.
    """
    changes: list[Change] = []
    uncovered: list[tuple[str, str]] = []
    warnings: list[str] = []

    for browser in browsers:
        if not browser.configurable:
            uncovered.append((browser.display_name or str(browser.executable),
                              browser.detail or "unsupported browser"))
            continue

        if browser.family == CHROMIUM:
            target = browser.policy_target or ""
            changes.append(Change(kind="registry", target=target,
                                  name="ExtensionInstallForcelist",
                                  value=force_list(extension_id),
                                  browser=browser.display_name))
            changes.append(Change(
                kind="registry", target=target, name="ExtensionSettings",
                value=extension_settings(extension_id, update_url=update_url),
                browser=browser.display_name))
            if not (update_url or EXTENSION_UPDATE_URL):
                warnings.append(
                    f"{browser.display_name}: no update URL was supplied, so "
                    f"Chromium will look for {extension_id} in the Web Store, "
                    f"where it is not published. The extension will not install.")
        elif browser.family == FIREFOX:
            if not firefox_install_url:
                warnings.append(
                    f"{browser.display_name}: Firefox needs a concrete "
                    f"install_url for a self-hosted extension; none was "
                    f"supplied, so the policy would be ignored.")
            changes.append(Change(
                kind="file", target=browser.policy_target or "", name=None,
                value=firefox_policy_document(
                    extension_id, install_url=firefox_install_url),
                browser=browser.display_name))
        else:                                    # pragma: no cover - guarded above
            uncovered.append((browser.display_name, "unknown family"))

    return Plan(changes=tuple(changes), uncovered=tuple(uncovered),
                warnings=tuple(warnings))


def plan_remove(browsers, *, all_chromium_vendors: tuple[str, ...] = ()) -> Plan:
    """What to delete to remove every trace of this product's browser policy.

    Two deliberate widenings over ``plan_install``:

    **Every Chromium vendor is swept, not just the ones detected now.** A browser
    uninstalled between install and uninstall leaves its policy key behind; a
    leftover ``ExtensionInstallForcelist`` is a browser that tries to load a
    missing extension forever, and the parent has no way to clear it.

    **Only this product's value names are deleted.** Deleting the whole policy
    key would also remove settings another tool wrote - on this machine
    ``Microsoft\\Edge`` already contains ``StartupBoostEnabled``, which this
    product never set.
    """
    changes: list[Change] = []
    uncovered: list[tuple[str, str]] = []
    warnings: list[str] = []

    seen_targets: set[str] = set()
    for browser in browsers:
        if not browser.configurable:
            uncovered.append((browser.display_name or str(browser.executable),
                              browser.detail or "unsupported browser"))
            continue
        target = browser.policy_target or ""
        if browser.family == CHROMIUM:
            if target in seen_targets:
                continue
            seen_targets.add(target)
            for name in CHROMIUM_KEYS:
                changes.append(Change(kind="registry", target=target, name=name,
                                      value=None, browser=browser.display_name))
        elif browser.family == FIREFOX:
            changes.append(Change(kind="file", target=target, name=None,
                                  value=None, browser=browser.display_name))

    # Vendors that are no longer installed still get their values cleared.
    detected = {b.policy_target for b in browsers if b.configurable}
    for vendor in all_chromium_vendors:
        # The hive prefix is required: ``apply`` parses it to decide scope, and a
        # target without one raises. This was missing and the sweep silently
        # produced unusable changes for every vendor it was meant to clean.
        target = rf"HKLM\SOFTWARE\Policies\{vendor}"
        if target in detected or target in seen_targets:
            continue
        seen_targets.add(target)
        for name in CHROMIUM_KEYS:
            changes.append(Change(kind="registry", target=target, name=name,
                                  value=None,
                                  browser=f"{vendor} (not installed)"))

    return Plan(changes=tuple(changes), uncovered=tuple(uncovered),
                warnings=tuple(warnings))


def install_extension(browsers, extension_dir, rules, *,
                      version: str = "0.1.0") -> Plan:
    """Build the extension and produce the policy plan that installs it.

    One function for both halves because they must agree: the manifest inside
    ``extension_dir`` declares the id that the policy points at, and a mismatch
    produces a browser that force-installs an extension which is not there.

    The extension is self-hosted from a local directory, so the update URL is a
    ``file://`` path. Chromium refuses ``file://`` for ``update_url`` on most
    versions, which is why :func:`plan_install` reports a warning when the URL is
    empty - the honest outcome is "the parent must allow it manually" rather than
    a policy that silently fails.
    """
    from ..extension import build_extension, manifest as build_manifest

    summary = build_extension(extension_dir, rules, version=version)
    document = build_manifest(version=version)
    extension_id = document["browser_specific_settings"]["gecko"]["id"]

    plan = plan_install(
        browsers, extension_id,
        update_url=f"file:///{Path(extension_dir).as_posix()}/update.xml",
        firefox_install_url=f"file:///{Path(extension_dir).as_posix()}/greendam.xpi",
    )
    plan.warnings = plan.warnings + (
        f"extension written to {summary['destination']} "
        f"({summary['terms']} terms)",)
    return plan
