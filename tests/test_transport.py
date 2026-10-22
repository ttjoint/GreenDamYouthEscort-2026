"""Tests for the four-stage planning skeleton and the replay harness.

No network is used anywhere in this file. That is the point: the stage logic is
the part the architecture makes guarantees about, and it can be verified
without a proxy, a CA, or administrator rights.

Fixtures are invented (example hosts, 甲乙丙丁).
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam.eventlog import EventLog  # noqa: E402
from green_dam.filter import Filter  # noqa: E402
from green_dam.namelists import NameLists  # noqa: E402
from green_dam.replay import (  # noqa: E402
    FlowFileError,
    load_flows,
    parse_flows,
    replay,
    replay_file,
)
from green_dam.transport import (  # noqa: E402
    LIMIT_NO_BODY,
    LIMIT_NO_CA,
    LIMIT_NO_IMAGE,
    LIMIT_PINNED,
    LIMIT_QUIC,
    LIMIT_UNKNOWN_PROTOCOL,
    Flow,
    Stage,
    describe,
    keyword_ports,
    plan,
)


def _flow(**kwargs) -> Flow:
    defaults = {"host": "example.com", "port": 80}
    defaults.update(kwargs)
    return Flow(**defaults)


class StageSelectionTest(unittest.TestCase):
    """D5: each protocol maps to the stage that can actually judge it."""

    def test_plain_http_is_stage_two(self):
        got = plan(_flow(protocol="http"))
        self.assertEqual(got.stage, Stage.PLAIN_HTTP)
        self.assertTrue(got.sees_content)

    def test_https_without_ca_is_host_only(self):
        got = plan(_flow(protocol="https", port=443), ca_installed=False)
        self.assertEqual(got.stage, Stage.HOST_ONLY)
        self.assertFalse(got.sees_content)

    def test_https_with_ca_is_mitm(self):
        got = plan(_flow(protocol="https", port=443), ca_installed=True)
        self.assertEqual(got.stage, Stage.HTTPS_MITM)
        self.assertTrue(got.sees_content)

    def test_pinned_certificate_stays_host_only_even_with_ca(self):
        # D7: pinning defeats interception; pretending otherwise would be a lie.
        got = plan(_flow(protocol="https", port=443, pinned=True),
                   ca_installed=True)
        self.assertEqual(got.stage, Stage.HOST_ONLY)
        self.assertIn(LIMIT_PINNED, got.limits)

    def test_quic_is_never_proxied(self):
        # D8: QUIC is UDP; the proxy cannot see it.
        got = plan(_flow(protocol="quic", port=443), ca_installed=True)
        self.assertEqual(got.stage, Stage.HOST_ONLY)
        self.assertIn(LIMIT_QUIC, got.limits)

    def test_unknown_protocol_degrades_to_host_only(self):
        got = plan(_flow(protocol="other"))
        self.assertEqual(got.stage, Stage.HOST_ONLY)
        self.assertIn(LIMIT_UNKNOWN_PROTOCOL, got.limits)

    def test_stage_one_always_works(self):
        # Whatever the protocol, host and port are visible.
        for protocol in ("http", "https", "quic", "other"):
            got = plan(_flow(protocol=protocol))
            self.assertGreaterEqual(int(got.stage), int(Stage.HOST_ONLY))
            self.assertEqual(got.request.host, "example.com")
            self.assertEqual(got.request.port, 80)


class DegradationTest(unittest.TestCase):
    """D6: a stage that cannot see content must say so, not claim cleanliness."""

    def test_no_ca_is_reported_as_a_limit(self):
        got = plan(_flow(protocol="https", port=443), ca_installed=False)
        self.assertIn(LIMIT_NO_CA, got.limits)
        self.assertTrue(got.partial)

    def test_full_visibility_is_not_partial(self):
        got = plan(_flow(protocol="http"))
        self.assertEqual(got.limits, ())
        self.assertFalse(got.partial)

    def test_body_withheld_when_stage_cannot_see_it(self):
        # A caller that supplies a body for a flow the stage cannot decrypt must
        # not have that body judged: the stage could not have obtained it.
        got = plan(_flow(protocol="https", port=443, body="secret"),
                   ca_installed=False)
        self.assertIsNone(got.request.body)

    def test_body_passed_when_stage_can_see_it(self):
        got = plan(_flow(protocol="http", body="GET http://example.com/ HTTP/1.1"))
        self.assertEqual(got.request.body, "GET http://example.com/ HTTP/1.1")

    def test_image_without_support_is_a_limit(self):
        got = plan(_flow(protocol="http", body="x", is_image=True),
                   image_supported=False)
        self.assertIn(LIMIT_NO_IMAGE, got.limits)
        self.assertFalse(got.sees_images)

    def test_image_needs_a_body(self):
        got = plan(_flow(protocol="http", is_image=True))
        self.assertIn(LIMIT_NO_BODY, got.limits)
        self.assertFalse(got.sees_images)

    def test_image_stage_when_everything_is_available(self):
        got = plan(_flow(protocol="http", body="x", is_image=True),
                   image_supported=True)
        self.assertEqual(got.stage, Stage.CONTENT_IMAGE)
        self.assertTrue(got.sees_images)

    def test_describe_contains_no_host_or_body(self):
        got = plan(_flow(protocol="http", host="secret.example",
                         body="private-marker"))
        blob = json.dumps(describe(got))
        self.assertNotIn("secret.example", blob)
        self.assertNotIn("private-marker", blob)


class KeywordPortTest(unittest.TestCase):
    def test_ports_match_the_intercept_layer(self):
        from green_dam.intercept import HTTP_PORTS
        self.assertEqual(keyword_ports(), HTTP_PORTS)


class FlowFileTest(unittest.TestCase):
    def test_parses_a_minimal_flow(self):
        flows = parse_flows([{"host": "a.example", "port": 80}])
        self.assertEqual(len(flows), 1)
        self.assertEqual(flows[0].protocol, "http")

    def test_rejects_non_array(self):
        with self.assertRaises(FlowFileError):
            parse_flows({"host": "a"})

    def test_rejects_unknown_keys(self):
        # A typo in a fixture must fail loudly, not silently change the test.
        with self.assertRaises(FlowFileError):
            parse_flows([{"host": "a", "port": 80, "protocl": "https"}])

    def test_rejects_missing_required(self):
        with self.assertRaises(FlowFileError):
            parse_flows([{"host": "a"}])

    def test_rejects_bad_port(self):
        for port in (0, -1, 70000, "80"):
            with self.assertRaises(FlowFileError, msg=str(port)):
                parse_flows([{"host": "a", "port": port}])

    def test_rejects_non_object_entry(self):
        with self.assertRaises(FlowFileError):
            parse_flows(["not an object"])

    def test_invalid_json(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "f.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(FlowFileError):
                load_flows(path)


class ReplayTest(unittest.TestCase):
    """The harness must produce the same decisions the filter would directly."""

    def _filter(self, log=None):
        lists = NameLists()
        lists["A"].add("bad.example")
        lists["B"].add("proc-secret")
        return Filter(lists=lists, log=log)

    def test_blocked_host_is_blocked_through_replay(self):
        results = list(replay(
            [Flow(host="bad.example", port=80)],
            filter_obj=self._filter()))
        self.assertTrue(results[0].assessment.blocked)

    def test_clean_host_is_not_blocked(self):
        results = list(replay(
            [Flow(host="ok.example", port=80)],
            filter_obj=self._filter()))
        self.assertFalse(results[0].assessment.blocked)

    def test_replay_matches_direct_call(self):
        f = self._filter()
        flow = Flow(host="bad.example", port=80)
        planned = plan(flow)
        direct = f.check(request=planned.request)
        replayed = list(replay([flow], filter_obj=self._filter()))[0]
        self.assertEqual(direct.action, replayed.assessment.action)

    def test_https_without_ca_still_judges_host(self):
        # D6: stage 1 must remain useful even when content is invisible.
        results = list(replay(
            [Flow(host="bad.example", port=443, protocol="https")],
            filter_obj=self._filter(), ca_installed=False))
        self.assertTrue(results[0].assessment.blocked)
        self.assertIn(LIMIT_NO_CA, results[0].limits)

    def test_limits_are_visible_per_flow(self):
        results = list(replay(
            [Flow(host="ok.example", port=443, protocol="https", pinned=True)],
            filter_obj=self._filter(), ca_installed=True))
        self.assertIn(LIMIT_PINNED, results[0].limits)

    def test_replay_file_end_to_end(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "flows.json"
            path.write_text(json.dumps([
                {"host": "bad.example", "port": 80},
                {"host": "ok.example", "port": 80},
            ]), encoding="utf-8")
            log = EventLog()
            results = replay_file(path, filter_obj=self._filter(log=log))
            self.assertEqual(len(results), 2)
            self.assertTrue(results[0].assessment.blocked)
            self.assertFalse(results[1].assessment.blocked)
            self.assertEqual(len(log), 2)

    def test_replay_log_contains_no_hosts(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "flows.json"
            path.write_text(json.dumps([
                {"host": "secret.example", "port": 80},
            ]), encoding="utf-8")
            log = EventLog()
            replay_file(path, filter_obj=self._filter(log=log))
            blob = "".join(r.to_json() for r in log.records)
            self.assertNotIn("secret.example", blob)


if __name__ == "__main__":
    unittest.main()
