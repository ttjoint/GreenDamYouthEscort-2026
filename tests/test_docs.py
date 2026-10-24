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
        """The grouping table must account for every MISSING id, and no others.

        The table stops at the next ``###`` heading. Without that bound, the
        N/A section that follows would be counted as part of the grouping, and
        ids that are explicitly *not* being built would look like pending work -
        which is the opposite of what the section is for.
        """
        missing = {rid for rid, rest in _rows(self.text)
                   if "**MISSING**" in rest}
        index = self.text.rfind("需要平台 API / 真机")
        self.assertGreater(index, 0, msg="grouping section not found")
        tail = self.text[index:]
        end = tail.find(chr(10) + "### ", 1)
        if end > 0:
            tail = tail[:end]

        # The grouping table merges related ids into one cell: ``| G1 / G3 |``.
        grouped: set[str] = set()
        for cell in re.findall(r"^\| ([^|]+?) \|", tail, re.M):
            grouped |= set(re.findall(r"([A-Z]\d+)", cell))
        self.assertEqual(grouped, missing,
                         msg=f"missing in grouping: {missing - grouped}; "
                             f"extra in grouping: {grouped - missing}")

    def test_declined_items_are_not_counted_as_missing(self):
        """A decision not to build something must not read as pending work.

        ``D14``/``G1``/``G3`` are N/A: a replacement exists and the reason is
        recorded. If one of them reappeared as MISSING, the summary would
        overstate what is left and the reason would be lost.
        """
        for rid in ("D14", "G1", "G3"):
            row = next((rest for row_id, rest in _rows(self.text)
                        if row_id == rid), None)
            self.assertIsNotNone(row, msg=f"{rid} is not in the table at all")
            self.assertNotIn("**MISSING**", row,
                             msg=f"{rid} is marked MISSING but a decision was "
                                 f"taken not to build it")
            self.assertIn("N/A", row,
                          msg=f"{rid} should be marked N/A with a reason")

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


