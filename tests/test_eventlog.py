"""Tests for structured event logging.

The load-bearing property here is that a log record never contains user
content. Several tests exist purely to assert that, because a regression would
be silent and would recreate the original product's worst behaviour.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam.eventlog import (  # noqa: E402
    DIGEST_CHARS,
    Event,
    EventLog,
    LogRecord,
    digest,
    read_jsonl,
    record_for_assessment,
    record_for_decision,
    record_for_gate,
)
from green_dam.intercept import Decision, Interceptor, Outcome, Request, Stage  # noqa: E402
from green_dam.namelists import NameLists  # noqa: E402
from green_dam.policy import Verdict, assess  # noqa: E402


class DigestTest(unittest.TestCase):
    def test_truncated_length(self):
        self.assertEqual(len(digest("anything")), DIGEST_CHARS)

    def test_stable_and_distinct(self):
        self.assertEqual(digest("甲"), digest("甲"))
        self.assertNotEqual(digest("甲"), digest("乙"))

    def test_empty_is_defined(self):
        self.assertEqual(len(digest("")), DIGEST_CHARS)


class RecordShapeTest(unittest.TestCase):
    def test_json_has_required_keys(self):
        rec = LogRecord(event=Event.BLOCK, source="network", stage="host",
                        reason="host_listed", when="2026-01-01T00:00:00Z")
        obj = json.loads(rec.to_json())
        for key in ("event", "source", "stage", "reason", "when"):
            self.assertIn(key, obj)
        self.assertEqual(obj["event"], "block")

    def test_empty_fields_dropped_but_anchors_kept(self):
        rec = LogRecord(event=Event.ALLOW, when="2026-01-01T00:00:00Z")
        obj = json.loads(rec.to_json())
        self.assertIn("when", obj)
        self.assertIn("event", obj)
        self.assertNotIn("source", obj)

    def test_json_is_single_line(self):
        rec = LogRecord(event=Event.BLOCK, reason="x\ny", when="t")
        self.assertNotIn("\n", rec.to_json())


class FromDecisionTest(unittest.TestCase):
    def test_block(self):
        d = Decision(Outcome.BLOCK, Stage.HOST, "host_listed")
        rec = record_for_decision(d, when="T")
        self.assertEqual(rec.event, Event.BLOCK)
        self.assertEqual(rec.source, "network")
        self.assertEqual(rec.stage, "host")
        self.assertEqual(rec.reason, "host_listed")

    def test_allow(self):
        d = Decision(Outcome.ALLOW, None, "no_stage_matched")
        rec = record_for_decision(d, when="T")
        self.assertEqual(rec.event, Event.ALLOW)
        self.assertEqual(rec.stage, "")

    def test_decision_detail_is_ignored(self):
        # Decision.detail may hold a matched string; the record must not copy it.
        d = Decision(Outcome.BLOCK, Stage.HOST, "host_listed",
                     detail="secret.example")
        rec = record_for_decision(d, when="T")
        self.assertNotIn("secret.example", rec.to_json())


class FromAssessmentTest(unittest.TestCase):
    def test_network_block_keeps_stage(self):
        net = Decision(Outcome.BLOCK, Stage.PROCESS, "watched_process")
        got = assess(network=net)
        rec = record_for_assessment(got, direction="outbound", when="T")
        self.assertEqual(rec.event, Event.BLOCK)
        self.assertEqual(rec.source, "network")
        self.assertEqual(rec.stage, "process")
        self.assertEqual(rec.direction, "outbound")

    def test_text_block_has_no_stage(self):
        text = Verdict(findings=(), action="block", reason="keyword")
        got = assess(text=text)
        rec = record_for_assessment(got, when="T")
        self.assertEqual(rec.event, Event.BLOCK)
        self.assertEqual(rec.source, "text")
        self.assertEqual(rec.stage, "")

    def test_no_action(self):
        rec = record_for_assessment(assess(), when="T")
        self.assertEqual(rec.event, Event.NO_ACTION)

    def test_gate_record(self):
        rec = record_for_gate("legacy_filtering_disabled", when="T")
        self.assertEqual(rec.event, Event.GATED)
        self.assertEqual(rec.source, "policy")


class NoContentLeakTest(unittest.TestCase):
    """The whole point of this module: logs describe, they do not disclose."""

    def test_blocked_body_text_never_reaches_the_log(self):
        lists = NameLists()
        lists["C"].add("{甲}[乙]")
        icpt = Interceptor(lists=lists)
        body = "...甲乙..." + "private-body-marker"
        d = icpt.decide(Request("inbound", port=80, body=body))
        self.assertTrue(d.blocked)

        rec = record_for_decision(d, when="T")
        blob = rec.to_json()
        for leaked in ("甲", "乙", "private-body-marker"):
            self.assertNotIn(leaked, blob)

    def test_host_never_reaches_the_log(self):
        lists = NameLists()
        lists["A"].add("secret.example")
        icpt = Interceptor(lists=lists)
        d = icpt.decide(Request("outbound", host="secret.example", port=80))
        self.assertTrue(d.blocked)
        rec = record_for_decision(d, when="T")
        self.assertNotIn("secret.example", rec.to_json())

    def test_process_name_never_reaches_the_log(self):
        lists = NameLists()
        lists["B"].add("proc-secret")
        icpt = Interceptor(lists=lists)
        d = icpt.decide(Request("outbound", process="proc-secret"))
        self.assertTrue(d.blocked)
        rec = record_for_decision(d, when="T")
        self.assertNotIn("proc-secret", rec.to_json())


class SinkTest(unittest.TestCase):
    def test_writes_jsonl_and_reads_back(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sub" / "events.jsonl"
            log = EventLog(path=path)
            log.append(LogRecord(event=Event.BLOCK, source="network",
                                 stage="host", reason="host_listed", when="T"))
            log.append(LogRecord(event=Event.ALLOW, when="T"))
            log.close()

            self.assertTrue(path.exists())
            rows = list(read_jsonl(path))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["event"], "block")
            self.assertEqual(rows[1]["event"], "allow")

    def test_sequence_is_monotonic(self):
        log = EventLog()
        a = log.append(LogRecord(event=Event.ALLOW, when="T"))
        b = log.append(LogRecord(event=Event.ALLOW, when="T"))
        self.assertEqual((a.seq, b.seq), (1, 2))

    def test_append_fills_missing_timestamp(self):
        log = EventLog()
        rec = log.append(LogRecord(event=Event.ALLOW))
        self.assertTrue(rec.when.endswith("Z"))
        self.assertGreater(len(rec.when), 10)

    def test_context_manager_closes(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "e.jsonl"
            with EventLog(path=path) as log:
                log.append(LogRecord(event=Event.ALLOW, when="T"))
            self.assertEqual(len(list(read_jsonl(path))), 1)

    def test_queries(self):
        log = EventLog()
        log.extend([
            LogRecord(event=Event.BLOCK, stage="host", when="T"),
            LogRecord(event=Event.ALLOW, when="T"),
            LogRecord(event=Event.BLOCK, stage="content", when="T"),
        ])
        self.assertEqual(len(log.blocked()), 2)
        self.assertEqual(len(log.by_stage("host")), 1)
        self.assertEqual(len(log), 3)

    def test_no_sink_means_no_file(self):
        log = EventLog()
        log.append(LogRecord(event=Event.ALLOW, when="T"))
        self.assertEqual(len(log), 1)
        log.close()


if __name__ == "__main__":
    unittest.main()
