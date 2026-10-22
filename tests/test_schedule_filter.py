"""Tests for the schedule wired into Filter.check_request.

The load-bearing properties:

* the schedule short-circuits the what stages - a refusal outside allowed hours
  happens before the network chain runs
* switch off means no schedule, which must be distinguishable from an empty grid
* no clock is read anywhere; every test injects its time

Fixtures reproduce the manual's figure 19 (Wednesday 14:00-17:00).
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam.eventlog import EventLog  # noqa: E402
from green_dam.filter import Filter  # noqa: E402
from green_dam.intercept import Request  # noqa: E402
from green_dam.schedule import WeeklyGrid  # noqa: E402
from green_dam.sysdat import PolicyArray  # noqa: E402


def policy(slot24: int) -> PolicyArray:
    values = [0] * 67
    values[23] = slot24
    return PolicyArray(values=tuple(values))


def request() -> Request:
    return Request(direction="outbound", host="example.com", port=80)


WED_1430 = datetime(2026, 1, 7, 14, 30)
WED_1700 = datetime(2026, 1, 7, 17, 0)


class ScheduleWiringTest(unittest.TestCase):
    def _filter(self, slot24: int, log: EventLog | None = None) -> Filter:
        f = Filter(legacy=policy(slot24), schedule=figure19_grid(),
                   log=log)
        return f

    def test_inside_hours_proceeds_to_decision(self):
        got = self._filter(1).check_request(request(), when=WED_1430)
        # example.com is not on any list, so the network chain allows it.
        self.assertEqual(got.network.outcome.value, "allow")

    def test_outside_hours_is_refused_before_the_chain(self):
        log = EventLog()
        got = self._filter(1, log).check_request(request(), when=WED_1700)
        self.assertIsNone(got.action)
        self.assertEqual(got.source, "schedule")
        self.assertEqual(got.reason, "outside_allowed_hours")
        # The network chain never ran, so there is exactly one record and it
        # is the gate, not a decision.
        self.assertEqual(len(log.records), 1)
        self.assertEqual(log.records[0].event.value, "gated")
        self.assertEqual(log.records[0].source, "schedule")

    def test_switch_off_disables_the_schedule(self):
        got = self._filter(0).check_request(request(), when=WED_1700)
        self.assertEqual(got.network.outcome.value, "allow")

    def test_no_time_supplied_skips_the_schedule(self):
        # A caller that cannot supply a clock must not inherit the wall clock.
        got = self._filter(1).check_request(request())
        self.assertEqual(got.network.outcome.value, "allow")

    def test_no_schedule_configured_skips_the_check(self):
        f = Filter(legacy=policy(1))
        got = f.check_request(request(), when=WED_1700)
        self.assertEqual(got.network.outcome.value, "allow")

    def test_refusal_records_one_log_line(self):
        """Single-record invariant: gated + network must not double-log."""
        log = EventLog()
        self._filter(1, log).check_request(request(), when=WED_1700)
        self.assertEqual(len(log.records), 1)


class SwitchSemanticsTest(unittest.TestCase):
    """Switch off and empty grid must stay distinguishable."""

    def _assess(self, slot24: int, grid: WeeklyGrid, when: datetime):
        return Filter(legacy=policy(slot24), schedule=grid) \
            .check_request(request(), when=when)

    def test_empty_grid_switch_on_denies(self):
        got = self._assess(1, WeeklyGrid.empty(), WED_1430)
        self.assertIsNone(got.action)
        self.assertEqual(got.source, "schedule")

    def test_full_grid_switch_off_allows(self):
        full = WeeklyGrid.from_ranges({d: [(0, 24)] for d in range(7)})
        got = self._assess(0, full, WED_1700)
        self.assertEqual(got.network.outcome.value, "allow")

    def test_empty_grid_switch_off_allows(self):
        got = self._assess(0, WeeklyGrid.empty(), WED_1430)
        self.assertEqual(got.network.outcome.value, "allow")

    def test_refusal_reason_carries_no_content(self):
        log = EventLog()
        got = self._assess(1, figure19_grid(), WED_1700)
        blob = json.dumps({"action": got.action, "reason": got.reason,
                           "source": got.source})
        self.assertNotIn("example.com", blob)


def figure19_grid() -> WeeklyGrid:
    return WeeklyGrid.from_ranges({3: [(14, 17)]})


if __name__ == "__main__":
    unittest.main()
