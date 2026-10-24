"""A7: what a deployment contains, decided before anything is copied.

The requirement (``docs/architecture.md`` §Target) is "nothing to install on the
target". That means shipping an embeddable CPython, the engine, the host, and the
data - and it means every file in the output has to be justified, because the
point of the exercise is that the target machine gets nothing else.

This module answers "what goes in the box" as **data**: a list of
:class:`Entry` objects, each with a source, a destination and a reason. Nothing
is copied here. That split matters because the interesting questions are all
answerable without touching the filesystem:

* Is every module of the engine included? (Ask the manifest.)
* Is anything from the *test* suites included by accident? (Ask the manifest.)
* Does the layout match what ``host.engine`` looks for? (Ask the manifest.)
* Is there a file whose destination escapes the output root? (Ask the manifest.)

A packer that could only be checked by running it would need an embeddable
CPython on every CI runner to answer any of those. This one does not.

Layout produced (matching ``docs/dependency-audit.md`` §4)::

    GreenDam-2026/
    ├── python/          embeddable CPython 3.12
    ├── engine/green_dam/
    ├── host/
    ├── data/
    ├── VERSION
    └── manifest.json    what was built, from what, with which hashes

``host/engine.py`` already looks for ``<root>/src`` then ``<root>/engine``, so
the deployed layout needs no change to the host.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "Entry",
    "Manifest",
    "UnsafeDestination",
    "build_manifest",
    "verify_manifest",
    "EXCLUDED_NAMES",
    "EXCLUDED_SUFFIXES",
    "ENGINE_PACKAGE",
    "HOST_PACKAGE",
    "DATA_FILES",
]

#: Files never shipped, by name. Test suites are excluded because the deployment
#: has no way to run them and they are larger than the code they test.
EXCLUDED_NAMES = frozenset({
    "__pycache__", ".pytest_cache", ".git", ".venv",
    "tests", "conftest.py",
})

#: Never shipped, by suffix.
EXCLUDED_SUFFIXES = frozenset({".pyc", ".pyo", ".pyd.bak", ".orig", ".rej"})

ENGINE_PACKAGE = "green_dam"
HOST_PACKAGE = "host"

#: Data files the engine or host actually reads at runtime.
#:
#: Deliberately a short explicit list rather than "all of data/": the directory
#: holds 13 MB of import inputs, corpora and staging dumps that the product never
#: opens. Shipping them would triple the download for no benefit, and the list
#: being explicit is what makes "is this file needed?" a question someone has to
#: answer when adding one.
#:
#: ``legacy-imported/falun-terms.json`` is **deliberately absent**. It was here,
#: and removing it is a product decision rather than a size one:
#:
#: * ``docs/corpus-eval.md`` §4.1 measured it against two corpora - ordinary
#:   novels 96.12% of files hit, the adult corpus 97.28%. A list that fires on
#:   both is not filtering, and shipping it as a parental control would be a
#:   claim this project has no business making on a parent's behalf.
#: * It is now available as an importable, switchable word pack instead
#:   (``green-dam words import``), so a parent who wants it can add it, inspect
#:   it, and turn it off - which the original product never allowed.
#:
#: The default deployment therefore ships **no** word lists. That is the honest
#: starting state: a filter with no lists reports "not assessed" rather than
#: "clean", and the engine already distinguishes the two.
DATA_FILES: tuple[str, ...] = (
    # Verb table for the chunker; absence is tolerated but the chunker is worse.
    "verb-table.json",
)


class UnsafeDestination(ValueError):
    """A manifest entry whose destination escapes the output root.

    Raised at manifest-build time, before anything is copied. A path containing
    ``..`` or an absolute path would write outside the deployment directory, and
    the check exists so that a future edit to the manifest cannot quietly do it.
    """


@dataclass(frozen=True)
class Entry:
    """One file to copy, with the reason it is there."""

    source: Path
    #: Path relative to the deployment root, always with ``/`` separators.
    destination: str
    #: Why this file is in the box. Required: an unexplained file is one nobody
    #: can decide to remove later.
    reason: str

    @property
    def size(self) -> int:
        try:
            return self.source.stat().st_size
        except OSError:
            return 0

    def to_dict(self) -> dict[str, object]:
        return {"destination": self.destination, "reason": self.reason,
                "size": self.size}


@dataclass
class Manifest:
    """The complete plan: every entry, plus what was skipped and why."""

    entries: tuple[Entry, ...] = ()
    #: Sources that exist but were excluded, with the rule that excluded them.
    excluded: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    #: Entries whose source was missing. Non-empty is a broken manifest.
    missing: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.entries)

    def by_top(self) -> dict[str, int]:
        """File counts per top-level directory. Used by the size report."""
        counts: dict[str, int] = {}
        for entry in self.entries:
            top = entry.destination.split("/", 1)[0]
            counts[top] = counts.get(top, 0) + 1
        return counts

    def summary(self) -> dict[str, object]:
        return {
            "files": len(self.entries),
            "total_bytes": self.total_bytes,
            "by_top": self.by_top(),
            "missing": [list(pair) for pair in self.missing],
            "excluded_count": len(self.excluded),
        }


def _included(path: Path) -> tuple[bool, str]:
    """Whether a source file belongs in the box, and the rule that decided.

    Exclusions are reported rather than silently applied: a packer that quietly
    drops a file is a packer whose output nobody can reason about.
    """
    for part in path.parts:
        if part in EXCLUDED_NAMES:
            return False, f"excluded directory/file name: {part}"
    if path.suffix in EXCLUDED_SUFFIXES:
        return False, f"excluded suffix: {path.suffix}"
    if path.name.startswith("."):
        return False, "hidden file"
    return True, ""


def _safe_destination(destination: str) -> str:
    """Validate a destination path and return it normalised to forward slashes."""
    normalised = destination.replace("\\", "/")
    if normalised.startswith("/") or ":" in normalised.split("/", 1)[0]:
        raise UnsafeDestination(
            f"destination {destination!r} is absolute; every entry must be "
            f"relative to the deployment root")
    parts = normalised.split("/")
    if any(part == ".." for part in parts):
        raise UnsafeDestination(
            f"destination {destination!r} contains '..' and would write "
            f"outside the deployment root")
    if not parts or parts[0] == "":
        raise UnsafeDestination(f"destination {destination!r} is empty")
    return normalised


def _walk(root: Path, *, destination_prefix: str,
          reason: str) -> tuple[list[Entry], list[tuple[str, str]]]:
    """Every includable file under ``root``, plus what was excluded and why."""
    entries: list[Entry] = []
    excluded: list[tuple[str, str]] = []
    if not root.is_dir():
        return entries, excluded
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        include, why = _included(path)
        if not include:
            excluded.append((f"{destination_prefix}/{relative}", why))
            continue
        entries.append(Entry(
            source=path,
            destination=_safe_destination(f"{destination_prefix}/{relative}"),
            reason=reason))
    return entries, excluded


def build_manifest(repo_root: Path) -> Manifest:
    """Plan the whole deployment. Reads the tree; copies nothing.

    ``repo_root`` is the checkout: the directory holding ``src/``, ``host/`` and
    ``data/``. The embeddable CPython is *not* part of this manifest - it is an
    external artefact, downloaded rather than built, and pretending otherwise
    would make the manifest's size numbers meaningless. :mod:`tools.package`
    reports it separately.
    """
    repo_root = Path(repo_root)
    entries: list[Entry] = []
    excluded: list[tuple[str, str]] = []
    missing: list[tuple[str, str]] = []

    engine_root = repo_root / "src" / ENGINE_PACKAGE
    if engine_root.is_dir():
        found, skipped = _walk(engine_root,
                               destination_prefix=f"engine/{ENGINE_PACKAGE}",
                               reason="engine module")
        entries.extend(found)
        excluded.extend(skipped)
    else:
        missing.append(("src/green_dam", "the engine package is absent"))

    host_root = repo_root / HOST_PACKAGE
    if host_root.is_dir():
        found, skipped = _walk(host_root, destination_prefix=HOST_PACKAGE,
                               reason="host module")
        entries.extend(found)
        excluded.extend(skipped)
    else:
        missing.append((HOST_PACKAGE, "the host package is absent"))

    for relative in DATA_FILES:
        source = repo_root / "data" / relative
        if not source.is_file():
            missing.append((f"data/{relative}", "runtime data file is absent"))
            continue
        entries.append(Entry(
            source=source,
            destination=_safe_destination(f"data/{relative}"),
            reason="runtime data"))

    # ``docs/dependency-audit.md`` is the record of why each third-party package
    # is present. Shipping it costs 20 KB and means the box explains itself.
    audit = repo_root / "docs" / "dependency-audit.md"
    if audit.is_file():
        entries.append(Entry(source=audit,
                             destination="docs/dependency-audit.md",
                             reason="dependency justification"))

    return Manifest(entries=tuple(entries), excluded=tuple(excluded),
                    missing=tuple(missing))


def file_digest(path: Path) -> str:
    """SHA-256 of a file, streamed so a large one does not sit in memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_manifest(root: Path, manifest: Manifest) -> list[str]:
    """Check that every entry exists under ``root`` with the planned size.

    Returns a list of problems; empty means the deployment matches the plan.
    Hashes are not compared here because the manifest records sizes only - a
    full hash check belongs to the installer's integrity pass, not to "did the
    copy finish".
    """
    problems: list[str] = []
    root = Path(root)
    for entry in manifest.entries:
        target = root / entry.destination
        if not target.is_file():
            problems.append(f"missing: {entry.destination}")
            continue
        expected = entry.size
        actual = target.stat().st_size
        if expected and actual != expected:
            problems.append(
                f"size mismatch: {entry.destination} "
                f"({actual} != {expected})")
    return problems
