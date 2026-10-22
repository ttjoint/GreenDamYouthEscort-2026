"""Cross-layer invariants: properties that must hold for every input.

Where ``test_contracts.py`` checks the shape of the code, this file checks the
behaviour of the *system*: statements that must be true no matter which path,
which configuration or which combination of evidence is used.

Each invariant is exercised over a matrix of inputs rather than one example,
because a property that holds for one case and fails for another is not an
invariant. Requirement IDs match ``docs/requirements-traceability.md``.

All fixtures are invented (甲乙丙丁, example hosts, proc-a).
"""
from __future__ import annotations

import itertools
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam import config, sysdat  # noqa: E402
from green_dam.eventlog import Event, EventLog  # noqa: E402
from green_dam.filter import Filter  # noqa: E402
from green_dam.imaging import Check, ScreenResult  # noqa: E402
from green_dam.intercept import Request  # noqa: E402
from green_dam.namelists import CAPACITY, NameLists  # noqa: E402
from green_dam.wordlists import WordlistIndex  # noqa: E402

#: Strings that must never appear in a log record. Each is a stand-in for a kind
#: of user content: a host, a process name, a wordlist term, a rule, a body.
SECRETS = (
    "secret.example",
    "proc-secret",
    "甲词",
    "乙词",
    "{甲}[乙]",
    "private-body-marker",
    "身份证号占位",
)


def _policy(**slots) -> sysdat.PolicyArray:
    values = [0] * sysdat.SLOT_COUNT
    for key, value in slots.items():
        values[sysdat.slot_index(int(key.lstrip("s")))] = value
    return sysdat.PolicyArray(values=tuple(values))


def _words(*entries) -> WordlistIndex:
    index = WordlistIndex()
    for word, level in entries:
        index.add(word, "test", level=level)
    return index


def _suspect_image() -> ScreenResult:
    return ScreenResult(suspect=True, fired=(Check.HIGH_TONE,),
                        checks_run=(Check.ASPECT, Check.HIGH_TONE),
                        detail="high_tone")


def _loaded_filter(log=None, **policy_slots) -> Filter:
    """A filter with every path armed, so each invariant gets a chance to fire."""
    lists = NameLists()
    lists["A"].add("secret.example")
    lists["B"].add("proc-secret")
    lists["C"].add("{甲}[乙]")
    lists["D"].add("secret.example:80")
    legacy = _policy(**policy_slots) if policy_slots else None
    return Filter(lists=lists, words=_words(("甲词", 2), ("乙词", 3)),
                  log=log, legacy=legacy)


#: Every combination of the four paths, used to prove an invariant holds for
#: all of them rather than for a hand-picked case.
PATH_COMBOS = [
    {},
    {"request": Request("outbound", host="secret.example", port=80)},
    {"request": Request("inbound", port=80, body="甲词 private-body-marker")},
    {"text": "甲词 private-body-marker"},
    {"process": "proc-secret"},
    {"image": _suspect_image(), "image_action": "block"},
    {"request": Request("outbound", host="secret.example", port=80),
     "text": "甲词 private-body-marker"},
    {"request": Request("outbound", host="secret.example", port=80),
     "text": "甲词 private-body-marker", "process": "proc-secret",
     "image": _suspect_image(), "image_action": "block"},
]


class RedactionTest(unittest.TestCase):
    """L3 / B13: no path may write user content into the log."""

    def test_no_secret_in_any_record(self):
        for combo in PATH_COMBOS:
            log = EventLog()
            f = _loaded_filter(log=log, s13=1, s5=2)
            f.check(**combo)
            blob = "".join(r.to_json() for r in log.records)
            for secret in SECRETS:
                self.assertNotIn(
                    secret, blob,
                    msg=f"leaked {secret!r} with paths {sorted(combo)}")

    def test_digest_is_one_way(self):
        """When a digest is present it must not be reversible by inspection."""
        from green_dam.eventlog import digest
        for secret in SECRETS:
            value = digest(secret)
            self.assertNotIn(secret, value)
            self.assertEqual(len(value), 16)

    def test_status_contains_no_entries(self):
        f = _loaded_filter()
        blob = repr(f.status())
        for secret in SECRETS:
            self.assertNotIn(secret, blob)


class GateSemanticsTest(unittest.TestCase):
    """L4 / L5: a gate silences evidence; it never manufactures an allow."""

    def test_gate_never_produces_allow(self):
        for combo in PATH_COMBOS:
            log = EventLog()
            f = _loaded_filter(log=log, s2=1)
            got = f.check(**combo)
            self.assertNotEqual(got.action, "allow",
                                msg=f"gate produced allow for {sorted(combo)}")
            self.assertIsNone(got.action)

    def test_gate_reason_names_a_switch(self):
        for combo in PATH_COMBOS:
            f = _loaded_filter(s2=1)
            got = f.check(**combo)
            if got.action is None and got.source == "policy":
                self.assertIn("disabled", got.reason)

    def test_not_looking_is_never_reported_as_clean(self):
        """The core correction over the original product.

        A gated or unconfigured run must never produce a reason that reads as
        "nothing found". Those two states have distinct reasons, and neither is
        the clean-run reason.
        """
        clean_reasons = {"no_stage_matched", "no_evidence"}
        gated = _loaded_filter(s2=1).check(text="甲词")
        self.assertNotIn(gated.reason, clean_reasons)
        self.assertIn("disabled", gated.reason)

    def test_gate_is_logged_as_gated_not_allow(self):
        for combo in PATH_COMBOS:
            if not combo:
                # Nothing was asked, so nothing can be gated. An empty call is
                # not a filtered call.
                continue
            log = EventLog()
            f = _loaded_filter(log=log, s2=1)
            f.check(**combo)
            events = [r.event for r in log.records]
            self.assertIn(Event.GATED, events,
                          msg=f"no gated event for {sorted(combo)}")
            self.assertNotIn(Event.ALLOW, events)

    def test_slot10_does_not_silence_network(self):
        f = _loaded_filter(s10=1)
        got = f.check(request=Request("outbound", host="secret.example",
                                      port=80))
        self.assertTrue(got.blocked)

    def test_image_gate_only_affects_image(self):
        f = _loaded_filter()      # image off by default
        got = f.check(text="甲词", image=_suspect_image(),
                      image_action="block")
        # Image action suppressed, but text evidence still present.
        self.assertIsNotNone(got.text)
        self.assertNotEqual(got.source, "image")