class TestCountClaimTest(unittest.TestCase):
    """The "N passed" figure quoted in the docs must match the tree.

    This is the one number that drifted silently three times (1,105 / 1,247 in
    two files, while the suite was actually at 1,265). Counting test *methods*
    is not the same as counting pytest's ``passed`` - skips and subtests differ -
    so the number asserted here is the method count, which is the figure the
    docs actually claim. It is checked by collection, not by running, so the
    assertion costs about a second.
    """

    #: Files whose quoted totals must all agree with each other and the tree.
    DOCUMENTS = (HANDOFF, CHECKLIST)

    #: Both suites count. The host is part of the product, so a total that
    #: omits it understates what actually runs - and the host is where the
    #: refusal tests live, which are the ones a reader most needs to trust.
    SUITES = (ROOT / "tests", ROOT / "host" / "tests")

    @classmethod
    def _counted(cls) -> int:
        import ast

        total = 0
        for suite in cls.SUITES:
            for path in sorted(suite.glob("test_*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if (isinstance(node, ast.FunctionDef)
                            and node.name.startswith("test")):
                        total += 1
        return total

    def test_every_quoted_total_matches_the_tree(self):
        actual = self._counted()
        self.assertGreater(actual, 1000, msg="test discovery found almost "
                                             "nothing; the counter is broken")
        checked = 0
        for doc in self.DOCUMENTS:
            text = doc.read_text(encoding="utf-8")

            # Form 1: ``N passed / M skipped``. The two must add up to every
            # collected test, which is what the tree holds. Checking the sum
            # rather than ``passed`` alone means the assertion holds even as
            # the skip count moves, without hard-coding which tests skip.
            for match in re.finditer(r"([\d,]+) passed / (\d+) skipped", text):
                passed = int(match.group(1).replace(",", ""))
                skipped = int(match.group(2))
                checked += 1
                self.assertEqual(
                    passed + skipped, actual,
                    msg=f"{doc.name} claims {passed} passed + {skipped} "
                        f"skipped, the tree collects {actual} tests. "
                        f"Re-quote the numbers rather than deleting this "
                        f"assertion.")

            # Form 2: ``### 测试分层（1,247 项）`` - a bare total.
            for match in re.finditer(r"测试分层（([\d,]+) 项）", text):
                value = int(match.group(1).replace(",", ""))
                checked += 1
                self.assertEqual(
                    value, actual,
                    msg=f"{doc.name} says the test total is {value}, the "
                        f"tree collects {actual}")

            # Form 3: ``1,247 项测试里`` in prose - also a bare total.
            # Restricted to four-digit counts: the docs also quote per-module
            # figures ("29 项测试", "9 项测试"), which are asserted against the
            # modules themselves elsewhere. Only the whole-suite total belongs
            # here, and that number is always four digits or more.
            for match in re.finditer(r"(\d{1,3}(?:,\d{3})+|\d{4,}) 项测试", text):
                value = int(match.group(1).replace(",", ""))
                checked += 1
                self.assertEqual(
                    value, actual,
                    msg=f"{doc.name} says there are {value} tests, the "
                        f"tree collects {actual}")

        self.assertGreater(checked, 2, msg="fewer quoted totals found than "
                                           "expected; if the wording changed, "
                                           "update the patterns instead of "
                                           "dropping the test")


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


class RoadmapClaimTest(unittest.TestCase):
    """The roadmap says what is missing; this checks it is still missing.

    ``docs/roadmap.md`` opens by claiming the assembly layer is empty - three
    components, no process that starts them, no installer. That claim is the
    whole justification for the ordering, so it has to be checked mechanically
    or it will quietly become false: the day ``host/supervisor.py`` lands, the
    roadmap's first paragraph becomes wrong, and a reader cannot tell.

    Each assertion names the file whose *absence* the roadmap depends on. When
    one of them appears, this test fails and points at the paragraph to update.
    That is the intent: the roadmap is allowed to change, it is not allowed to
    go stale.
    """

    ROADMAP = ROOT / "docs" / "roadmap.md"

    def setUp(self):
        if not self.ROADMAP.exists():
            self.skipTest("docs/roadmap.md not present")
        self.text = self.ROADMAP.read_text(encoding="utf-8")

    def test_stage_zero_files_are_still_absent(self):
        """While these are missing, stage 0 is genuinely the next thing."""
        host = ROOT / "host"
        for name in ("supervisor.py", "__main__.py"):
            path = host / name
            self.assertFalse(
                path.exists(),
                msg=f"host/{name} now exists, so roadmap.md stage 0 is done. "
                    f"Update the roadmap and this test rather than deleting "
                    f"the assertion.")

    def test_the_service_host_is_still_absent(self):
        self.assertFalse((ROOT / "host" / "service.py").exists(),
                         msg="host/service.py exists; roadmap stage 3 has "
                             "started - update the roadmap")

    def test_no_production_code_starts_the_proxy(self):
        """The claim that makes stage 0 urgent, checked rather than asserted.

        If some future commit wires the proxy into a supervisor, the roadmap's
        "three components, zero assemblers" framing stops being true.
        """
        offenders = []
        for path in sorted((ROOT / "host").rglob("*.py")):
            if "tests" in path.parts or path.parts[-2] == "proxy":
                continue
            text = path.read_text(encoding="utf-8")
            if "serve_forever_in_thread" in text or "ProxyServer(" in text:
                offenders.append(path.name)
        self.assertEqual(offenders, [],
                         msg=f"production code now starts the proxy: "
                             f"{offenders}. Update roadmap.md stage 0/1.")

    def test_no_browser_proxy_policy_is_written_yet(self):
        """Stage 1's premise: the proxy is never pointed at a browser.

        A proxy nobody is routed through protects nothing. When ``ProxyMode``
        starts being written, stage 1 is done and the roadmap needs editing.
        """
        policy = ROOT / "host" / "policy" / "__init__.py"
        text = policy.read_text(encoding="utf-8")
        self.assertNotIn("ProxyMode", text,
                         msg="the browser proxy policy is now written; "
                             "roadmap stage 1 is done - update the roadmap")

    def test_the_console_is_still_read_only(self):
        """Stage 2's premise: the GUI cannot change anything."""
        text = (ROOT / "host" / "ui" / "server.py").read_text(encoding="utf-8")
        self.assertIn("read_only", text,
                      msg="the console now accepts writes; roadmap stage 2 "
                          "is done - update the roadmap")

    def test_the_roadmap_states_the_deployment_defaults(self):
        """The two decisions that were settled must stay stated.

        They were open questions once and were re-litigated more than once. The
        roadmap records the answers so they are not reopened by accident.
        """
        for phrase in ("Program Files", "专用", "便携"):
            self.assertIn(phrase, self.text,
                          msg=f"roadmap.md no longer states {phrase!r}, which "
                              f"was a settled decision")


class CoverageClaimTest(unittest.TestCase):
    """The coverage matrix's conclusions depend on facts in the code.

    ``docs/coverage-matrix.md`` answers "can the extension replace the proxy?"
    with no, and the argument rests on four mechanical facts. Each one is cheap
    to change by accident - a new manifest permission, a tag removed from
    ``SKIP_TAGS`` - and the document would silently become wrong.

    These assertions are the reason the answer is no, so they are asserted here
    rather than trusted to prose.
    """

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT))
        from host import extension

        cls.extension = extension

    def test_the_extension_cannot_block_requests(self):
        """No webRequest/tabs means it can only hide, never prevent.

        This is the whole "bytes already arrived" argument. If a future change
        adds ``webRequest`` with blocking, the matrix's central comparison
        becomes wrong and must be rewritten.
        """
        permissions = set(self.extension.manifest()["permissions"])
        for forbidden in ("webRequest", "webRequestBlocking", "tabs",
                          "webNavigation", "declarativeNetRequest"):
            self.assertNotIn(
                forbidden, permissions,
                msg=f"the extension now requests {forbidden!r}, so it may be "
                    f"able to block requests. docs/coverage-matrix.md says it "
                    f"cannot - rewrite that section.")

    def test_video_and_canvas_are_still_skipped(self):
        """The two blind spots the matrix admits to.

        If either stops being skipped, the matrix is understating coverage; if
        the list grows, it is overstating it. Either way it needs an edit.
        """
        skip = set(self.extension.SKIP_TAGS)
        self.assertIn("video", skip,
                      msg="video is no longer skipped; coverage-matrix.md 2.3 "
                          "says video content is not judged")
        self.assertIn("canvas", skip,
                      msg="canvas is no longer skipped; coverage-matrix.md 2.3 "
                          "says canvas content is not judged")

    def test_iframes_are_covered_even_though_the_tag_is_skipped(self):
        """The distinction the matrix is careful to make.

        ``iframe`` is in SKIP_TAGS (the container's own text is not judged) but
        ``all_frames`` is true (the inner document is injected and judged).
        Conflating the two would make the matrix claim iframes are uncovered,
        which is wrong and would misdirect a later reader.
        """
        self.assertIn("iframe", set(self.extension.SKIP_TAGS))
        scripts = self.extension.manifest()["content_scripts"]
        self.assertTrue(all(s.get("all_frames") for s in scripts),
                        msg="content scripts no longer run in sub-frames; "
                            "coverage-matrix.md 2.3 says iframe content is "
                            "judged via all_frames")

    def test_only_three_browsers_are_configurable(self):
        """The matrix's browser count, checked against detection.

        ``detect()`` returns whatever is installed on this machine, so the
        assertion is about the *configurable* subset, not about the machine:
        IE is detected and reported but cannot be configured, which is why the
        matrix says three browsers and not four.
        """
        from host.browsers import detect

        browsers = detect()
        configurable = [b for b in browsers if b.configurable]
        self.assertEqual(len(configurable), 3,
                         msg=f"expected Chrome/Edge/Firefox to be "
                             f"configurable, got "
                             f"{[b.display_name for b in configurable]}")
        for browser in browsers:
            if browser.display_name == "Internet Explorer":
                self.assertFalse(
                    browser.configurable,
                    msg="IE is now configurable; coverage-matrix.md says it "
                        "cannot receive the extension")

    def test_the_proxy_is_not_a_gateway(self):
        """The matrix's other correction: this is an explicit proxy.

        A transparent gateway would need ARP spoofing, routing redirection or a
        WFP driver - all of which intercept the whole machine, which is the
        same scope objection that declined MITM. Asserted so a future change
        that starts sniffing interfaces is caught.
        """
        from host.proxy import server as proxy_server

        text = Path(proxy_server.__file__).read_text(encoding="utf-8")
        for forbidden in ("ARP", "WinDivert", "WFP", "promiscuous",
                          "IP_TRANSPARENT", "SO_ORIGINAL_DST"):
            self.assertNotIn(
                forbidden, text,
                msg=f"the proxy references {forbidden!r}; it is documented as "
                    f"an explicit proxy, not a gateway. Update "
                    f"coverage-matrix.md 4.2.")


