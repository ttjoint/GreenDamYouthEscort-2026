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


if __name__ == "__main__":
    unittest.main()
