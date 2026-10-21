"""Persisting the policy array, safely.

The legacy product wrote ``xconfigs.dat`` by opening the file ``"wb"`` and
dumping all 268 bytes (``analysis/probe/p15``: ``WriteFileBin`` at ``0x40ad67``).
That is a whole-file overwrite with no temp file, no fsync and no validation, so
an interrupted write left a truncated policy file and the product silently fell
back to defaults.

This module keeps the **format** byte-identical - a 268-byte little-endian
uint32 array is what the importer and any legacy tool expect - and fixes only
the write path:

* write to a temporary file in the same directory, ``fsync``, then ``os.replace``
  (atomic on POSIX and Windows for same-volume renames)
* validate before writing: exactly 67 slots, and no named slot silently changed
  to an out-of-range value
* read back and re-validate after writing, so a bad disk surfaces here

Why no schema version field
---------------------------
The legacy format has no room for one: all 67 slots are accounted for, 26..67
reserved and zero. Adding a header would break compatibility with the importer
that reads the original files. Provenance is therefore kept in a **sidecar**
``.meta.json`` that old tools ignore and new ones can check.

That sidecar is also where the honest limits are recorded: which slot names are
attested, and that the score-to-action mapping is still unknown. A config file
that claims more certainty than the code has would be the same failure mode as
the docs drifting from the evidence.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from . import sysdat

#: Name of the sidecar written next to the policy blob.
META_SUFFIX = ".meta.json"

#: Schema tag for the sidecar (not the blob - the blob has no room for one).
META_SCHEMA = 1

#: Values any named slot is allowed to take. The shipped blob only ever
#: contains these, so a write outside the set is almost certainly a bug.
#: Reserved slots (26..67) are always 0.
ALLOWED_VALUES = frozenset({0, 1, 2, 3, 5, 14})


class ConfigError(ValueError):
    """Raised when a policy array would be written in an invalid state."""


@dataclass(frozen=True)
class ConfigPaths:
    """Where a policy blob and its sidecar live."""

    blob: Path
    meta: Path

    @classmethod
    def for_blob(cls, blob: Path) -> "ConfigPaths":
        return cls(blob=blob, meta=blob.with_name(blob.name + META_SUFFIX))


def validate(array: sysdat.PolicyArray, *, strict: bool = True) -> None:
    """Reject arrays that the legacy format or our own constraints forbid.

    ``strict`` additionally enforces that every named slot holds a value seen in
    the shipped blob. Turn it off only when deliberately testing edge cases.
    """
    if len(array.values) != sysdat.SLOT_COUNT:
        raise ConfigError(
            f"expected {sysdat.SLOT_COUNT} slots, got {len(array.values)}")

    for index in range(sysdat.NAMED_SLOTS, sysdat.SLOT_COUNT):
        if array.values[index] != 0:
            raise ConfigError(
                f"reserved slot {index + 1} must be 0, got {array.values[index]}")

    if strict:
        for index in range(sysdat.NAMED_SLOTS):
            value = array.values[index]
            if value not in ALLOWED_VALUES:
                raise ConfigError(
                    f"policy {index + 1} ({sysdat.SLOT_NAMES[index]}) has "
                    f"value {value}, not in {sorted(ALLOWED_VALUES)}")


def _atomic_write(path: Path, payload: bytes) -> None:
    """Write ``payload`` to ``path`` via a same-directory temp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Leave no partial temp file behind on any failure path.
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def _meta_payload(array: sysdat.PolicyArray, source: str) -> dict:
    return {
        "schema": META_SCHEMA,
        "written_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "slot_count": sysdat.SLOT_COUNT,
        "named_slots": sysdat.NAMED_SLOTS,
        "blob_bytes": sysdat.BLOB_SIZE,
        # Honest provenance: which names are backed by disassembly, and which
        # semantics are still unknown. Old tools ignore this file entirely.
        "attested": {
            "slot_names": True,
            "index_is_slot_minus_one": True,
            "score_to_action": False,
        },
        "nonzero": [
            {"policy": slot_no, "name": sysdat.SLOT_NAMES[slot_no - 1],
             "value": value}
            for slot_no, value in array.nonzero()
            if slot_no <= sysdat.NAMED_SLOTS
        ],
    }


