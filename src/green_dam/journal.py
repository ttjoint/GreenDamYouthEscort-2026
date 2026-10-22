"""The install journal: every change, recorded so it can be undone.

``docs/architecture.md`` §M7 requires:

    Uninstall is a strict inverse. Every change is journalled to a manifest at
    install time and rolled back in reverse: remove firewall rules, revoke the
    CA, delete the service, clean registry, delete files, restore settings. A
    ``--dry-run`` lists what would be removed. No forced reboot.

Why this is its own module
--------------------------
A journal is not a config file and not an event log. It has two properties
neither of those has:

* **Order matters.** Rollback runs in reverse, because a later change may depend
  on an earlier one (a firewall rule that references a service account, a file
  inside a directory that was created first). A set of changes would lose that.
* **Every entry must declare its inverse.** A change with no known inverse is a
  bug in the installer, and it has to surface at plan time rather than being
  silently skipped at uninstall time. :func:`plan_uninstall` reports those as
  ``unreversible`` instead of quietly dropping them - the same "not looking is
  not the same as clean" rule the rest of the project follows.

This module is pure data and planning. It performs no filesystem, registry or
service operations: executing a plan is the Windows host's job, which keeps the
engine portable and this file testable.

Why a manifest rather than "just know what we installed"
--------------------------------------------------------
The original product left registry keys behind after uninstall, and that is
listed in ``architecture.md`` as a behaviour explicitly not carried over. The
only way to guarantee a strict inverse is to write down what was done while
doing it. A manifest that is *generated from the install script* is a promise; a
manifest that is *written as the install runs* is a record.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

#: Bumped when the manifest layout changes incompatibly.
SCHEMA = 1

#: The product this journal belongs to, so a stray manifest is not mistaken for
#: one of ours.
PRODUCT = "green-dam-2026"


class ChangeKind(str, Enum):
    """What was changed. The value is the stable token written to disk."""

    FIREWALL_RULE = "firewall_rule"
    CERTIFICATE = "certificate"
    SERVICE = "service"
    REGISTRY = "registry"
    FILE = "file"
    DIRECTORY = "directory"
    SETTING = "setting"


class JournalError(ValueError):
    """Raised when a manifest is malformed."""


class UnreversibleChange(JournalError):
    """Raised when a change cannot be undone and strictness was requested."""


@dataclass(frozen=True)
class Change:
    """One recorded change.

    ``target`` identifies what was touched: a rule name, a thumbprint, a service
    name, a registry path, a file path. ``detail`` holds whatever the inverse
    needs (the previous value of a setting, the rule's specification) so
    rollback does not have to guess.

    ``inverse`` is the operation that undoes this change, as a stable token.
    Making it a required field is deliberate: a change that nobody thought about
    undoing cannot be recorded.
    """

    kind: ChangeKind
    target: str
    inverse: str
    detail: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.target:
            raise JournalError("a change must name its target")
        if not self.inverse:
            raise JournalError(
                f"change {self.kind.value}:{self.target} has no inverse; "
                f"record how to undo it or do not record it")

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "target": self.target,
            "inverse": self.inverse,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Change":
        try:
            kind = ChangeKind(payload["kind"])
            target = payload["target"]
            inverse = payload["inverse"]
        except KeyError as exc:
            raise JournalError(f"change is missing {exc.args[0]!r}") from exc
        except ValueError as exc:
            raise JournalError(f"unknown change kind: {payload['kind']!r}") from exc
        detail = payload.get("detail", {})
        if not isinstance(detail, dict):
            raise JournalError(f"detail must be an object, got {type(detail).__name__}")
        return cls(kind=kind, target=target, inverse=inverse, detail=detail)

    def __repr__(self) -> str:
        """Shows the shape of the change, not the previous value.

        ``detail`` can hold a restored setting's old value, which may be a
        secret-adjacent string. It stays out of repr for the same reason
        ``Finding.surface`` does.
        """
        return (f"Change(kind={self.kind.value!r}, target={self.target!r}, "
                f"inverse={self.inverse!r})")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Manifest:
    """The journal: an ordered record of what an install changed."""

    changes: list[Change] = field(default_factory=list)
    created_at: str = field(default_factory=_utc_now)
    product: str = PRODUCT
    schema: int = SCHEMA
    #: Version string of the product that wrote this journal.
    version: str = ""
    #: True once a successful uninstall has run, so a second run is a no-op
    #: rather than a destructive surprise.
    rolled_back: bool = False

    # -- recording ---------------------------------------------------------

    def record(self, kind: ChangeKind, target: str, inverse: str,
               **detail) -> Change:
        """Append a change. Order is preserved and meaningful."""
        change = Change(kind=kind, target=target, inverse=inverse, detail=detail)
        self.changes.append(change)
        return change

    def __len__(self) -> int:
        return len(self.changes)

    def kinds(self) -> dict[str, int]:
        """Counts per kind - never the targets themselves."""
        counts: dict[str, int] = {}
        for change in self.changes:
            counts[change.kind.value] = counts.get(change.kind.value, 0) + 1
        return dict(sorted(counts.items()))

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "product": self.product,
            "version": self.version,
            "created_at": self.created_at,
            "rolled_back": self.rolled_back,
            "changes": [c.to_dict() for c in self.changes],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2,
                          sort_keys=True)

    @classmethod
    def from_dict(cls, payload: object) -> "Manifest":
        if not isinstance(payload, dict):
            raise JournalError(
                f"manifest must be an object, got {type(payload).__name__}")
        if payload.get("product") != PRODUCT:
            raise JournalError(
                f"manifest belongs to {payload.get('product')!r}, not {PRODUCT!r}")
        schema = payload.get("schema")
        if schema != SCHEMA:
            raise JournalError(
                f"manifest schema is {schema!r}, this build understands {SCHEMA}")
        raw = payload.get("changes", [])
        if not isinstance(raw, list):
            raise JournalError("changes must be a list")
        return cls(
            changes=[Change.from_dict(item) for item in raw],
            created_at=str(payload.get("created_at", "")),
            product=PRODUCT,
            schema=SCHEMA,
            version=str(payload.get("version", "")),
            rolled_back=bool(payload.get("rolled_back", False)),
        )

    @classmethod
    def from_json(cls, text: str) -> "Manifest":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise JournalError(f"manifest is not valid JSON: {exc}") from exc
        return cls.from_dict(payload)

    # -- storage -----------------------------------------------------------

    def save(self, path: Path) -> None:
        """Write atomically; the manifest must survive a crash mid-install."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_json().encode("utf-8")
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent),
                                        prefix=path.name + ".", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
            raise

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        if not path.exists():
            raise JournalError(f"no manifest at {path}")
        return cls.from_json(path.read_text(encoding="utf-8"))


