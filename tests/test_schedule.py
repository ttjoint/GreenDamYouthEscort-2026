"""Tests for the weekly time-control grid.

Source: analysis/probe/p29_time_control.txt. The semantics come from the
manual's screenshots (pages 18-19); the fixture used here reproduces figure 19
exactly - Wednesday, three hours.

Time is injected everywhere. No test reads the system clock.
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam.schedule import (  # noqa: E402
    DAYS,
    HOURS,
    WARN_BEFORE,
    ScheduleError,
    WeeklyGrid,
)

#: 2026-01-07 is a Wednesday. Used for every figure-19 scenario.
WED_1400 = datetime(2026, 1, 7, 14, 0)
WED_1430 = datetime(2026, 1, 7, 14, 30)
WED_1700 = datetime(2026, 1, 7, 17, 0)


def figure19() -> WeeklyGrid:
    """The manual's figure 19: Wednesday, 14:00-17:00 allowed."""
    return WeeklyGrid.from_ranges({3: [(14, 17)]})


class ConstructionTest(unittest.TestCase):
    def test_grid_is_7x24(self):
        self.assertEqual((DAYS, HOURS), (7, 24))

    def test_empty_grid_denies_everything(self):
        # Manual figure 18: all cells grey.
        grid = WeeklyGrid.empty()
        self.assertEqual(grid.count(), 0)
        self.assertFalse(grid.at(WED_1430))

    def test_rejects_wrong_day_count(self):
        with self.assertRaises(ScheduleError):
            WeeklyGrid(cells=[[False] * HOURS for _ in range(6)])

    def test_rejects_wrong_hour_count(self):
        cells = [[False] * HOURS for _ in range(DAYS)]
        cells[0] = [False] * 23
        with self.assertRaises(ScheduleError):
            WeeklyGrid(cells=cells)

    def test_rejects_non_bool_cell(self):
        cells = [[False] * HOURS for _ in range(DAYS)]
        cells[0][0] = 1
        with self.assertRaises(ScheduleError):
            WeeklyGrid(cells=cells)

    def test_from_ranges(self):
        grid = WeeklyGrid.from_ranges({3: [(14, 17)]})
        self.assertEqual(grid.count(), 3)

    def test_from_ranges_rejects_inverted_span(self):
        with self.assertRaises(ScheduleError):
            WeeklyGrid.from_ranges({3: [(17, 14)]})

    def test_from_ranges_rejects_out_of_range(self):
        with self.assertRaises(ScheduleError):
            WeeklyGrid.from_ranges({3: [(0, 25)]})

    def test_from_ranges_rejects_bad_day(self):
        with self.assertRaises(ScheduleError):
            WeeklyGrid.from_ranges({7: [(0, 1)]})

    def test_sunday_is_day_zero(self):
        # The original's row order starts at 星期日 (p29 §1.2).
        grid = WeeklyGrid.from_ranges({0: [(9, 10)]})
        sunday = datetime(2026, 1, 11, 9, 30)   # a Sunday
        monday = datetime(2026, 1, 5, 9, 30)    # the Monday before
        self.assertTrue(grid.at(sunday))
        self.assertFalse(grid.at(monday))


class QueryTest(unittest.TestCase):
    """Figure 19: Wednesday 14:00-17:00."""

    def setUp(self):
        self.grid = figure19()

    def test_inside_window(self):
        for hour in (14, 15, 16):
            self.assertTrue(self.grid.at(datetime(2026, 1, 7, hour, 30)),
                            msg=f"Wednesday {hour}:30")

    def test_outside_window(self):
        for hour in (0, 13, 17, 23):
            self.assertFalse(self.grid.at(datetime(2026, 1, 7, hour, 30)),
                             msg=f"Wednesday {hour}:30")

    def test_other_days_denied(self):
        # Thursday 15:00 is the same wall-clock time but a different row.
        self.assertFalse(self.grid.at(datetime(2026, 1, 8, 15, 30)))

    def test_left_closed_right_open(self):
        """14:00 belongs to the cell; 17:00 does not.

        Adjacent cells must join without gaps or double-ownership, and
        left-closed/right-open is the only reading of the manual's grid that
        does that.
        """
        self.assertTrue(self.grid.at(WED_1400))
        self.assertTrue(self.grid.at(datetime(2026, 1, 7, 16, 59)))
        self.assertFalse(self.grid.at(WED_1700))

    def test_same_weekday_next_week_also_allowed(self):
        # 2026-01-14 is the following Wednesday.
        self.assertTrue(self.grid.at(datetime(2026, 1, 14, 15, 0)))


class SwitchTest(unittest.TestCase):
    def setUp(self):
        self.grid = figure19()

    def test_enabled_asks_the_grid(self):
        self.assertTrue(self.grid.is_allowed_at(WED_1430, enabled=True))
        self.assertFalse(self.grid.is_allowed_at(WED_1700, enabled=True))

    def test_disabled_allows_everything(self):
        # Slot 24 off means no schedule is in force, so the grid is ignored.
        self.assertTrue(self.grid.is_allowed_at(WED_1700, enabled=False))
        self.assertTrue(self.grid.is_allowed_at(
            datetime(2026, 1, 8, 3, 0), enabled=False))

    def test_disabled_is_not_grid_empty(self):
        """An empty grid with the switch on denies; a full grid with it off
        allows. Both must be expressible or the two states are conflated."""
        empty = WeeklyGrid.empty()
        self.assertFalse(empty.is_allowed_at(WED_1430, enabled=True))
        full = WeeklyGrid.from_ranges({d: [(0, HOURS)] for d in range(DAYS)})
        self.assertTrue(full.is_allowed_at(WED_1430, enabled=False))


