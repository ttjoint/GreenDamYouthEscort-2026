"""Apply a policy :class:`~host.policy.Plan`, recording what was done.

The only module in the browser-policy path that writes. Everything it writes is
first expressed as a :class:`~host.policy.Change`, so the decision of *what* to
change is testable without an elevated shell and the *act* of changing it is
small enough to audit.

Three properties this module must keep:

**A write is read back.** ``winreg`` reports success for a value it wrote to a
key that a policy engine later ignores - for example a ``REG_SZ`` where a
multi-string is required. Reading back the value and comparing catches the class
of mistake that otherwise appears as "the policy is set and nothing happens".

**Removal never deletes a key it did not fully own.** Deleting
``SOFTWARE\\Policies\\Microsoft\\Edge`` removes settings another tool wrote. Each
removal is a value delete, and a key is only removed when it ends up empty.

**A failed removal is reported, not swallowed.** This is the difference between
an uninstall that leaves a trusted extension force-installed and one that does
not. Failures accumulate and the caller decides whether to block.
"""
from __future__ import annotations

import json
import winreg
from dataclasses import dataclass, field
from pathlib import Path

from . import Change, Plan

__all__ = [
    "ApplyResult",
    "apply_plan",
    "read_back",
    "REGISTRY_WRITE_ACCESS",
]

#: Access rights for writing a policy value. ``KEY_WOW64_64KEY`` is explicit:
#: a 32-bit process writing a policy key without it lands in the redirected view,
#: where 64-bit Chrome never looks.
REGISTRY_WRITE_ACCESS = (winreg.KEY_SET_VALUE | winreg.KEY_READ
                         | winreg.KEY_WOW64_64KEY)


