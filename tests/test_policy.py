"""Behaviour of the advisory policy layer.

No test in this file asserts or prints the text of any word or rule.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam.policy import (  # noqa: E402
    Finding,
    PolicyEngine,
    PolicyTable,
    Verdict,
    assess,
    default_policy_table,
    load_trust_domains,
)
from green_dam.wordlists import WordlistIndex  # noqa: E402


def _engine(levels=None, cats=None, trust=frozenset(), attested=False):
    index = WordlistIndex()
    for word, source, level in levels or []:
        index.add(word, source, level=level)
    table = PolicyTable(
        level_actions=cats or {},
        category_actions=cats or {},
        trust_domains=trust,
        attested=attested,
    )
    return PolicyEngine(words=index, policies=table)


class UnmappedEvidenceTest(unittest.TestCase):
    def test_no_level_means_no_action(self):
        eng = _engine(levels=[("甲词", "s", None)])
        v = eng.verdict("这是甲词")
        self.assertEqual(len(v.findings), 1)
        self.assertIsNone(v.findings[0].action)
        self.assertEqual(v.findings[0].kind, "library_only")
        self.assertIsNone(v.action)
        self.assertEqual(v.reason, "all_unmapped")

    def test_level_without_mapping_means_no_action(self):
        eng = _engine(levels=[("甲词", "s", 4)])
        v = eng.verdict("这是甲词")
        self.assertEqual(v.findings[0].kind, "leveled")
        self.assertEqual(v.findings[0].reason, "level_unmapped")
        self.assertIsNone(v.action)

    def test_empty_text_has_no_evidence(self):
        v = _engine().verdict("")
        self.assertEqual(v.findings, ())
        self.assertEqual(v.reason, "no_evidence")

    def test_unproven_policy_is_not_attested(self):
        eng = _engine(levels=[("甲词", "s", 4)], cats={4: "block"})
        v = eng.verdict("这是甲词")
        self.assertEqual(v.action, "block")
        self.assertFalse(v.attested)


class MappingTest(unittest.TestCase):
    def test_level_mapping_produces_action(self):
        eng = _engine(levels=[("甲词", "s", 3)], cats={3: "warn"})
        v = eng.verdict("这是甲词")
        self.assertEqual(v.findings[0].action, "warn")
        self.assertEqual(v.reason, "strongest_finding")

    def test_aggregation_takes_strongest(self):
        eng = _engine(
            levels=[("甲词", "s", 1), ("乙词", "s", 4)],
            cats={1: "log", 4: "block"},
        )
        v = eng.verdict("甲词与乙词")
        self.assertEqual(len(v.findings), 2)
        self.assertEqual(v.action, "block")

    def test_all_levels_of_one_hit_are_considered(self):
        index = WordlistIndex()
        index.add("甲词", "a", level=1)
        index.add("甲词", "b", level=4)
        eng = PolicyEngine(words=index,
                           policies=PolicyTable(level_actions={1: "log", 4: "block"}))
        v = eng.verdict("甲词")
        self.assertEqual(v.findings[0].levels, (1, 4))
        self.assertEqual(v.action, "block")


class TrustTest(unittest.TestCase):
    def test_trust_domain_overrides_blocking(self):
        eng = _engine(
            levels=[("甲词", "s", 4)],
            cats={4: "block"},
            trust=frozenset({"trusted.example"}),
        )
        v = eng.verdict("https://trusted.example/甲词")
        self.assertEqual(v.findings[0].action, "block")
        self.assertEqual(v.action, "allow")
        self.assertEqual(v.reason, "trusted_domain")

    def test_trust_ignores_case_and_scheme(self):
        eng = _engine(trust=frozenset({"trusted.example"}))
        self.assertTrue(eng.policies.in_trust("HTTP://Trusted.Example/x"))


class TrustLoaderTest(unittest.TestCase):
    def test_loader_on_real_import(self):
        path = ROOT / "data" / "legacy-imported" / "trust-urls.json"
        if not path.exists():
            self.skipTest("legacy import not present")
        domains = load_trust_domains(path)
        self.assertGreater(len(domains), 1000)
        self.assertTrue(all(d == d.lower() for d in domains))
        self.assertTrue(all("://" not in d for d in domains))

    def test_default_table_is_inert(self):
        table = default_policy_table(ROOT / "data" / "legacy-imported")
        self.assertEqual(table.level_actions, {})
        self.assertEqual(table.category_actions, {})
        self.assertFalse(table.attested)
        self.assertGreater(len(table.trust_domains), 0)


class FindingShapeTest(unittest.TestCase):
    def test_matched_returns_text_of_span(self):
        f = Finding(span=(2, 4), surface="abcdef", kind="rule",
                    sources=("x",))
        self.assertEqual(f.matched, "cd")

    def test_sources_always_recorded(self):
        eng = _engine(levels=[("甲词", "src-a", 2)])
        v = eng.verdict("甲词")
        self.assertEqual(v.findings[0].sources, ("src-a",))


class LegacySysdatGateTest(unittest.TestCase):
    """The decoded sys.dat array gates verdicts (the only attested behaviour).

    Slot numbers are policy numbers (1-based), not array indices.
    """

    def _engine_with(self, values):
        from green_dam import sysdat
        index = WordlistIndex()
        index.add("甲词", "src-a", level=2)
        legacy = sysdat.PolicyArray(values=tuple(values))
        table = PolicyTable(
            level_actions={2: "block"},
            legacy=legacy,
            attested=True,
        )
        return PolicyEngine(words=index, policies=table)

    def _slots(self, **overrides):
        from green_dam import sysdat
        values = [0] * sysdat.SLOT_COUNT
        for key, value in overrides.items():
            values[sysdat.slot_index(int(key.lstrip("s")))] = value
        return values

    def test_baseline_blocks(self):
        eng = self._engine_with(self._slots())
        self.assertEqual(eng.verdict("甲词").action, "block")

    def test_slot2_disables_filtering_entirely(self):
        eng = self._engine_with(self._slots(s2=1))
        v = eng.verdict("甲词")
        self.assertIsNone(v.action)
        self.assertEqual(v.reason, "legacy_filtering_disabled")

    def test_slot10_disables_keyword_filtering(self):
        eng = self._engine_with(self._slots(s10=1))
        v = eng.verdict("甲词")
        self.assertIsNone(v.action)
        self.assertEqual(v.reason, "legacy_keyword_filter_disabled")

    def test_gate_does_not_invent_an_allow(self):
        # An off-switch silences evidence; it must never manufacture "allow".
        eng = self._engine_with(self._slots(s2=1))
        self.assertNotEqual(eng.verdict("甲词").action, "allow")

    def test_default_table_loads_shipped_sysdat(self):
        legacy_dir = ROOT.parent / "LH-installed-tree" / "Windows" / "System32"
        if not (legacy_dir / "sys.dat").exists():
            self.skipTest("legacy tree not present")
        table = default_policy_table(legacy_dir)
        self.assertIsNotNone(table.legacy)
        self.assertTrue(table.legacy.attested)
        self.assertFalse(table.legacy.filtering_disabled)
        self.assertFalse(table.legacy.keyword_filter_disabled)
        self.assertEqual(table.legacy.image_filter_level, 2)


if __name__ == "__main__":
    unittest.main()


class AssessTest(unittest.TestCase):
    """Network decisions and text verdicts combine without losing provenance."""

    def _verdict(self, action, reason="r"):
        return Verdict(findings=(), action=action, reason=reason)

    def test_network_block_wins(self):
        from green_dam.intercept import Decision, Outcome, Stage
        net = Decision(Outcome.BLOCK, Stage.HOST, "host_listed")
        got = assess(network=net, text=self._verdict("warn"))
        self.assertEqual(got.action, "block")
        self.assertEqual(got.source, "network")
        self.assertEqual(got.reason, "host_listed")
        self.assertTrue(got.blocked)

    def test_text_action_used_when_network_allows(self):
        from green_dam.intercept import Decision, Outcome
        net = Decision(Outcome.ALLOW, None, "no_stage_matched")
        got = assess(network=net, text=self._verdict("block", "keyword"))
        self.assertEqual(got.action, "block")
        self.assertEqual(got.source, "text")

    def test_no_evidence_means_no_action(self):
        got = assess()
        self.assertIsNone(got.action)
        self.assertIsNone(got.source)
        self.assertFalse(got.blocked)

    def test_trusted_allow_does_not_override_network_block(self):
        # 'allow' is a statement about one evidence source, not a licence.
        from green_dam.intercept import Decision, Outcome, Stage
        net = Decision(Outcome.BLOCK, Stage.PROCESS, "watched_process")
        got = assess(network=net, text=self._verdict("allow", "trusted_domain"))
        self.assertEqual(got.action, "block")
        self.assertEqual(got.source, "network")

    def test_provenance_kept_on_both_sides(self):
        from green_dam.intercept import Decision, Outcome, Stage
        net = Decision(Outcome.BLOCK, Stage.HOST, "host_listed")
        text = self._verdict("warn")
        got = assess(network=net, text=text)
        self.assertIs(got.network, net)
        self.assertIs(got.text, text)

    def test_text_only(self):
        got = assess(text=self._verdict("log"))
        self.assertEqual(got.action, "log")
        self.assertEqual(got.source, "text")
        self.assertIsNone(got.network)

    def test_network_allow_only_is_not_an_action(self):
        from green_dam.intercept import Decision, Outcome
        net = Decision(Outcome.ALLOW, None, "no_stage_matched")
        got = assess(network=net)
        self.assertIsNone(got.action)
        self.assertIsNotNone(got.network)


class ImageAssessmentTest(unittest.TestCase):
    """Image evidence combines with the others without inventing an action."""

    def _screen(self, suspect=True, detail="high_tone"):
        from green_dam.imaging import Check, ScreenResult
        return ScreenResult(
            suspect=suspect,
            fired=(Check.HIGH_TONE,) if suspect else (),
            checks_run=(Check.ASPECT, Check.HIGH_TONE),
            detail=detail,
        )

    def test_image_alone_has_no_action(self):
        # A measurement is not a policy: no image_action means no action.
        got = assess(image=self._screen())
        self.assertIsNone(got.action)
        self.assertIsNotNone(got.image)

    def test_image_action_applied(self):
        got = assess(image=self._screen(), image_action="block")
        self.assertEqual(got.action, "block")
        self.assertEqual(got.source, "image")
        self.assertEqual(got.reason, "high_tone")

    def test_network_block_beats_image(self):
        from green_dam.intercept import Decision, Outcome, Stage
        net = Decision(Outcome.BLOCK, Stage.HOST, "host_listed")
        got = assess(network=net, image=self._screen(), image_action="block")
        self.assertEqual(got.source, "network")

    def test_stronger_of_text_and_image_wins(self):
        text = Verdict(findings=(), action="log", reason="keyword")
        got = assess(text=text, image=self._screen(), image_action="block")
        self.assertEqual(got.source, "image")
        self.assertEqual(got.action, "block")

    def test_weaker_image_does_not_beat_text(self):
        text = Verdict(findings=(), action="block", reason="keyword")
        got = assess(text=text, image=self._screen(), image_action="log")
        self.assertEqual(got.source, "text")

    def test_tie_keeps_text(self):
        text = Verdict(findings=(), action="warn", reason="keyword")
        got = assess(text=text, image=self._screen(), image_action="warn")
        self.assertEqual(got.source, "text")

    def test_image_provenance_kept(self):
        screen_result = self._screen()
        got = assess(image=screen_result, image_action="block")
        self.assertIs(got.image, screen_result)
