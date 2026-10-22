"""Weekly time control: the 7x24 allow/deny grid from the original's UI.

Source: ``analysis/probe/p29_time_control.txt``. The manual's screenshots
(pages 18-19) are the primary evidence for the semantics; the binary supplies
the policy slot and the anti-tamper NTP servers.

What the original did
---------------------
A grid with one cell per hour of the week - seven rows (days) against 24
columns (hours). Grey denies, green allows, the checkbox at the bottom is the
master switch, and a warning fires five minutes before a transition. The
original also synced the clock against ten hard-coded NTP servers, so a child
could not bypass the schedule by changing the system clock.

What this module does
---------------------
Implements the grid, the master-switch interaction, the transition query, and a
serialisable form. Time is injected: every query takes a ``datetime`` and none
of them read the system clock, so tests are deterministic and a host process can
supply a clock it controls.

What is deliberately not here
-----------------------------
NTP synchronisation. It needs network I/O, which the engine's contracts forbid,
and the original's server list is from 2009. Architecture §M7 is explicit that an
administrator can always defeat this product; the NTP sync bought resistance
against a *child* changing the clock, and that is the host's concern, not the
engine's. This module exposes :func:`next_change` so the host can build the
five-minute warning on top.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

#: Days, index 0..6. The original's grid starts at 星期日 (Sunday), matching
#: ``0x681fa`` in XNet2.exe and the manual's row order.
DAY_NAMES = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday",
             "Friday", "Saturday")

#: Days per week and hours per day - the grid is fixed at 7x24, as the manual
#: shows. Python's ``weekday()`` is Monday=0, so conversion is needed.
DAYS = 7
HOURS = 24

#: The five-minute warning the manual describes (切换提前5分钟预警).
WARN_BEFORE = timedelta(minutes=5)

#: Bits per day when packed - one byte per hour would be 24 bytes a day; the
#: original stored this somewhere that was never recovered (p29 §2), so the
#: serialised form here is our own.
BITS_PER_DAY = HOURS


class ScheduleError(ValueError):
    """Raised when a grid or blob is malformed."""


def _check(day: int, hour: int) -> None:
    if not 0 <= day < DAYS:
        raise ScheduleError(f"day must be 0..{DAYS - 1} (Sunday=0), got {day}")
    if not 0 <= hour < HOURS:
        raise ScheduleError(f"hour must be 0..{HOURS - 1}, got {hour}")


@dataclass
class WeeklyGrid:
    """Seven days by 24 hours of allow flags.

    A cell is *left-closed, right-open*: ``grid[3][14]`` true means Wednesday
    14:00 up to 15:00 is allowed. Adjacent cells therefore join without gaps or
    overlaps, which is the only reading of the manual's grid that makes sense -
    the alternative (closed on both ends) would make 14:00 belong to two cells
    or neither.
    """

    #: ``cells[day][hour]``. All False means "nothing allowed", which with the
    #: master switch off is the shipped default: the manual's figure 18 shows
    #: every cell grey and the box unticked.
    cells: list[list[bool]]

    def __post_init__(self) -> None:
        if len(self.cells) != DAYS:
            raise ScheduleError(
                f"grid must have {DAYS} days, got {len(self.cells)}")
        for day, row in enumerate(self.cells):
            if len(row) != HOURS:
                raise ScheduleError(
                    f"day {day} must have {HOURS} hours, got {len(row)}")
            for hour, flag in enumerate(row):
                if not isinstance(flag, bool):
                    raise ScheduleError(
                        f"cell ({day}, {hour}) must be bool, "
                        f"got {type(flag).__name__}")

    # -- construction -------------------------------------------------------

    @classmethod
    def empty(cls) -> "WeeklyGrid":
        """The shipped default: nothing allowed (manual figure 18)."""
        return cls(cells=[[False] * HOURS for _ in range(DAYS)])

    @classmethod
    def from_ranges(cls, ranges: dict[int, list[tuple[int, int]]]) -> "WeeklyGrid":
        """Build from ``{day: [(start_hour, end_hour), ...]}``.

        End hours are exclusive, matching the cell semantics, so
        ``{2: [(9, 12)]}`` allows Tuesday 9:00-11:59.
        """
        grid = cls.empty()
        for day, spans in ranges.items():
            _check(day, 0)
            for start, end in spans:
                if not 0 <= start < end <= HOURS:
                    raise ScheduleError(
                        f"day {day}: span ({start}, {end}) must satisfy "
                        f"0 <= start < end <= {HOURS}")
                for hour in range(start, end):
                    grid.cells[day][hour] = True
        return grid

    # -- queries ------------------------------------------------------------

    def allowed(self, day: int, hour: int) -> bool:
        """Whether one cell allows access."""
        _check(day, hour)
        return self.cells[day][hour]

    def at(self, when: datetime) -> bool:
        """Whether ``when`` falls in an allowed cell.

        Pure function of the argument: no clock reads. Python counts Monday as
        weekday 0; the grid counts Sunday as day 0, so the index is
        ``(weekday + 1) % 7``.
        """
        day = (when.weekday() + 1) % 7
        return self.cells[day][when.hour]

    def is_allowed_at(self, when: datetime, *, enabled: bool) -> bool:
        """The full decision: grid cell AND master switch.

        This is what the host asks at connection time. ``enabled`` is slot 24
        (``allow_time_control``) read by the caller - it is a policy value, not
        part of the grid, and a policy read belongs to the policy layer.
        """
        if not enabled:
            return True
        return self.at(when)

    def next_change(self, after: datetime) -> datetime | None:
        """The earliest cell boundary after ``after`` where the flag flips.

        Returns None when no transition exists in the next week, i.e. every
        cell in the grid is the same. The five-minute warning the manual
        describes is ``next_change(now) - WARN_BEFORE``, which the host
        schedules; this module does not fire events.
        """
        day = (after.weekday() + 1) % 7
        hour = after.hour
        current = self.cells[day][hour]
        # Walk forward from the top of the next hour, up to a full week.
        for offset in range(1, DAYS * HOURS + 1):
            minute_of_week = (day * HOURS + hour + offset) % (DAYS * HOURS)
            d, h = divmod(minute_of_week, HOURS)
            if self.cells[d][h] != current:
                boundary = after.replace(minute=0, second=0, microsecond=0)
                return boundary + timedelta(hours=offset)
        return None

    def warn_at(self, after: datetime, *,
                enabled: bool = True) -> datetime | None:
        """When the five-minute warning should fire, or None.

        None when the switch is off (no schedule is in force) or when the grid
        never transitions. The host schedules the actual notification; this
        module only computes the time.
        """
        if not enabled:
            return None
        change = self.next_change(after)
        if change is None:
            return None
        return change - WARN_BEFORE

    # -- reporting ----------------------------------------------------------

    def count(self) -> int:
        """How many hours are allowed. Counts only - log-safe."""
        return sum(1 for row in self.cells for flag in row if flag)

    def summary(self) -> dict:
        """Counts per day, never cell-by-cell state.

        A week of booleans is not sensitive, but a summary is what a log line
        wants, and keeping it counts-only matches how every other module
        reports.
        """
        return {
            "allowed_hours": self.count(),
            "per_day": {DAY_NAMES[d]: sum(1 for f in row if f)
                        for d, row in enumerate(self.cells)},
        }

    # -- serialisation ------------------------------------------------------

    def to_blob(self) -> bytes:
        """Pack to 21 bytes: 168 bits, row-major, day-major.

        Bit order is day-major then hour, least-significant bit first, so bit
        ``(day * 24 + hour) % 8`` of byte ``(day * 24 + hour) // 8`` is the
        cell. 21 bytes is exactly 168 bits with nothing spare.
        """
        bits = bytearray((DAYS * HOURS + 7) // 8)
        for d in range(DAYS):
            for h in range(HOURS):
                if self.cells[d][h]:
                    index = d * HOURS + h
                    bits[index // 8] |= 1 << (index % 8)
        return bytes(bits)

    @classmethod
    def from_blob(cls, blob: bytes) -> "WeeklyGrid":
        """Unpack :meth:`to_blob`'s form; rejects anything else."""
        expected = (DAYS * HOURS + 7) // 8
        if len(blob) != expected:
            raise ScheduleError(
                f"grid blob must be {expected} bytes, got {len(blob)}")
        cells = [[False] * HOURS for _ in range(DAYS)]
        for d in range(DAYS):
            for h in range(HOURS):
                index = d * HOURS + h
                cells[d][h] = bool(blob[index // 8] >> (index % 8) & 1)
        return cls(cells=cells)

    def to_json(self) -> str:
        """A readable form for config files. ``enabled`` is not included - it
        is policy slot 24, not grid state."""
        return json.dumps({"cells": self.cells}, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> "WeeklyGrid":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ScheduleError(f"grid JSON is invalid: {exc}") from exc
        if not isinstance(payload, dict) or "cells" not in payload:
            raise ScheduleError("grid JSON must have a 'cells' key")
        return cls(cells=payload["cells"])
