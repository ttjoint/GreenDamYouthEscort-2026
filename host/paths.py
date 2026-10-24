"""Where the product keeps its files, and where it must never write.

The original wrote into ``%WinDir%\\System32``; that is on the project's
refusal list (requirement B7) and the engine's contract test forbids the string
from appearing in ``src/``. The host is where a real path would be built, so
the rule is enforced here instead: :func:`config_dir` and friends return
locations under the user's or the machine's data directory, and
:func:`assert_writable` refuses anything under the system directory.

Why a separate module rather than four ``os.environ`` lookups at the call
sites: the layout has to be the same for the service (LocalSystem, no user
profile), the console (a normal user) and the uninstaller (which runs as admin
but must find what the installer wrote). One place to state it, one place to
test it.

The layout::

    %ProgramData%\\GreenDam2026\\          machine-wide, written by the installer
        config\\xconfigs.dat                 policy blob
        logs\\events.jsonl                   audit log
        journal\\install.json                change manifest
        lists\\                              imported word lists

    %LOCALAPPDATA%\\GreenDam2026\\         per-user, written by the console
        ui\\                                 console state

The service runs as LocalSystem, so it reads the machine-wide tree. The
console writes only under ``LOCALAPPDATA``. A child account cannot edit the
machine-wide tree without elevation, which is the point.
"""
from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "APP_NAME",
    "PROGRAM_DATA_ENV",
    "LOCAL_APP_DATA_ENV",
    "config_dir",
    "data_dir",
    "log_path",
    "journal_path",
    "user_dir",
    "assert_writable",
    "SystemDirectoryError",
]

#: One name in one place: the directory name, the service name and the
#: uninstall registry key all derive from it.
APP_NAME = "GreenDam2026"

PROGRAM_DATA_ENV = "ProgramData"
LOCAL_APP_DATA_ENV = "LOCALAPPDATA"


class SystemDirectoryError(ValueError):
    """Raised when something tries to write under the Windows system directory.

    This is requirement B7. It is a ``ValueError`` rather than a bespoke
    exception hierarchy because the only useful handling is "do not do that".
    """


def _env(name: str, *, default: str) -> Path:
    """Read an environment variable as a path, with a documented fallback.

    The fallbacks exist so the module is testable with the variables cleared,
    which is exactly what a test should do - a test that inherits the real
    ``%ProgramData%`` would pass on this machine and nowhere else.
    """
    value = os.environ.get(name)
    return Path(value) if value else Path(default)


def data_dir() -> Path:
    """The machine-wide tree. Requires elevation to modify."""
    return _env(PROGRAM_DATA_ENV, default=r"C:\ProgramData") / APP_NAME


def user_dir() -> Path:
    """The per-user tree. The console writes here and nowhere else."""
    return _env(LOCAL_APP_DATA_ENV,
                default=str(Path.home() / "AppData" / "Local")) / APP_NAME


def config_dir() -> Path:
    """Where ``xconfigs.dat`` lives."""
    return data_dir() / "config"


def log_path() -> Path:
    """The audit log. One file, append-only JSON lines."""
    return data_dir() / "logs" / "events.jsonl"


def journal_path() -> Path:
    """The install manifest the uninstaller reads to roll back in reverse."""
    return data_dir() / "journal" / "install.json"


def _system_roots() -> tuple[Path, ...]:
    """Directories the product must never write into.

    ``%WinDir%`` and its ``System32`` are read from the environment rather than
    hard-coded: a test machine may have Windows somewhere unusual, and a
    hard-coded ``C:\\Windows`` would make the guard vacuous there. The literal
    string ``System32`` is composed from parts so that grepping the host tree
    for it finds only this one deliberate occurrence, next to the explanation.
    """
    windir = _env("SystemRoot", default=r"C:\Windows")
    system32 = "System" + "32"
    return (windir, windir / system32)


def assert_writable(path: Path) -> Path:
    """Return ``path``, or raise if it is under the Windows system directory.

    Call this before any write the installer or the console performs. It is
    deliberately a pure check - it does not create directories, so a caller
    that gets the error has not left a half-made tree behind.
    """
    resolved = Path(os.path.abspath(str(path))).resolve()
    for root in _system_roots():
        try:
            root_resolved = Path(os.path.abspath(str(root))).resolve()
        except OSError:                          # pragma: no cover - odd mount
            continue
        if resolved == root_resolved or root_resolved in resolved.parents:
            raise SystemDirectoryError(
                f"refusing to write to {resolved}: it is under the Windows "
                f"system directory, which this product must never modify "
                f"(requirement B7)")
    return resolved
