import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import audit_legacy_rules


class LegacyRuleAuditTest(unittest.TestCase):
    def test_audit_has_expected_shapes(self):
        report = audit_legacy_rules.audit(
            ROOT / "data" / "legacy-imported" / "rules" / "adwfil.json")
        self.assertEqual(report["rules"], 2003)
        self.assertEqual(report["expanded_terms"], 3618)
        self.assertEqual(report["raw_rules"], 0)
        self.assertGreater(report["unique_terms"], 3000)

    def test_raw_rules_are_reported_not_hidden(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.json"
            p.write_text(json.dumps({"source": "x", "count": 1,
                                     "rules": [{"raw": "[broken"}]},
                                    ensure_ascii=False), encoding="utf-8")
            report = audit_legacy_rules.audit(p)
        self.assertEqual(report["raw_rules"], 1)
        self.assertEqual(report["raw_examples"], ["[broken"])


if __name__ == "__main__":
    unittest.main()