class NextChangeTest(unittest.TestCase):
    def setUp(self):
        self.grid = figure19()

    def test_from_before_the_window(self):
        got = self.grid.next_change(datetime(2026, 1, 7, 10, 0))
        self.assertEqual(got, WED_1400)

    def test_from_inside_the_window(self):
        got = self.grid.next_change(datetime(2026, 1, 7, 15, 0))
        self.assertEqual(got, WED_1700)

    def test_from_after_the_window_wraps_to_next_week(self):
        got = self.grid.next_change(datetime(2026, 1, 7, 18, 0))
        self.assertEqual(got, datetime(2026, 1, 14, 14, 0))

    def test_on_the_boundary_reports_the_next_flip(self):
        got = self.grid.next_change(WED_1400)
        # At 14:00 the flag is already True; the next flip is the close.
        self.assertEqual(got, WED_1700)

    def test_uniform_grid_never_changes(self):
        full = WeeklyGrid.from_ranges({d: [(0, HOURS)] for d in range(DAYS)})
        self.assertIsNone(full.next_change(WED_1430))
        self.assertIsNone(WeeklyGrid.empty().next_change(WED_1430))

    def test_returns_aligned_to_the_hour(self):
        got = self.grid.next_change(datetime(2026, 1, 7, 10, 37, 12))
        self.assertEqual((got.minute, got.second), (0, 0))


class WarnTest(unittest.TestCase):
    def setUp(self):
        self.grid = figure19()

    def test_warn_before_constant(self):
        # 切换提前5分钟预警
        self.assertEqual(WARN_BEFORE.total_seconds(), 300)

    def test_warning_time(self):
        got = self.grid.warn_at(datetime(2026, 1, 7, 10, 0))
        self.assertEqual(got, datetime(2026, 1, 7, 13, 55))

    def test_disabled_means_no_warning(self):
        self.assertIsNone(self.grid.warn_at(
            datetime(2026, 1, 7, 10, 0), enabled=False))

    def test_no_transition_means_no_warning(self):
        full = WeeklyGrid.from_ranges({d: [(0, HOURS)] for d in range(DAYS)})
        self.assertIsNone(full.warn_at(WED_1430))


class SerialisationTest(unittest.TestCase):
    def test_blob_is_21_bytes(self):
        # 168 bits exactly.
        self.assertEqual(len(figure19().to_blob()), 21)

    def test_blob_round_trip(self):
        for grid in (figure19(),
                     WeeklyGrid.empty(),
                     WeeklyGrid.from_ranges({d: [(0, HOURS)]
                                             for d in range(DAYS)}),
                     WeeklyGrid.from_ranges({6: [(23, 24)]})):
            self.assertEqual(WeeklyGrid.from_blob(grid.to_blob()).cells,
                             grid.cells)

    def test_from_blob_rejects_wrong_size(self):
        with self.assertRaises(ScheduleError):
            WeeklyGrid.from_blob(b"\x00" * 20)
        with self.assertRaises(ScheduleError):
            WeeklyGrid.from_blob(b"\x00" * 22)

    def test_last_bit_is_addressable(self):
        # Saturday hour 23 is the very last bit; a packing bug would lose it.
        grid = WeeklyGrid.from_ranges({6: [(23, 24)]})
        back = WeeklyGrid.from_blob(grid.to_blob())
        self.assertTrue(back.cells[6][23])
        self.assertEqual(back.count(), 1)

    def test_json_round_trip(self):
        grid = figure19()
        self.assertEqual(WeeklyGrid.from_json(grid.to_json()).cells,
                         grid.cells)

    def test_json_rejects_garbage(self):
        with self.assertRaises(ScheduleError):
            WeeklyGrid.from_json("{not json")
        with self.assertRaises(ScheduleError):
            WeeklyGrid.from_json('{"no_cells": 1}')


class SummaryTest(unittest.TestCase):
    def test_summary_matches_figure19(self):
        got = figure19().summary()
        self.assertEqual(got["allowed_hours"], 3)
        self.assertEqual(got["per_day"]["Wednesday"], 3)
        self.assertEqual(got["per_day"]["Sunday"], 0)

    def test_summary_is_json_friendly(self):
        json.dumps(figure19().summary())

    def test_day_names_start_at_sunday(self):
        from green_dam.schedule import DAY_NAMES
        self.assertEqual(DAY_NAMES[0], "Sunday")
        self.assertEqual(DAY_NAMES[6], "Saturday")


class ErrorTest(unittest.TestCase):
    def test_allowed_validates_arguments(self):
        grid = figure19()
        with self.assertRaises(ScheduleError):
            grid.allowed(7, 0)
        with self.assertRaises(ScheduleError):
            grid.allowed(0, 24)


if __name__ == "__main__":
    unittest.main()