# -- uninstall planning ----------------------------------------------------

@dataclass(frozen=True)
class Step:
    """One rollback step, in the order it must run."""

    order: int
    change: Change

    @property
    def description(self) -> str:
        return f"{self.change.inverse} {self.change.kind.value} {self.change.target}"

    def to_dict(self) -> dict:
        return {
            "order": self.order,
            "inverse": self.change.inverse,
            "kind": self.change.kind.value,
            "target": self.change.target,
            "detail": self.change.detail,
        }


@dataclass(frozen=True)
class UninstallPlan:
    """What an uninstall would do, without doing it.

    ``steps`` is already in execution order: reverse of install order, because a
    later change may depend on an earlier one.
    """

    steps: tuple[Step, ...] = ()
    #: Changes whose inverse is unknown. Reported, never silently dropped.
    unreversible: tuple[Change, ...] = ()
    #: Non-fatal observations the administrator should see.
    warnings: tuple[str, ...] = ()
    #: True when the journal says an uninstall already ran.
    already_rolled_back: bool = False

    @property
    def complete(self) -> bool:
        """True when every recorded change can be undone."""
        return not self.unreversible

    @property
    def count(self) -> int:
        return len(self.steps)

    def summary(self) -> dict:
        """Counts only - no targets, no previous values."""
        return {
            "steps": self.count,
            "unreversible": len(self.unreversible),
            "warnings": list(self.warnings),
            "already_rolled_back": self.already_rolled_back,
            "complete": self.complete,
            "kinds": self._kind_counts(),
        }

    def _kind_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for step in self.steps:
            key = step.change.kind.value
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    def render(self) -> str:
        """A dry-run listing, one line per step, in execution order."""
        lines: list[str] = []
        if self.already_rolled_back:
            lines.append("already rolled back; nothing to do")
        if self.warnings:
            lines.append("warnings:")
            lines.extend(f"  - {w}" for w in self.warnings)
        if self.unreversible:
            lines.append("cannot be undone (will remain on the system):")
            lines.extend(
                f"  ! {c.kind.value} {c.target}" for c in self.unreversible)
        if self.steps:
            lines.append(f"would perform {self.count} step(s), in this order:")
            lines.extend(f"  {s.order:>2}. {s.description}" for s in self.steps)
        elif not self.already_rolled_back and not self.unreversible:
            lines.append("nothing to do; the journal is empty")
        if self.steps or self.unreversible:
            lines.append("no reboot will be requested")
        return "\n".join(lines)


