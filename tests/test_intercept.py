"""Tests for the interception decision chain.

Fixtures are invented (甲乙丙丁, example.com, proc-a). No legacy list content
is used, and no assertion prints a legacy entry.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam.intercept import (  # noqa: E402
    BLOCK_ERRNO,
    HTTP_PORTS,
    Decision,
    Interceptor,
    Outcome,
    Request,
    Stage,
    extract_outbound_url,
    looks_binary,
)
from green_dam.namelists import NameLists  # noqa: E402


def _icpt(**entries):
    """Build an Interceptor with entries preloaded into named lists."""
    lists = NameLists()
    for key, texts in entries.items():
        for text in texts:
            lists[key].add(text)
    return Interceptor(lists=lists)


class ConstantsTest(unittest.TestCase):
    def test_block_errno_is_connection_reset(self):
        # 10054 = WSAECONNRESET, so the blocked program sees a network fault.
        self.assertEqual(BLOCK_ERRNO, 10054)

    def test_http_ports_pinned_to_evidence(self):
        self.assertEqual(HTTP_PORTS, frozenset({80, 81, 8080, 8081, 8888, 3128}))


class BinarySniffTest(unittest.TestCase):
    def test_ascii_is_not_binary(self):
        self.assertFalse(looks_binary(b"GET / HTTP/1.1\r\n"))

    def test_high_byte_is_binary(self):
        self.assertTrue(looks_binary(b"\x89PNG"))

    def test_empty_prefix_is_not_binary(self):
        self.assertFalse(looks_binary(b""))

    def test_only_first_ten_bytes_matter(self):
        # The original sniffed 10 bytes (p19).
        self.assertFalse(looks_binary(b"A" * 10 + b"\xff"))
        self.assertTrue(looks_binary(b"A" * 9 + b"\xff"))


class UrlExtractionTest(unittest.TestCase):
    def test_http_after_method(self):
        self.assertEqual(
            extract_outbound_url("GET http://example.com/x HTTP/1.1"),
            "http://example.com/x")

    def test_https(self):
        self.assertEqual(
            extract_outbound_url("POST https://example.com/y HTTP/1.1"),
            "https://example.com/y")

    def test_bare_url(self):
        self.assertEqual(extract_outbound_url("http://example.com"),
                         "http://example.com")

    def test_no_url(self):
        self.assertIsNone(extract_outbound_url("hello world"))

    def test_stops_at_whitespace(self):
        self.assertEqual(extract_outbound_url("http://a/b?c=1 rest"),
                         "http://a/b?c=1")


class StageOrderTest(unittest.TestCase):
    """The original stopped at the first hit; order decides the reason."""

    def test_process_beats_everything(self):
        icpt = _icpt(B=["proc-a"], A=["bad.example"])
        d = icpt.decide(Request("outbound", process="proc-a",
                                host="bad.example", port=80))
        self.assertEqual(d.stage, Stage.PROCESS)
        self.assertEqual(d.reason, "watched_process")

    def test_address_beats_host(self):
        icpt = _icpt(D=["bad.example:80"], A=["bad.example"])
        d = icpt.decide(Request("outbound", host="bad.example", port=80))
        self.assertEqual(d.stage, Stage.ADDRESS)

    def test_host_beats_content(self):
        icpt = _icpt(A=["bad.example"])
        d = icpt.decide(Request("outbound", host="bad.example", port=80,
                                body="GET http://bad.example/x HTTP/1.1"))
        self.assertEqual(d.stage, Stage.HOST)

    def test_content_last(self):
        icpt = _icpt(A=["bad.example"])
        d = icpt.decide(Request("outbound", host="ok.example", port=80,
                                body="GET http://bad.example/x HTTP/1.1"))
        self.assertEqual(d.stage, Stage.CONTENT)
        self.assertEqual(d.reason, "url_in_body")


class OutboundTest(unittest.TestCase):
    def test_clean_request_allowed(self):
        d = _icpt().decide(Request("outbound", process="p", host="ok.example",
                                   port=80, body="GET http://ok.example/ HTTP/1.1"))
        self.assertEqual(d.outcome, Outcome.ALLOW)
        self.assertIsNone(d.stage)
        self.assertIsNone(d.errno)

    def test_block_carries_errno(self):
        d = _icpt(B=["proc-a"]).decide(Request("outbound", process="proc-a"))
        self.assertTrue(d.blocked)
        self.assertEqual(d.errno, BLOCK_ERRNO)

    def test_process_match_is_case_insensitive(self):
        d = _icpt(B=["proc-a"]).decide(Request("outbound", process="PROC-A"))
        self.assertTrue(d.blocked)

    def test_missing_fields_skip_stages(self):
        # No process, no port, no host, no body -> nothing to judge.
        d = _icpt(B=["proc-a"], A=["bad.example"]).decide(Request("outbound"))
        self.assertEqual(d.outcome, Outcome.ALLOW)

    def test_address_needs_both_host_and_port(self):
        d = _icpt(D=["bad.example:80"]).decide(
            Request("outbound", host="bad.example"))
        self.assertEqual(d.outcome, Outcome.ALLOW)


class InboundTest(unittest.TestCase):
    def test_keyword_rule_blocks(self):
        icpt = _icpt(C=["{甲}[乙]"])
        d = icpt.decide(Request("inbound", port=80, body="...甲乙..."))
        self.assertTrue(d.blocked)
        self.assertEqual(d.stage, Stage.CONTENT)
        self.assertEqual(d.reason, "keyword_rule")

    def test_keyword_not_matched(self):
        icpt = _icpt(C=["{甲}[乙]"])
        d = icpt.decide(Request("inbound", port=80, body="...甲丙..."))
        self.assertEqual(d.outcome, Outcome.ALLOW)

    def test_content_skipped_on_non_http_port(self):
        icpt = _icpt(C=["{甲}[乙]"])
        d = icpt.decide(Request("inbound", port=25, body="...甲乙..."))
        self.assertEqual(d.outcome, Outcome.ALLOW)

    def test_binary_body_skips_content_but_host_still_blocks(self):
        # A binary body from a listed host must still be blocked: the sniff
        # suppresses only the content stage, it does not short-circuit.
        icpt = _icpt(A=["bad.example"], C=["{甲}[乙]"])
        d = icpt.decide(Request("inbound", port=80, host="bad.example",
                                prefix=b"\x89PNG", body="...甲乙..."))
        self.assertTrue(d.blocked)
        self.assertEqual(d.stage, Stage.HOST)

    def test_binary_body_without_host_hit_allows(self):
        icpt = _icpt(C=["{甲}[乙]"])
        d = icpt.decide(Request("inbound", port=80, prefix=b"\x89PNG",
                                body="...甲乙..."))
        self.assertEqual(d.outcome, Outcome.ALLOW)

    def test_process_stage_applies_inbound_too(self):
        d = _icpt(B=["proc-a"]).decide(
            Request("inbound", process="proc-a", port=80))
        self.assertEqual(d.stage, Stage.PROCESS)


class DecisionShapeTest(unittest.TestCase):
    def test_allow_has_no_errno(self):
        d = Decision(Outcome.ALLOW, None, "x")
        self.assertFalse(d.blocked)
        self.assertIsNone(d.errno)

    def test_block_has_errno(self):
        d = Decision(Outcome.BLOCK, Stage.HOST, "x")
        self.assertTrue(d.blocked)
        self.assertEqual(d.errno, BLOCK_ERRNO)

    def test_unknown_direction_rejected(self):
        with self.assertRaises(ValueError):
            _icpt().decide(Request("sideways"))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
