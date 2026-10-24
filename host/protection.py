"""What the host must refuse to touch, and why.

The original product shipped a 22-entry termination list, and the entries were
security tools. Reproducing that is requirement B8 in
``docs/requirements-traceability.md``: *never* kill a process by name because it
looks like an antivirus. ``tests/test_contracts.py`` proves the engine does not
contain such a call; this module is where the host would have one, so the guard
lives here and is tested here.

Three independent refusals, each for a different reason:

``critical``  Windows itself stops working. Killing ``csrss`` bugchecks the
              machine. This is not a policy choice.
``security``  An antivirus or EDR product. Killing it is what malware does, and
              it is the specific behaviour that got the original product
              classified as one.
``self``      This product's own process, its parent, or its service host.
              Terminating ourselves is never the intended action.

The matching is deliberately **over-inclusive**. A false positive means "we
declined to kill something", which is recoverable and logged; a false negative
means "we killed the antivirus", which is not. Every guard in this project fails
in the safe direction, and this one is the clearest case.
"""
from __future__ import annotations

__all__ = [
    "CRITICAL_PROCESSES",
    "SECURITY_HINTS",
    "Refusal",
    "why_protected",
    "is_protected",
]

#: Exact (lowercased, without ``.exe``) names of processes Windows needs.
#: Sourced from the documented critical-process list, not from guesswork: every
#: entry here is one where termination causes a bugcheck or a logon failure.
CRITICAL_PROCESSES: frozenset[str] = frozenset({
    "smss", "csrss", "wininit", "winlogon", "services", "lsass", "lsm",
    "svchost", "system", "system idle process", "registry", "memory compression",
    "fontdrvhost", "dwm", "sihost", "taskhostw", "explorer", "logonui",
    "userinit", "wudfhost", "audiodg", "conhost", "spoolsv",
})

#: Substrings that identify a security product, lowercased. Substring matching
#: because vendors ship many binaries per product and the set changes with every
#: release; an exact list would be wrong within a month.
#:
#: This is intentionally broad. See the module docstring on why false positives
#: are the acceptable failure here.
SECURITY_HINTS: tuple[str, ...] = (
    # Microsoft
    "msmpeng", "nissrv", "windefend", "mpcmdrun", "securityhealth",
    "smartscreen", "defender",
    # Mainland China vendors. The vendor prefixes matter more than product
    # names here: 360, Tencent and Kingsoft each ship dozens of binaries whose
    # names share only a prefix.
    "360", "qihoo", "zhudongfangyu", "qqpc", "qhsafe", "tencent",
    "huorong", "hrsword", "sysdiag", "hipstray", "hipsdaemon",
    "hipsmain", "kingsoft", "ksafe", "kxescore",
    "kxetray", "baidusd", "rising", "ravmon",
    # International
    "avast", "avgnt", "avguard", "avp", "kavfs", "kaspersky", "nod32", "ekrn",
    "egui", "mcafee", "mcshield", "symantec", "ccsvchst", "rtvscan",
    "bitdefender", "vsserv", "sophos", "savservice", "trend", "tmlisten",
    "crowdstrike", "csfalconservice", "sentinelone", "sentinelagent",
    "malwarebytes", "mbamservice", "eset", "emsisoft", "a2service",
    "carbonblack", "cylancesvc", "tanium", "velociraptor",
)


class Refusal:
    """Why one candidate was not terminated. Plain data, safe to log."""

    __slots__ = ("pid", "name", "category", "why")

    def __init__(self, pid: int, name: str, category: str, why: str) -> None:
        self.pid = pid
        self.name = name
        self.category = category
        self.why = why

    def to_dict(self) -> dict[str, object]:
        return {"pid": self.pid, "name": self.name,
                "category": self.category, "why": self.why}

    def __repr__(self) -> str:                   # pragma: no cover - debugging aid
        return (f"Refusal(pid={self.pid}, name={self.name!r}, "
                f"category={self.category!r})")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Refusal):
            return NotImplemented
        return (self.pid, self.name, self.category) == (
            other.pid, other.name, other.category)

    def __hash__(self) -> int:
        return hash((self.pid, self.name, self.category))


def _stem(name: str) -> str:
    """Lowercase, strip any directory part and one ``.exe`` suffix.

    ``C:\\Windows\\<system dir>\\CSRSS.EXE`` and ``csrss`` must compare equal; the
    process list gives a bare name but a caller replaying a log may pass a path.
    """
    base = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base


def why_protected(name: str, *, pid: int | None = None,
                  self_pids: frozenset[int] | tuple[int, ...] = ()) -> tuple[str, str] | None:
    """Return ``(category, explanation)`` when ``name`` must not be terminated.

    ``None`` means the process is not protected by any of the three rules. The
    order matters: ``self`` is checked first because it is the one case where
    acting would be self-inflicted, then ``critical`` because that is the case
    with the worst consequence, then ``security``.
    """
    if pid is not None and pid in set(self_pids):
        return ("self", "this product's own process tree")

    stem = _stem(name)

    if stem in CRITICAL_PROCESSES:
        return ("critical",
                f"{stem} is a Windows critical process; terminating it can "
                f"bugcheck the machine or end the user's session")

    for hint in SECURITY_HINTS:
        if hint in stem:
            return ("security",
                    f"{stem} looks like a security product ({hint!r}); "
                    f"killing security tools is requirement B8 and this "
                    f"product must never do it")

    return None


def is_protected(name: str, *, pid: int | None = None,
                 self_pids: frozenset[int] | tuple[int, ...] = ()) -> bool:
    """Converse of :func:`why_protected`, for readable call sites."""
    return why_protected(name, pid=pid, self_pids=self_pids) is not None