class SingleRecordInvariantTest(unittest.TestCase):
    """L2: exactly one log record per check(), for every path combination."""

    def test_one_record_per_combo(self):
        for combo in PATH_COMBOS:
            log = EventLog()
            f = _loaded_filter(log=log, s13=1, s5=2)
            f.check(**combo)
            self.assertEqual(len(log), 1, msg=f"combo {sorted(combo)}")

    def test_one_record_even_when_gated(self):
        for combo in PATH_COMBOS:
            log = EventLog()
            f = _loaded_filter(log=log, s2=1)
            f.check(**combo)
            self.assertEqual(len(log), 1, msg=f"gated combo {sorted(combo)}")


class PathConsistencyInvariantTest(unittest.TestCase):
    """L1: the combined entry point never contradicts a single-path call."""

    def test_text_agrees_for_every_wordlist(self):
        for word, level in (("甲词", 2), ("乙词", 3), ("丙词", 0)):
            f = Filter(words=_words((word, level)))
            combined = f.check(text=word).text
            single = f.check_text(word)
            self.assertEqual(len(combined.findings), len(single.findings),
                             msg=word)
            self.assertEqual(combined.action, single.action, msg=word)

    def test_network_agrees_for_every_host(self):
        for host in ("secret.example", "ok.example", ""):
            f = _loaded_filter()
            req = Request("outbound", host=host, port=80)
            self.assertEqual(f.check(request=req).action,
                             f.check_request(req).action, msg=host)

    def test_process_agrees_for_every_name(self):
        for name in ("proc-secret", "PROC-SECRET", "other"):
            f = _loaded_filter()
            single = f.check_process(name).blocked
            combined = f.check(process=name).blocked
            self.assertEqual(single, combined, msg=name)


class CapacityInvariantTest(unittest.TestCase):
    """L6: capacities match the disassembly, and cannot be exceeded."""

    def test_constants_match_evidence(self):
        self.assertEqual(CAPACITY["A"], 100)     # 0x64  @ 0x2d34
        self.assertEqual(CAPACITY["C"], 1000)    # 0x3e8 @ 0x1acd
        self.assertEqual(CAPACITY["D"], 1500)    # 0x5dc @ 0x27b5
        self.assertIsNone(CAPACITY["B"])         # no immediate compare

    def test_every_bounded_list_refuses_overflow(self):
        from green_dam.namelists import ListFull, NameList
        for name, capacity in CAPACITY.items():
            if capacity is None:
                continue
            lst = NameList(name=name)
            for i in range(capacity):
                lst.add(f"x{i}")
            with self.assertRaises(ListFull, msg=name):
                lst.add("overflow")

    def test_record_size_is_144_everywhere(self):
        from green_dam.namelist import RECORD_SIZE
        self.assertEqual(RECORD_SIZE, 144)
        self.assertEqual(RECORD_SIZE * 11000, 0x182b80)  # the legacy buffer


class ShippedConfigInvariantTest(unittest.TestCase):
    """L7 / I2: the shipped defaults equal the bytes on the CD."""

    SYSDIR = ROOT.parent / "LH-installed-tree" / "Windows" / "System32"

    def test_defaults_equal_sysdat(self):
        path = self.SYSDIR / "sys.dat"
        if not path.exists():
            self.skipTest("legacy tree not present")
        self.assertEqual(config.default_for_fresh_install().values,
                         sysdat.load(path).values)

    def test_saved_blob_is_byte_identical(self):
        import tempfile
        path = self.SYSDIR / "sys.dat"
        if not path.exists():
            self.skipTest("legacy tree not present")
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "x.dat"
            config.save(config.default_for_fresh_install(), out)
            self.assertEqual(out.read_bytes(), path.read_bytes())


class NoActionInvariantTest(unittest.TestCase):
    """E5 / J5: evidence never invents an action the vendor did not specify."""

    def test_wordlist_hit_alone_has_no_action(self):
        f = Filter(words=_words(("甲词", 7)))
        got = f.check_text("甲词")
        self.assertTrue(got.findings)
        self.assertIsNone(got.action)

    def test_hnc_hit_alone_has_no_action(self):
        from green_dam import semantic
        matcher = semantic.load_default()
        term = next(t for t, v in matcher._levels.items() if max(v) >= 7)
        got = Filter(hnc=matcher).check_text(term)
        self.assertTrue(got.findings)
        self.assertIsNone(got.action)

    def test_suspect_image_alone_has_no_action(self):
        got = Filter().check(image=_suspect_image())
        self.assertIsNone(got.action)

    def test_only_attested_switches_change_behaviour(self):
        # Mapping a level to an action is opt-in, never default.
        f = Filter(words=_words(("甲词", 7)))
        self.assertEqual(f.policies.level_actions, {})
        f.policies.level_actions[7] = "block"
        self.assertEqual(f.check_text("甲词").action, "block")


if __name__ == "__main__":
    unittest.main()
