"""Tests for the gate: the filter's on/off switches.

A gate answers "may this path run" and nothing else. The load-bearing property is
that a closed gate never produces ``allow`` - it produces a reason. That
distinction is the core behavioural correction over the original product.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam import sysdat  # noqa: E402
from green_dam.gate import (  # noqa: E402
    DISABLED_ALL,
    DISABLED_IMAGE,
    DISABLED_KEYWORD,
    OPEN,
    Gate,
    from_policy,
)


def _policy(**slots) -> sysdat.PolicyArray:
    values = [0] * sysdat.SLOT_COUNT
    for key, value in slots.items():
        values[sysdat.slot_index(int(key.lstrip("s")))] = value
    return sysdat.PolicyArray(values=tuple(values))


class NoPolicyTest(unittest.TestCase):
    def test_open_gate_allows_everything(self):
        self.assertIsNone(OPEN.reason())
        self.assertIsNone(OPEN.reason(keyword=True))

    def test_no_policy_means_images_off(self):
        # "Not configured" must not read as "enabled".
        self.assertFalse(OPEN.image_filter_enabled)
        self.assertEqual(OPEN.reason(image=True), DISABLED_IMAGE)

    def test_no_policy_does_not_disable_filtering(self):
        self.assertFalse(OPEN.filtering_disabled)
        self.assertFalse(OPEN.keyword_filter_disabled)


class SlotTwoTest(unittest.TestCase):
    def test_gates_every_path(self):
        gate = Gate(legacy=_policy(s2=1))
        self.assertEqual(gate.reason(), DISABLED_ALL)
        self.assertEqual(gate.reason(keyword=True), DISABLED_ALL)
        self.assertEqual(gate.reason(image=True), DISABLED_ALL)

    def test_takes_precedence_over_other_slots(self):
        # Slot 2 wins when several apply - first hit wins, as in the chain.
        gate = Gate(legacy=_policy(s2=1, s10=1))
        self.assertEqual(gate.reason(keyword=True), DISABLED_ALL)


class SlotTenTest(unittest.TestCase):
    def test_gates_text_only(self):
        gate = Gate(legacy=_policy(s10=1))
        self.assertEqual(gate.reason(keyword=True), DISABLED_KEYWORD)
        self.assertIsNone(gate.reason())

    def test_network_still_allowed(self):
        gate = Gate(legacy=_policy(s10=1))
        self.assertTrue(gate.allows())


class SlotThirteenTest(unittest.TestCase):
    def test_needs_both_enable_and_level(self):
        # Slot 13 enables, slot 5 must also be non-zero.
        self.assertFalse(Gate(legacy=_policy(s13=1)).image_filter_enabled)
        self.assertFalse(Gate(legacy=_policy(s5=2)).image_filter_enabled)
        self.assertTrue(Gate(legacy=_policy(s13=1, s5=2)).image_filter_enabled)

    def test_disabled_image_reports_reason(self):
        gate = Gate(legacy=_policy(s13=0, s5=2))
        self.assertEqual(gate.reason(image=True), DISABLED_IMAGE)

    def test_enabled_image_has_no_reason(self):
        gate = Gate(legacy=_policy(s13=1, s5=2))
        self.assertIsNone(gate.reason(image=True))


class AllowsTest(unittest.TestCase):
    def test_allows_is_the_converse_of_reason(self):
        for legacy in (None, _policy(), _policy(s2=1), _policy(s10=1),
                       _policy(s13=1, s5=2)):
            gate = Gate(legacy=legacy)
            for kwargs in ({}, {"keyword": True}, {"image": True}):
                self.assertEqual(gate.allows(**kwargs),
                                 gate.reason(**kwargs) is None)

    def test_reason_tokens_are_stable(self):
        # These strings appear in logs; changing them is a data change.
        self.assertEqual(DISABLED_ALL, "legacy_filtering_disabled")
        self.assertEqual(DISABLED_KEYWORD, "legacy_keyword_filter_disabled")
        self.assertEqual(DISABLED_IMAGE, "image_filter_disabled")


class SummaryTest(unittest.TestCase):
    def test_summary_has_only_switches(self):
        got = Gate(legacy=_policy(s2=1)).summary()
        self.assertEqual(set(got), {"filtering_disabled",
                                    "keyword_filter_disabled",
                                    "image_filter_enabled"})

    def test_summary_is_json_friendly(self):
        import json
        json.dumps(Gate(legacy=_policy(s13=1, s5=2)).summary())


class FactoryTest(unittest.TestCase):
    def test_from_policy(self):
        self.assertTrue(from_policy(_policy(s2=1)).filtering_disabled)

    def test_from_none_is_open(self):
        self.assertFalse(from_policy(None).filtering_disabled)


if __name__ == "__main__":
    unittest.main()
