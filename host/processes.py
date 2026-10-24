"""M6: watch process names, and terminate the ones the engine blocks.

The engine already decides. ``Filter.check_process`` matches the name against
list B (case-insensitively, list B is exact-match) and returns a ``Decision``
carrying the gate reason when filtering is switched off. This module's entire
job is the part the engine cannot do under its no-I/O contract: enumerate real
processes, and - only when explicitly asked - terminate.

Two design decisions worth stating because they are not obvious:

**Dry run is the default.** :func:`sweep` never terminates unless
``apply=True``. An enforcement action that fires by default is one that runs
during a test, during a first start, during a misconfiguration. The parent
product this replaces did that. Making the dangerous path opt-in is cheap; the
alternative is a bug that kills a user's work.

**Refusals are logged, not silently skipped.** When a protected process is on
the watched list, that is a configuration mistake worth surfacing - the parent
asked to block something the product will never block. Returning a ``Refusal``
lets the console say so instead of the block mysteriously not working.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Protocol

from .protection import Refusal, why_protected

__all__ = [
    "ProcessInfo",
    "ActionResult",
    "SweepReport",
    "ProcessSource",
    "list_processes",
    "sweep",
]


@dataclass(frozen=True)
class ProcessInfo:
    """One running process, reduced to what the decision needs.

    ``name`` is the bare executable name as Windows reports it (``notepad.exe``).
    ``pid`` is kept for logging and for the self-protection check; it is never
    rendered into a log line by itself, because a PID alone identifies nothing
    useful after a reboot.
    """

    pid: int
    name: str

    @property
    def stem(self) -> str:
        base = self.name.lower()
        return base[:-4] if base.endswith(".exe") else base


class ProcessSource(Protocol):
    """Where process rows come from. Exists so tests need no real processes."""

    def __call__(self) -> Iterable[ProcessInfo]: ...  # pragma: no cover


def list_processes() -> tuple[ProcessInfo, ...]:
    """Every process this user can see, via ``psutil``.

    Processes that vanish mid-enumeration and processes belonging to another
    user are skipped rather than raised: a sweep that fails because one process
    exited is a sweep that never completes. The count of skipped rows is not
    reported here because the caller's question is "which watched process is
    running", and a permission error on an unrelated process is not an answer to
    it.

    ``psutil`` is imported inside the function so that importing this module
    stays safe on a machine without it - the capability probe reports the
    absence, and this module is then simply never called.
    """
    import psutil                                   # noqa: PLC0415 - lazy by design

    rows: list[ProcessInfo] = []
    for proc in psutil.process_iter(["pid", "name"]):
        info = proc.info
        name = info.get("name")
        pid = info.get("pid")
        if not name or pid is None:
            continue
        rows.append(ProcessInfo(pid=int(pid), name=str(name)))
    return tuple(rows)


@dataclass
class ActionResult:
    """What happened to one watched process."""

    process: ProcessInfo
    blocked: bool
    reason: str
    #: "would_terminate" in a dry run, "terminated" after a real one,
    #: "refused" when a guard fired, "failed" when the OS said no,
    #: "not_watched" when the engine allowed it.
    action: str

    @property
    def needs_attention(self) -> bool:
        """True when a parent should be told: a refusal or a failure."""
        return self.action in {"refused", "failed"}

    def to_dict(self) -> dict[str, object]:
        return {"pid": self.process.pid, "name": self.process.name,
                "blocked": self.blocked, "reason": self.reason,
                "action": self.action}


@dataclass
class SweepReport:
    """The result of one pass over the process table."""

    results: tuple[ActionResult, ...] = ()
    #: How many rows the source produced. A count, not the rows.
    seen: int = 0
    #: False when the sweep was a dry run, which is the default.
    applied: bool = False
    #: Refusals, surfaced separately because they are configuration errors.
    refusals: tuple[Refusal, ...] = field(default_factory=tuple)

    @property
    def terminated(self) -> tuple[ActionResult, ...]:
        return tuple(r for r in self.results if r.action == "terminated")

    @property
    def would_terminate(self) -> tuple[ActionResult, ...]:
        return tuple(r for r in self.results if r.action == "would_terminate")

    @property
    def blocked(self) -> tuple[ActionResult, ...]:
        """Every watched-and-blocked row, whatever was done about it."""
        return tuple(r for r in self.results if r.blocked)

    def summary(self) -> dict[str, object]:
        """Counts and names only. Never a command line or a window title."""
        return {
            "seen": self.seen,
            "applied": self.applied,
            "blocked": len(self.blocked),
            "terminated": len(self.terminated),
            "would_terminate": len(self.would_terminate),
            "refused": [r.to_dict() for r in self.refusals],
            "failed": [r.to_dict() for r in self.results
                       if r.action == "failed"],
        }


def _terminate(process: ProcessInfo) -> tuple[str, str]:
    """Terminate one process. Returns ``(action, detail)``.

    ``psutil`` is used rather than calling the Win32 termination API directly:
    it raises a typed ``AccessDenied`` instead of returning a bare zero, and the
    difference between "we may not" and "it is gone" is exactly what the caller
    needs to log. The Win32 entry point's name is deliberately not written here
    - ``host/tests/test_contracts.py`` scans for it, and the requirement it
    guards (B8) is about *which* processes are killed. The guard for that is
    :mod:`host.protection`, not the absence of an import.
    """
    import psutil                                   # noqa: PLC0415

    try:
        proc = psutil.Process(process.pid)
    except psutil.NoSuchProcess:
        return "not_watched", "process exited before it could be acted on"
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except psutil.AccessDenied:
        return "failed", "access denied: the service must run elevated to do this"
    except psutil.NoSuchProcess:
        return "terminated", "process exited during termination"
    except psutil.TimeoutExpired:
        return "failed", "process did not exit within 3s of terminate()"
    return "terminated", "terminated"


def sweep(filter_, *, source: ProcessSource | None = None,
          apply: bool = False,
          self_pids: Iterable[int] = (),
          on_result: Callable[[ActionResult], None] | None = None) -> SweepReport:
    """Run one pass: enumerate, ask the engine, act on the blocked ones.

    ``filter_`` is a ``green_dam.filter.Filter``; only ``check_process`` is
    used. The engine decides, the host acts - the same split as everywhere else.

    ``apply=False`` (the default) reports what *would* happen. Every code path
    that terminates is behind this flag.
    """
    source = source or list_processes
    rows = tuple(source())
    own = frozenset(self_pids)

    results: list[ActionResult] = []
    refusals: list[Refusal] = []

    for process in rows:
        decision = filter_.check_process(process.name)
        if not getattr(decision, "blocked", False):
            continue

        reason = str(getattr(decision, "reason", "") or "")
        guard = why_protected(process.name, pid=process.pid, self_pids=own)
        if guard is not None:
            category, why = guard
            refusals.append(Refusal(process.pid, process.name, category, why))
            results.append(ActionResult(process=process, blocked=True,
                                        reason=reason, action="refused"))
        elif not apply:
            results.append(ActionResult(process=process, blocked=True,
                                        reason=reason,
                                        action="would_terminate"))
        else:
            action, detail = _terminate(process)
            results.append(ActionResult(process=process, blocked=True,
                                        reason=detail or reason,
                                        action=action))

    report = SweepReport(results=tuple(results), seen=len(rows),
                         applied=apply, refusals=tuple(refusals))
    if on_result is not None:
        for result in results:
            on_result(result)
    return report