def save(
    array: sysdat.PolicyArray,
    path: Path,
    *,
    source: str = "",
    strict: bool = True,
    write_meta: bool = True,
) -> ConfigPaths:
    """Persist ``array`` to ``path`` atomically, with a sidecar by default."""
    validate(array, strict=strict)
    payload = sysdat.encode(array)

    # Round-trip check before touching the disk: if encode/decode disagree, the
    # bug is here, not in the filesystem.
    if sysdat.decode(payload).values != array.values:
        raise ConfigError("encode/decode round trip disagreed; refusing to write")

    paths = ConfigPaths.for_blob(path)
    _atomic_write(path, payload)

    # Read back what actually landed. A short write or a bad sector shows up
    # now rather than on the next start.
    landed = sysdat.load(path)
    if landed.values != array.values:
        raise ConfigError(
            f"read-back mismatch after writing {path}; file may be corrupt")

    if write_meta:
        meta = json.dumps(_meta_payload(array, source), ensure_ascii=False,
                          indent=2, sort_keys=True).encode("utf-8")
        _atomic_write(paths.meta, meta)

    return paths


def load(path: Path, *, strict: bool = False) -> sysdat.PolicyArray:
    """Load a policy blob, validating structure (not value domain) by default.

    ``strict=False`` on read because a file produced by the original product is
    authoritative about its own contents: refusing to read it because a value
    looks unusual would make the importer unable to read real data. Structural
    errors (wrong size, non-zero reserved slot) are still rejected.
    """
    if not path.exists():
        raise ConfigError(f"no such policy file: {path}")
    raw = path.read_bytes()
    if len(raw) != sysdat.BLOB_SIZE:
        raise ConfigError(
            f"{path} is {len(raw)} bytes, expected {sysdat.BLOB_SIZE}")
    array = sysdat.decode(raw)
    validate(array, strict=strict)
    return array


def read_meta(path: Path) -> dict | None:
    """Read the sidecar if present. Returns None when absent."""
    meta_path = ConfigPaths.for_blob(path).meta
    if not meta_path.exists():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


def set_values(
    array: sysdat.PolicyArray,
    changes: dict[int, int],
    *,
    strict: bool = True,
) -> sysdat.PolicyArray:
    """Return a new array with ``{policy_no: value}`` applied.

    Changes are addressed by **policy number** (1-based), the numbering the UI
    and the name table use - not by array index. Passing an index here would be
    off by one in a way that is easy to miss, so the key is validated.
    """
    values = list(array.values)
    for slot_no, value in changes.items():
        index = sysdat.slot_index(slot_no)   # raises for out-of-range
        values[index] = value
    out = sysdat.PolicyArray(values=tuple(values))
    validate(out, strict=strict)
    return out


def default_for_fresh_install() -> sysdat.PolicyArray:
    """The shipped ``sys.dat`` values, for a machine with no config yet.

    These are the bytes on the CD, not the preset-level-6 branch. The two
    differ in slots 11 and 18 and both are legitimate; see ``sysdat``.
    """
    values = [0] * sysdat.SLOT_COUNT
    for index in sysdat.SHIPPED_NONZERO:
        values[index] = _SHIPPED_VALUES[index]
    return sysdat.PolicyArray(values=tuple(values))


#: Values for the non-zero shipped slots, in index order. Kept beside
#: ``SHIPPED_NONZERO`` so the pair cannot drift.
_SHIPPED_VALUES = {
    3: 3, 4: 2, 5: 1, 7: 1, 8: 1, 13: 1, 14: 1,
    16: 1, 17: 3, 18: 3, 20: 14, 21: 5, 22: 1, 24: 1,
}
