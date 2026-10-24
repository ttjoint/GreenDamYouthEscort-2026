"""``green-dam words``: inspect, switch, add to, and share the word packs.

This is the command that answers the complaint the original product earned: a
parent could not find out which lists were active, could not turn one off, and
could not add their own.

The split between this module and :mod:`green_dam.wordpacks` is the engine's
no-I/O contract. ``wordpacks`` holds the *model* - what a pack is, what makes one
valid, how terms merge - and performs no file access. This module does the file
access. That is why the format rules can be tested without a filesystem and the
file handling can be tested without caring about the format.

Seven actions, and the shape of each is chosen so the answer is useful without
being a leak:

``list``     every pack with its term *count*, category, licence and state.
             Never the terms themselves - the status output of a filter must not
             become a copy of the filter.
``show``     one pack's metadata. Still no terms.
``enable``   turn a pack on.
``disable``  turn a pack off. Disabling is not hiding: a disabled pack stays
             listed, so "why is this blocked" has an answer.
``add``      append terms to a pack, creating it if needed.
``remove``   drop terms from a pack.
``import``   install a pack file from disk.
``export``   copy a pack out, for inspection or to share with another parent.

Terms are only ever accepted from a file or from ``--term``, and the *count*
added is reported rather than the terms, so a shell history does not become a
record of what the parent is filtering.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .wordpacks import (
    PACK_DIR,
    PACK_SUFFIX,
    Pack,
    PackError,
    PackIndex,
    index_of,
    parse_pack,
    validate_id,
)

__all__ = ["cmd_words"]


def _packs_dir(args) -> Path:
    """Where the packs live. ``--dir`` overrides, for tests and portable installs."""
    explicit = getattr(args, "dir", None)
    if explicit:
        return Path(explicit)
    return _default_packs_dir()


def _default_packs_dir() -> Path:
    """The machine-wide pack directory.

    The layout is repeated from ``host.paths`` rather than imported: the engine
    must not depend on ``host`` (``tests/test_contracts.py`` enforces that), and
    it is two environment lookups and one constant. An import would be a cycle.
    """
    import os

    base = os.environ.get("ProgramData") or r"C:\ProgramData"
    return Path(base) / "GreenDam2026" / PACK_DIR


def _pack_path(directory: Path, pack_id: str) -> Path:
    return directory / f"{pack_id}{PACK_SUFFIX}"


def _read_pack(path: Path) -> Pack:
    """Load one pack file. A missing or unreadable file yields a Pack with error."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return Pack(id=path.name, name=path.name, category="other", licence="",
                    source="", error=f"unreadable: {exc}")
    return parse_pack(text, fallback_id=path.name.split(".")[0])


def load_index(directory: Path) -> PackIndex:
    """Every pack in ``directory``, with per-file errors collected.

    A missing directory is not an error: a fresh install has no packs, and the
    default state is "no lists" rather than "something is wrong".
    """
    directory = Path(directory)
    if not directory.is_dir():
        return PackIndex(directory=directory)
    packs = [_read_pack(path) for path in sorted(directory.glob(f"*{PACK_SUFFIX}"))]
    index = index_of(packs)
    index.directory = directory
    return index


