"""Tests that the documentation's numbers still match reality.

The status checklist and the traceability table went stale once already: nine
completed items were still listed as pending, and the summary said 58 COVERED
when the table had 77. A reader cannot tell a stale number from a current one, so
the numbers get asserted here instead of being maintained by hand.

Scope is deliberately narrow. This file checks arithmetic a machine can verify -
counts in tables, totals against their own rows - not whether the prose is
accurate. Judgement still belongs to a human; this stops the mechanical drift.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TRACEABILITY = ROOT / "docs" / "requirements-traceability.md"
CHECKLIST = ROOT / "docs" / "status-checklist.md"
HANDOFF = ROOT / "docs" / "HANDOFF.md"

#: A requirement row looks like ``| C8 | ... | COVERED |``.
ROW = re.compile(r"^\| ([A-Z]\d+) \|(.*)$", re.M)

#: Everything from here on is the summary and the MISSING grouping, which use a
#: different table shape. Parsing stops before it so the requirement rows and the
#: grouping rows are not mixed - the first version of this file did mix them and
#: reported phantom duplicates.
_SUMMARY_MARKER = "## 汇总"


def _requirement_section(text: str) -> str:
    index = text.find(_SUMMARY_MARKER)
    return text if index < 0 else text[:index]


def _rows(text: str) -> list[tuple[str, str]]:
    return ROW.findall(_requirement_section(text))


def _counts(text: str) -> tuple[int, int, int]:
    covered = partial = missing = 0
    for _rid, rest in _rows(text):
        if "**MISSING**" in rest:
            missing += 1
        elif "COVERED" in rest:
            covered += 1
        elif "PARTIAL" in rest:
            partial += 1
    return covered, partial, missing


class TraceabilityArithmeticTest(unittest.TestCase):
    """The summary table must equal the rows above it."""

    @classmethod
    def setUpClass(cls):
        cls.text = TRACEABILITY.read_text(encoding="utf-8")

    def test_counts_match_rows(self):
        covered, partial, missing = _counts(self.text)
        self.assertGreater(covered, 0, msg="no requirement rows parsed")

        # The summary block states the three totals; find them and compare.
        block = re.search(
            r"\| COVERED \| (\d+) \|\s*\n\| PARTIAL \| (\d+) \|\s*\n"
            r"\| \*\*MISSING\*\* \| \*\*(\d+)\*\* \|", self.text)
        self.assertIsNotNone(block, msg="summary block not found")
        stated = tuple(int(g) for g in block.groups())

        self.assertEqual(
            stated, (covered, partial, missing),
            msg=f"summary says {stated} but the rows give "
                f"({covered}, {partial}, {missing})")

    def test_no_requirement_is_listed_twice(self):
        ids = [rid for rid, _ in _rows(self.text)]
        duplicates = {i for i in ids if ids.count(i) > 1}
        self.assertEqual(duplicates, set(),
                         msg=f"duplicate requirement ids: {duplicates}")

    def test_grouping_is_not_parsed_as_requirements(self):
        """The MISSING grouping uses a three-column table; it must stay out of
        the requirement parse or every id appears twice."""
        whole = ROW.findall(self.text)
        self.assertGreater(len(whole), len(_rows(self.text)),
                           msg="the summary section has no id-bearing rows; "
                               "if that changed, this guard needs updating")

    def test_every_row_has_a_source_and_a_status(self):
        for rid, rest in _rows(self.text):
            fields = [f.strip() for f in rest.split("|")]
            self.assertGreaterEqual(
                len(fields), 4,
                msg=f"{rid} has too few columns: {fields}")
            self.assertTrue(fields[0], msg=f"{rid} has no source")
            status = fields[-2]
            self.assertTrue(
                any(token in status for token in
                    ("COVERED", "PARTIAL", "MISSING", "N/A")),
                msg=f"{rid} has an unrecognised status: {status!r}")


class MissingGroupingTest(unittest.TestCase):
    """Every MISSING id must appear in the grouping section, and vice versa."""

    @classmethod
    def setUpClass(cls):
        cls.text = TRACEABILITY.read_text(encoding="utf-8")

    def test_grouping_lists_exactly_the_missing_ids(self):
        missing = {rid for rid, rest in _rows(self.text)
                   if "**MISSING**" in rest}
        # The grouping section lists ids as ``| D10 | ...`` inside a table whose
        # header is "需要平台 API / 真机". Collect ids mentioned after it.
        index = self.text.rfind("需要平台 API / 真机")
        self.assertGreater(index, 0, msg="grouping section not found")
        tail = self.text[index:]
        # The grouping table merges related ids into one cell: ``| G1 / G3 |``.
        grouped: set[str] = set()
        for cell in re.findall(r"^\| ([^|]+?) \|", tail, re.M):
            grouped |= set(re.findall(r"([A-Z]\d+)", cell))
        self.assertEqual(grouped, missing,
                         msg=f"missing in grouping: {missing - grouped}; "
                             f"extra in grouping: {grouped - missing}")

    def test_no_pure_logic_items_remain(self):
        """When this fails, the engine has work it can do without a host."""
        index = self.text.find("纯逻辑，不需要权限")
        self.assertGreater(index, 0)
        tail = self.text[index:self.text.rfind("需要平台 API / 真机")]
        ids: list[str] = []
        for cell in re.findall(r"^\| ([^|]+?) \|", tail, re.M):
            ids.extend(re.findall(r"([A-Z]\d+)", cell))
        self.assertEqual(
            ids, [],
            msg=f"these need no host and are still MISSING: {ids}. "
                f"Update this test only after doing the work.")


class ChecklistConsistencyTest(unittest.TestCase):
    """Checks the overview table only.

    The checklist holds several tables of different shapes (pending lists, the
    test-layer breakdown, the revision log). Matching every module row anywhere
    picks up prose and reports phantom problems, which is what the first version
    of this file did.
    """

    @classmethod
    def setUpClass(cls):
        text = CHECKLIST.read_text(encoding="utf-8")
        start = text.find("| M1 数据")
        end = text.find("\n\n", start)
        cls.table = text[start:end if end > 0 else len(text)]

    def test_rows_parsed(self):
        rows = re.findall(r"^\| (M\d[^|]*) \|", self.table, re.M)
        self.assertGreaterEqual(len(rows), 7,
                                msg=f"only parsed {rows} from the overview")

    def test_every_row_has_a_status(self):
        for line in self.table.splitlines():
            if not line.startswith("| M"):
                continue
            fields = line.split("|")
            status = fields[3] if len(fields) > 3 else ""
            self.assertTrue(
                any(token in status for token in
                    ("DONE", "PARTIAL", "TODO", "N/A")),
                msg=f"no recognised status in: {line[:60]}")

    def test_module_names_are_unique(self):
        names = re.findall(r"^\| (M\d[^|]*) \|", self.table, re.M)
        duplicates = {n.strip() for n in names if names.count(n) > 1}
        self.assertEqual(duplicates, set(),
                         msg=f"module listed twice: {duplicates}")


class HandoffArithmeticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = HANDOFF.read_text(encoding="utf-8")

    def test_handoff_totals_match_traceability(self):
        trace = TRACEABILITY.read_text(encoding="utf-8")
        covered, partial, missing = _counts(trace)
        block = re.search(
            r"\| COVERED \| (\d+) \|\s*\n\| PARTIAL \| (\d+) \|\s*\n"
            r"\| \*\*MISSING\*\* \| \*\*(\d+)\*\*", self.text)
        self.assertIsNotNone(block, msg="HANDOFF summary block not found")
        self.assertEqual(tuple(int(g) for g in block.groups()),
                         (covered, partial, missing),
                         msg="HANDOFF totals disagree with the traceability "
                             "table")

    def test_handoff_module_status_matches_checklist(self):
        """Every module HANDOFF calls DONE must be DONE in the checklist too."""
        checklist = CHECKLIST.read_text(encoding="utf-8")
        rows = re.findall(r"^\| (M\d[^|]*) \|([^|]*)\|([^|]*)\|", checklist,
                          re.M)
        checklist_status = {m.strip(): s for m, _d, s in rows}

        for match in re.finditer(
                r"^\| \*\*(M\d[^*]*)\*\* \| \*\*(DONE|PARTIAL|TODO)\*\*",
                self.text, re.M):
            module, status = match.group(1).strip(), match.group(2)
            actual = checklist_status.get(module)
            if actual is None:
                continue        # HANDOFF may name a module the checklist omits
            self.assertIn(
                status, actual,
                msg=f"HANDOFF says {module} is {status}, checklist says "
                    f"{actual.strip()}")


class SourceCountsTest(unittest.TestCase):
    """Counts stated in HANDOFF must match the tree."""

    @classmethod
    def setUpClass(cls):
        cls.text = HANDOFF.read_text(encoding="utf-8")

    def test_module_count(self):
        actual = len(list((ROOT / "src" / "green_dam").glob("*.py")))
        stated = re.search(r"(\d+) 个模块，([\d,]+) 行", self.text)
        self.assertIsNotNone(stated, msg="module count not stated in HANDOFF")
        self.assertEqual(int(stated.group(1)), actual,
                         msg=f"HANDOFF says {stated.group(1)} modules, "
                             f"tree has {actual}")

    def test_source_line_count_is_close(self):
        """Exact line counts churn with every edit, so allow a small drift."""
        actual = sum(len(p.read_text(encoding="utf-8").splitlines())
                     for p in (ROOT / "src" / "green_dam").glob("*.py"))
        stated = re.search(r"\d+ 个模块，([\d,]+) 行", self.text)
        self.assertIsNotNone(stated)
        claimed = int(stated.group(1).replace(",", ""))
        self.assertLess(abs(claimed - actual), 200,
                        msg=f"HANDOFF says {claimed} lines, tree has {actual}")

    def test_test_file_count(self):
        actual = len(list((ROOT / "tests").glob("test_*.py")))
        stated = re.search(r"tests/\s+(\d+) 个文件", self.text)
        self.assertIsNotNone(stated, msg="test file count not stated")
        self.assertEqual(int(stated.group(1)), actual,
                         msg=f"HANDOFF says {stated.group(1)} test files, "
                             f"tree has {actual}")


class ProbeClaimTest(unittest.TestCase):
    """Every probe referenced in the reading-order table must exist."""

    def test_referenced_probes_exist(self):
        readme = ROOT / "analysis" / "README.md"
        if not readme.exists():
            self.skipTest("analysis/README.md not present")
        text = readme.read_text(encoding="utf-8")
        referenced = set(re.findall(r"\bp(\d{2})\b", text))
        probe_dir = ROOT / "analysis" / "probe"
        available = {p.name[1:3] for p in probe_dir.glob("p*.txt")}
        available |= {p.name[1:3] for p in probe_dir.glob("p*.py")}
        missing = {f"p{n}" for n in referenced} - {f"p{n}" for n in available}
        self.assertEqual(missing, set(),
                         msg=f"analysis/README.md cites probes that do not "
                             f"exist: {sorted(missing)}")


if __name__ == "__main__":
    unittest.main()