class FirewallPolicyClaimTest(unittest.TestCase):
    """The firewall policy document rests on four facts about the code.

    ``docs/firewall-policy.md`` answers two questions - should rules be user
    editable, and should time control use the firewall - and both answers depend
    on what the code can currently express. Each assertion below is one of those
    dependencies, so the document cannot quietly become wrong.
    """

    def test_domains_are_not_firewall_rules(self):
        """Windows Firewall has no host-name condition, so the validator
        rejects them - which is why domain blocking belongs to the proxy."""
        from host import firewall

        self.assertFalse(firewall.is_address("example.com"))
        plan = firewall.build_add_rules(["example.com"])
        self.assertEqual(plan.rules, ())
        self.assertEqual(plan.rejected, ("example.com",))

    def test_block_everything_is_one_rule(self):
        """The premise of the hard time-control option in section 4.3.

        Before CIDR support this returned an empty plan, so a scheduled blackout
        would silently do nothing. That is the bug the document records.
        """
        from host import firewall

        plan = firewall.build_add_rules(["0.0.0.0/0"])
        self.assertEqual(len(plan.rules), 1)
        self.assertEqual(plan.rejected, ())

    def test_the_firewall_has_no_time_condition(self):
        """If a schedule ever appears in the firewall module, section 4 changes.

        Checked against the source rather than the behaviour because the claim
        is about what the module is for: it builds rules, it does not decide
        when they apply.
        """
        text = (ROOT / "host" / "firewall.py").read_text(encoding="utf-8")
        for forbidden in ("WeeklyGrid", "is_allowed_at", "outside_allowed_hours",
                          "datetime", "schtasks"):
            self.assertNotIn(
                forbidden, text,
                msg=f"host/firewall.py now references {forbidden!r}; "
                    f"docs/firewall-policy.md 4 says time control is not a "
                    f"firewall concern - update that section.")

    def test_the_engine_schedule_is_not_yet_wired_to_the_host(self):
        """Section 4.4: soft time control exists in the engine, unused.

        ``filter.check_request`` honours a schedule, but ``host/engine.py``
        never builds one, so nothing in the product is time-limited. When that
        changes, 4.4's status column needs editing.
        """
        engine = (ROOT / "host" / "engine.py").read_text(encoding="utf-8")
        self.assertNotIn(
            "WeeklyGrid", engine,
            msg="host/engine.py now constructs a schedule; "
                "docs/firewall-policy.md 4.4 says soft time control is not "
                "wired - update the status table")

    def test_the_engine_does_honour_a_schedule_when_given_one(self):
        """The other half of 4.4: the capability is real, not planned.

        If this stopped being true, the document's "engine DONE, host not
        wired" would become "nothing exists", which changes the advice.
        """
        text = (ROOT / "src" / "green_dam" / "filter.py").read_text(
            encoding="utf-8")
        self.assertIn("is_allowed_at", text)
        self.assertIn("outside_allowed_hours", text)

    def test_the_console_still_cannot_edit_rules(self):
        """Section 5.3: rule editing waits for the write framework."""
        text = (ROOT / "host" / "ui" / "server.py").read_text(encoding="utf-8")
        self.assertIn("read_only", text)


if __name__ == "__main__":
    unittest.main()
