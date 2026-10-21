"""The legacy 67-slot policy array (``sys.dat``) and its slot semantics.

This module is the *only* place where the legacy slot numbering lives. It is
deliberately data-only: it names slots and decodes the array, and never decides
what a slot means for filtering. Higher layers consume it.

Evidence for every name and for the ``index = slot_no - 1`` mapping is in
``analysis/03-DATA-FORMATS.md`` §F-002 and ``analysis/probe/p15_sysdat_flow.txt``
(``CLOSED``: read out of ``XNet2.exe`` at ``0x68dbc``-``0x68f78`` and confirmed
by the ``rep movsd`` copy at ``0x40bc16``).

Two traps this module exists to prevent
---------------------------------------
1. **Three different numbering systems.** The slot names below (1..25) are the
   *policy* numbering. ``XNet2.exe`` also contains a log-event classification
   (``{1.URL过滤}``..) and a policy-detail legend (``A2``-``A7``). Those are
   *not* ``sys.dat`` indices. Mixing them produced a wrong claim once already
   (``17 = allow auto shutdown``), so the log-event names never enter this file.
2. **Preset value != shipped value.** The ``PRESET_LEVEL_6`` table records what
   ``XNet2.exe`` writes when the user picks preset level 6; the ``sys.dat``
   that shipped on the CD is the *installed* state and differs in two slots
   (see ``test_sysdat.py``). Neither is "the" policy - both are legitimate and
   the difference is asserted, not smoothed over.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

#: Size of the legacy policy blob in bytes.
SLOT_COUNT = 67
BLOB_SIZE = SLOT_COUNT * 4

#: Policy numbering (1-based) as shipped. Index into this tuple is ``slot_no-1``.
#: Order and wording are taken from the GBK name table in ``XNet2.exe``.
#: Only 25 policies are *named*; slots 26..67 exist in the blob, are always
#: zero in every shipped copy, and are deliberately left unnamed so nobody
#: invents a meaning for them.
SLOT_NAMES: tuple[str, ...] = (
    "block_network",                    # 1
    "disable_filtering",                # 2
    "disable_logging",                  # 3
    "log_retention",                    # 4
    "image_filter_level",               # 5
    "image_filter_auto_blacklist",      # 6
    "trust_all_domestic",               # 7
    "block_programs_port",              # 8
    "block_programs_process",           # 9
    "disable_keyword_filter",           # 10
    "whitelist_only",                   # 11
    "preset_level",                     # 12
    "enable_image_filter",              # 13
    "whitelist_priority",               # 14
    "allow_whitelist",                  # 15
    "allow_report",                     # 16
    "allow_auto_screenshot",            # 17  (NOT "auto shutdown" - see docstring)
    "screenshot_interval",              # 18
    "screenshot_retention",             # 19
    "auto_update",                      # 20
    "auto_update_time",                 # 21
    "auto_blacklist_image_count",       # 22
    "close_on_bad_image",               # 23
    "allow_time_control",               # 24
    "show_mascot",                      # 25
)

#: Number of *named* policies. Slots beyond this are reserved (always zero).
NAMED_SLOTS = len(SLOT_NAMES)

#: Slots that are non-zero in the shipped ``sys.dat`` (0-based indices).
SHIPPED_NONZERO = (3, 4, 5, 7, 8, 13, 14, 16, 17, 18, 20, 21, 22, 24)

#: What ``XNet2.exe`` writes when preset level 6 is chosen (``0x40bbd0`` branch).
#: Only slots whose value the disassembly states explicitly are listed; the
#: rest are left alone by that branch and are therefore not claimed here.
PRESET_LEVEL_6: dict[int, int] = {4: 2, 5: 1, 7: 1, 8: 1, 11: 2, 18: 14}


def slot_index(slot_no: int) -> int:
    """Convert 1-based policy number to 0-based array index.

    ``CLOSED``: ``XNet2.exe`` copies a 67-dword stack array to ``0x46f1e0``
    with the first element at ``[esp+0x40]``; ``[esp+0x50]`` holds policy 5.

    Only the first :data:`NAMED_SLOTS` policies have a name; asking for a
    reserved slot is an error rather than a silent zero.
    """
    if not 1 <= slot_no <= NAMED_SLOTS:
        raise ValueError(
            f"policy number out of range 1..{NAMED_SLOTS}: {slot_no}")
    return slot_no - 1


def slot_name(slot_no: int) -> str:
    """Human/machine name for a 1-based policy number."""
    return SLOT_NAMES[slot_index(slot_no)]


@dataclass(frozen=True)
class PolicyArray:
    """A decoded ``sys.dat`` / ``xconfigs.dat`` blob.

    ``attested`` is False for hand-built arrays; it is True only for an array
    decoded from a blob of the documented size.
    """

    values: tuple[int, ...]
    attested: bool = False

    def __post_init__(self) -> None:
        if len(self.values) != SLOT_COUNT:
            raise ValueError(
                f"expected {SLOT_COUNT} slots, got {len(self.values)}")

    # -- access by policy number (1-based), the numbering the UI uses --------

    def get(self, slot_no: int) -> int:
        return self.values[slot_index(slot_no)]

    def raw(self, index: int) -> int:
        """Read by 0-based index, for reserved slots that have no name."""
        return self.values[index]

    def nonzero(self) -> tuple[tuple[int, int], ...]:
        """``(policy_no, value)`` pairs for every non-zero slot.

        Reserved slots (26..67) are included if non-zero: they have no name,
        but hiding a non-zero reserved slot would be dishonest.
        """
        return tuple((i + 1, v) for i, v in enumerate(self.values) if v)

    def named(self) -> dict[str, int]:
        """Map every named policy to its value."""
        return {name: self.values[i] for i, name in enumerate(SLOT_NAMES)}

    # -- flags the rest of the codebase actually asks about -----------------

    @property
    def filtering_disabled(self) -> bool:
        return self.get(2) != 0

    @property
    def image_filter_enabled(self) -> bool:
        return self.get(13) != 0 and self.get(5) != 0

    @property
    def image_filter_level(self) -> int:
        """0/1/2 -> low/mid/high, matching the UI's three-way control."""
        return self.get(5)

    @property
    def keyword_filter_disabled(self) -> bool:
        return self.get(10) != 0

    @property
    def auto_screenshot(self) -> bool:
        return self.get(17) != 0

    @property
    def screenshot_retention_days(self) -> int:
        return self.get(19)


def decode(blob: bytes) -> PolicyArray:
    """Decode a raw ``sys.dat``-shaped blob (little-endian uint32s)."""
    if len(blob) != BLOB_SIZE:
        raise ValueError(
            f"expected {BLOB_SIZE} bytes ({SLOT_COUNT} uint32), got {len(blob)}")
    values = struct.unpack(f"<{SLOT_COUNT}I", blob)
    return PolicyArray(values=values, attested=True)


def encode(array: PolicyArray) -> bytes:
    """Encode back to the legacy blob form (round-trip partner of ``decode``)."""
    return struct.pack(f"<{SLOT_COUNT}I", *array.values)


def load(path: Path) -> PolicyArray:
    """Read and decode a ``sys.dat`` / ``xconfigs.dat`` file."""
    return decode(path.read_bytes())