@dataclass
class ApplyResult:
    """What happened, per change. Failures are kept, not raised."""

    applied: list[Change] = field(default_factory=list)
    failed: list[tuple[Change, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def summary(self) -> dict[str, object]:
        return {
            "applied": len(self.applied),
            "failed": [{"change": c.to_dict(), "why": why}
                       for c, why in self.failed],
        }


def _registry_type(value: object) -> int:
    """The ``winreg`` type for a Python value.

    ``ExtensionInstallForcelist`` is a ``REG_SZ`` list - Chromium accepts a single
    string containing the entries separated by newlines in the registry, and a
    ``REG_MULTI_SZ`` is silently ignored. Getting this wrong is the classic
    "policy set, no effect" bug, so the mapping is explicit rather than inferred
    from the value's Python type.
    """
    if isinstance(value, dict):
        return winreg.REG_SZ
    if isinstance(value, (list, tuple)):
        return winreg.REG_SZ
    if isinstance(value, int):
        return winreg.REG_DWORD
    return winreg.REG_SZ


def _registry_data(value: object) -> str | int:
    """Serialise a value for the registry.

    Dictionaries become compact JSON, which is what Chromium's
    ``ExtensionSettings`` expects in a ``REG_SZ``. Lists become newline-joined
    strings, which is what ``ExtensionInstallForcelist`` expects.
    """
    if isinstance(value, dict):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "\n".join(str(item) for item in value)
    return value if isinstance(value, int) else str(value)


def _write_registry(change: Change) -> None:
    """Write one policy value into ``change.target``.

    ``target`` is a full path including the hive, e.g.
    ``HKLM\\SOFTWARE\\Policies\\Google\\Chrome``. The hive is parsed rather than
    assumed because Chromium reads **both** ``HKEY_LOCAL_MACHINE`` and
    ``HKEY_CURRENT_USER`` and merges them
    (``policy_loader_win.cc``: ``kScopes[]`` lists ``POLICY_SCOPE_MACHINE`` and
    ``POLICY_SCOPE_USER``). A machine-wide write covers every account on the
    machine and is the right default; a per-user write needs no elevation, which
    is what makes an unprivileged install possible at all.

    ``KEY_WOW64_64KEY`` is explicit: a 32-bit process writing a policy key
    without it lands in the redirected view, where 64-bit Chrome never looks.
    """
    hive, subkey = _split_hive(change.target)
    data = _registry_data(change.value)
    kind = _registry_type(change.value)
    # Create the key if absent, but never delete it: another tool may own it.
    with winreg.CreateKeyEx(hive, subkey, 0, REGISTRY_WRITE_ACCESS) as handle:
        winreg.SetValueEx(handle, change.name or "", 0, kind, data)


def _split_hive(target: str) -> tuple[int, str]:
    """Split ``HKLM\\a\\b`` into ``(HKEY_LOCAL_MACHINE, 'a\\b')``.

    Accepts the short and long spellings so a plan written by hand and a plan
    built by :func:`host.policy.plan_install` compare equal.
    """
    head, _, rest = target.partition("\\")
    hives = {
        "HKLM": winreg.HKEY_LOCAL_MACHINE,
        "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
        "HKCU": winreg.HKEY_CURRENT_USER,
        "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
    }
    if head not in hives:
        raise ValueError(
            f"policy target {target!r} does not start with a hive; expected "
            f"HKLM\\... or HKCU\\... so the scope is explicit")
    return hives[head], rest


def _delete_registry(change: Change) -> None:
    """Delete one value, then the key only if it is now empty."""
    hive, subkey = _split_hive(change.target)
    try:
        with winreg.OpenKey(hive, subkey, 0, REGISTRY_WRITE_ACCESS) as handle:
            try:
                winreg.DeleteValue(handle, change.name or "")
            except FileNotFoundError:
                # Already absent. Removal is idempotent on purpose: an uninstall
                # that has been run twice must not fail the second time.
                return
            remaining = winreg.QueryInfoKey(handle)[1]
    except FileNotFoundError:
        return
    if remaining == 0:
        try:
            winreg.DeleteKeyEx(hive, subkey, winreg.KEY_WOW64_64KEY)
        except OSError:
            # Not fatal: an empty policy key is harmless, and something may have
            # re-created it between the two calls.
            pass


def _write_file(change: Change) -> None:
    target = Path(change.target)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Firefox rejects a policies.json that is not valid JSON, and a half-written
    # one is worse than none. Write beside, then replace.
    staging = target.with_suffix(".json.new")
    staging.write_text(json.dumps(change.value, indent=2, ensure_ascii=False),
                       encoding="utf-8")
    staging.replace(target)


def _delete_file(change: Change) -> None:
    target = Path(change.target)
    try:
        target.unlink()
    except FileNotFoundError:
        return
    # Leave the directory: Firefox tolerates an empty distribution/ and another
    # tool may have put something else there.
    try:
        parent = target.parent
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass


def read_back(change: Change) -> object:
    """Read a written value back, for comparison. ``None`` when absent."""
    if change.kind == "registry":
        try:
            hive, subkey = _split_hive(change.target)
            with winreg.OpenKey(hive, subkey, 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as handle:
                value, _kind = winreg.QueryValueEx(handle, change.name or "")
                return value
        except (FileNotFoundError, OSError, ValueError):
            return None
    try:
        return json.loads(Path(change.target).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def apply_plan(plan: Plan) -> ApplyResult:
    """Perform every change in ``plan``, collecting failures.

    Removals run before writes so that a reinstall starts from a clean state, and
    so that a partial failure leaves the machine closer to "nothing installed"
    than to "half installed".
    """
    result = ApplyResult()
    ordered = [c for c in plan.changes if c.is_removal] + \
              [c for c in plan.changes if not c.is_removal]

    for change in ordered:
        try:
            if change.kind == "registry":
                if change.is_removal:
                    _delete_registry(change)
                else:
                    _write_registry(change)
            elif change.kind == "file":
                if change.is_removal:
                    _delete_file(change)
                else:
                    _write_file(change)
            else:                                # pragma: no cover - guarded
                raise ValueError(f"unknown change kind {change.kind!r}")
        except OSError as exc:
            result.failed.append((change, f"{type(exc).__name__}: {exc}"))
            continue
        result.applied.append(change)

    return result