def plan_uninstall(manifest: Manifest, *,
                   strict: bool = False) -> UninstallPlan:
    """Build the rollback plan: reverse order, with problems surfaced.

    ``strict`` raises :class:`UnreversibleChange` instead of reporting it. The
    default is to report, because an uninstaller that refuses to run leaves the
    user with a half-installed product; reporting lets them decide.
    """
    warnings: list[str] = []
    unreversible: list[Change] = []

    if manifest.rolled_back:
        return UninstallPlan(already_rolled_back=True)

    # Reverse order: undo the most recent change first.
    steps: list[Step] = []
    order = 0
    for change in reversed(manifest.changes):
        if not change.inverse:
            unreversible.append(change)
            continue
        order += 1
        steps.append(Step(order=order, change=change))

    if unreversible:
        if strict:
            raise UnreversibleChange(
                f"{len(unreversible)} change(s) have no inverse; first is "
                f"{unreversible[0].kind.value}:{unreversible[0].target}")
        warnings.append(
            f"{len(unreversible)} change(s) cannot be undone and will remain")

    if not manifest.changes:
        warnings.append("journal is empty; was the product ever installed?")

    return UninstallPlan(steps=tuple(steps),
                         unreversible=tuple(unreversible),
                         warnings=tuple(warnings))


def mark_rolled_back(manifest: Manifest, path: Path | None = None) -> Manifest:
    """Record that an uninstall completed, and persist if a path is given.

    Keeping the journal after rollback - rather than deleting it - means a
    second run can say "already done" instead of repeating destructive steps.
    """
    manifest.rolled_back = True
    if path is not None:
        manifest.save(path)
    return manifest


def verify_inverse(manifest: Manifest) -> list[str]:
    """Check the journal is internally consistent, for install-time assertions.

    Returns a list of problems; empty means the journal is usable. Called by the
    installer before it reports success, so a missing inverse is caught while the
    install can still be aborted.
    """
    problems: list[str] = []
    if manifest.product != PRODUCT:
        problems.append(f"wrong product: {manifest.product!r}")
    if manifest.schema != SCHEMA:
        problems.append(f"wrong schema: {manifest.schema!r}")
    for index, change in enumerate(manifest.changes):
        if not change.inverse:
            problems.append(f"change {index} has no inverse")
        if not change.target:
            problems.append(f"change {index} has no target")
    return problems