def _write_pack(path: Path, payload: dict) -> None:
    """Write JSON via a staging file, so a crash cannot leave half a pack."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(path.suffix + ".new")
    staging.write_text(json.dumps(payload, indent=1, ensure_ascii=False),
                       encoding="utf-8")
    staging.replace(path)


def set_enabled(directory: Path, pack_id: str, enabled: bool) -> Pack:
    """Turn one pack on or off, in place.

    Rewrites the pack file rather than keeping state elsewhere. One file per pack
    means "what is installed" and "what is on" cannot disagree, and a parent can
    copy a single file to move a list between machines.
    """
    validate_id(pack_id)
    path = _pack_path(directory, pack_id)
    if not path.is_file():
        raise PackError(f"no pack named {pack_id!r} in {directory}")

    pack = _read_pack(path)
    if pack.error:
        raise PackError(f"pack {pack_id!r} is broken: {pack.error}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["enabled"] = bool(enabled)
    _write_pack(path, payload)
    return _read_pack(path)


def import_pack(source: Path, directory: Path, *,
                override_id: str | None = None) -> Pack:
    """Install a pack file into ``directory``.

    The id is validated before anything is written: it becomes a filename, and a
    pack whose id contains ``..`` or a slash would write outside the pack
    directory. A hostile pack is the one case worth refusing loudly.
    """
    source = Path(source)
    if not source.is_file():
        raise PackError(f"{source} does not exist")

    pack = _read_pack(source)
    if pack.error:
        raise PackError(f"{source.name} cannot be imported: {pack.error}")

    pack_id = validate_id(override_id or pack.id)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["id"] = pack_id
    target = _pack_path(directory, pack_id)
    _write_pack(target, payload)
    return _read_pack(target)


def export_pack(directory: Path, pack_id: str, destination: Path) -> Path:
    """Copy one pack out, for inspection or sharing."""
    validate_id(pack_id)
    source = _pack_path(directory, pack_id)
    if not source.is_file():
        raise PackError(f"no pack named {pack_id!r} in {directory}")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Read-then-write rather than ``shutil.copy2``: ``shutil`` is on the engine's
    # forbidden-import list (A5), and the only thing ``copy2`` would add is the
    # original mtime, which a shared pack file does not need.
    destination.write_bytes(source.read_bytes())
    return destination


def _emit(args, payload: dict) -> int:
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=1, ensure_ascii=False))
    else:
        for line in _render(payload):
            print(line)
    return 0


def _render(payload: dict) -> list[str]:
    """Human-readable form. Counts and metadata only, never terms."""
    lines: list[str] = []
    action = payload.get("action")

    if action == "list":
        packs = payload.get("packs") or []
        if not packs:
            lines.append("no word packs installed")
            lines.append(f"directory: {payload.get('directory', '')}")
            return lines
        lines.append(f"{'id':<20} {'state':<9} {'terms':>7}  {'category':<10} name")
        for pack in packs:
            state = "on" if pack["usable"] else ("error" if pack["error"] else "off")
            lines.append(f"{pack['id']:<20} {state:<9} {pack['terms']:>7}  "
                         f"{pack['category']:<10} {pack['name']}")
            if pack["error"]:
                lines.append(f"  ! {pack['error']}")
        lines.append("")
        lines.append(f"total terms in use: {payload.get('total_terms', 0)}")
        return lines

    if action in {"enable", "disable"}:
        pack = payload["pack"]
        lines.append(f"{pack['id']}: {'enabled' if pack['enabled'] else 'disabled'} "
                     f"({pack['terms']} terms)")
        return lines

    if action == "import":
        pack = payload["pack"]
        lines.append(f"imported {pack['id']} ({pack['terms']} terms, "
                     f"{pack['category']})")
        return lines

    if action == "export":
        lines.append(f"exported {payload['id']} to {payload['path']}")
        return lines

    if action == "add":
        lines.append(f"{payload['id']}: added {payload['added']} terms, "
                     f"now {payload['terms']} total")
        return lines

    if action == "remove":
        lines.append(f"{payload['id']}: removed {payload['removed']} terms, "
                     f"now {payload['terms']} total")
        return lines

    if action == "show":
        pack = payload["pack"]
        lines.append(f"id       : {pack['id']}")
        lines.append(f"name     : {pack['name']}")
        lines.append(f"category : {pack['category']}")
        lines.append(f"licence  : {pack['licence'] or '(none stated)'}")
        lines.append(f"source   : {pack['source'] or '(none stated)'}")
        lines.append(f"terms    : {pack['terms']}")
        lines.append(f"state    : {'enabled' if pack['enabled'] else 'disabled'}")
        return lines

    lines.append(json.dumps(payload, ensure_ascii=False))
    return lines


def cmd_words(args) -> int:
    directory = _packs_dir(args)
    action = args.action

    try:
        if action == "list":
            index = load_index(directory)
            payload = index.summary()
            payload["action"] = "list"
            payload["directory"] = str(directory)
            return _emit(args, payload)

        if action == "show":
            index = load_index(directory)
            pack = index.get(args.id)
            if pack is None:
                print(f"no pack named {args.id!r} in {directory}", file=sys.stderr)
                return 2
            return _emit(args, {"action": "show", "pack": pack.to_dict()})

        if action in {"enable", "disable"}:
            pack = set_enabled(directory, args.id, action == "enable")
            return _emit(args, {"action": action, "pack": pack.to_dict()})

        if action == "import":
            # ``import`` takes a *file* as its first positional argument and an
            # optional id override via ``--id``. Taking the id first would make
            # ``words import foo.json`` mean "import pack foo", which is not what
            # anyone types.
            source = args.file_path or args.id
            if not source:
                raise PackError("words import needs a file path")
            pack = import_pack(Path(source), directory,
                               override_id=getattr(args, "as_id", None))
            return _emit(args, {"action": "import", "pack": pack.to_dict()})

        if action == "export":
            if not args.id:
                raise PackError("words export needs a pack id")
            target = args.file_path or f"{args.id}.wordpack.json"
            path = export_pack(directory, args.id, Path(target))
            return _emit(args, {"action": "export", "id": args.id,
                                "path": str(path)})

        if action in {"add", "remove"}:
            return _edit_terms(args, directory, action)

    except PackError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"unknown action {action!r}", file=sys.stderr)   # pragma: no cover
    return 2


def _edit_terms(args, directory: Path, action: str) -> int:
    """Add or remove terms in one pack, creating the pack for ``add``.

    Removal never fails because a term was absent: a parent tidying a list should
    not have to know exactly what is in it, and "already gone" is success.
    """
    pack_id = validate_id(args.id)
    path = _pack_path(directory, pack_id)

    if action == "add" and not path.is_file():
        _write_pack(path, {
            "id": pack_id,
            "name": getattr(args, "name", None) or pack_id,
            "category": getattr(args, "category", None) or "custom",
            "licence": "",
            "source": "added by the parent",
            "enabled": True,
            "terms": [],
        })

    if not path.is_file():
        raise PackError(f"no pack named {pack_id!r} in {directory}")

    existing = _read_pack(path)
    if existing.error:
        raise PackError(f"pack {pack_id!r} is broken: {existing.error}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    terms = list(payload.get("terms") or [])

    incoming: list[str] = []
    if getattr(args, "term", None):
        incoming.extend(t.strip() for t in args.term if t and t.strip())
    if getattr(args, "file", None):
        text = Path(args.file).read_text(encoding="utf-8")
        incoming.extend(line.strip() for line in text.splitlines()
                        if line.strip() and not line.lstrip().startswith("#"))

    if not incoming:
        raise PackError("nothing to do: pass --term or --file")

    if action == "add":
        seen = set(terms)
        added = 0
        for term in incoming:
            if term in seen:
                continue
            seen.add(term)
            terms.append(term)
            added += 1
        payload["terms"] = terms
        _write_pack(path, payload)
        return _emit(args, {"action": "add", "id": pack_id, "added": added,
                            "terms": len(terms)})

    drop = set(incoming)
    kept = [t for t in terms if t not in drop]
    removed = len(terms) - len(kept)
    payload["terms"] = kept
    _write_pack(path, payload)
    return _emit(args, {"action": "remove", "id": pack_id, "removed": removed,
                        "terms": len(kept)})
