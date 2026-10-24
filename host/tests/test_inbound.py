"""Inbound response inspection: the content check that actually blocks pages.

``analysis/probe/p19`` is the evidence: the original keyword-matched the HTTP
*response* body and replaced the reply, not the request. These tests pin the
three things that follow from that, plus the honesty requirement that a body the
proxy could not read is never reported as clean.
"""
from __future__ import annotations

import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from host.engine import open_filter_from                  # noqa: E402
from host.proxy import inbound                            # noqa: E402
from host.proxy.server import ProxyConfig, serve_forever_in_thread  # noqa: E402

PAGE = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: %d\r\n\r\n%s"


def _engine(tmp: Path):
    built, _wiring = open_filter_from(tmp / "config")
    return built


class ResponseHeadTest(unittest.TestCase):
    def test_splits_head_and_body(self):
        head = inbound.parse_response_head(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\nhello")
        self.assertIsNotNone(head)
        self.assertEqual(head.status, "200")
        self.assertEqual(head.body_prefix, b"hello")

    def test_incomplete_head_returns_none(self):
        self.assertIsNone(inbound.parse_response_head(b"HTTP/1.1 200 OK\r\n"))

    def test_non_http_reply_returns_none(self):
        self.assertIsNone(inbound.parse_response_head(b"SSH-2.0-OpenSSH\r\n\r\n"))

    def test_content_length_is_parsed(self):
        head = inbound.parse_response_head(
            b"HTTP/1.1 200 OK\r\nContent-Length: 42\r\n\r\n")
        self.assertEqual(head.content_length, 42)

    def test_image_type_is_not_textual(self):
        head = inbound.parse_response_head(
            b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\n\r\n")
        self.assertFalse(head.is_textual)

    def test_missing_type_is_treated_as_textual(self):
        """The binary sniff is the authoritative test; it runs anyway."""
        head = inbound.parse_response_head(b"HTTP/1.1 200 OK\r\n\r\n")
        self.assertTrue(head.is_textual)


class InspectBodyTest(unittest.TestCase):
    def _inspect(self, engine, body: bytes, *, ctype: str = "text/html",
                port: int = 80, declared: int | None = None):
        length = len(body) if declared is None else declared
        head = inbound.parse_response_head(
            f"HTTP/1.1 200 OK\r\nContent-Type: {ctype}\r\n"
            f"Content-Length: {length}\r\n\r\n".encode("ascii"))
        return inbound.inspect_body(engine, head, body, port=port)

    def test_keyword_hit_blocks(self):
        """This is the check p19 describes, and the reason a page is blocked."""
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            engine.lists["C"].add("赌博")
            result = self._inspect(engine, "这里有赌博内容".encode("utf-8"))
            self.assertTrue(result.checked)
            self.assertTrue(result.blocked)
            self.assertEqual(result.reason, "keyword_rule")

    def test_clean_body_is_checked_and_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            engine.lists["C"].add("赌博")
            result = self._inspect(engine, "普通内容".encode("utf-8"))
            self.assertTrue(result.checked)
            self.assertFalse(result.blocked)

    def test_non_http_port_is_not_checked(self):
        """p19 step 6: keyword matching only ran on the HTTP ports."""
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            engine.lists["C"].add("赌博")
            result = self._inspect(engine, "赌博".encode("utf-8"), port=22)
            self.assertFalse(result.checked)
            self.assertEqual(result.limit, "port_not_filtered")

    def test_binary_body_is_not_checked(self):
        """p19 step 4: high-bit bytes mean binary, and binary is skipped."""
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            engine.lists["C"].add("赌博")
            result = self._inspect(engine, b"\x89PNG\r\n\x1a\n\x00\x00")
            self.assertFalse(result.checked)
            self.assertIn(result.limit, {"binary_body", "non_textual_type"})

    def test_image_type_is_not_checked(self):
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            result = self._inspect(engine, b"\x00" * 20, ctype="image/jpeg")
            self.assertFalse(result.checked)

    def test_truncated_body_is_not_checked(self):
        """'we did not finish reading' must never be recorded as 'clean'."""
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            engine.lists["C"].add("赌博")
            result = self._inspect(engine, b"short", declared=9999)
            self.assertFalse(result.checked)
            self.assertEqual(result.limit, "body_truncated")

    def test_oversized_body_is_not_checked(self):
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            huge = b"a" * (inbound.MAX_INSPECT_BYTES + 1)
            result = self._inspect(engine, huge)
            self.assertFalse(result.checked)
            self.assertEqual(result.limit, "body_too_large")

    def test_unchecked_is_never_blocked(self):
        """Absence of evidence is not evidence of a block either."""
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            result = self._inspect(engine, b"x", port=22)
            self.assertFalse(result.blocked)

    def test_summary_carries_no_body(self):
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            engine.lists["C"].add("赌博")
            result = self._inspect(engine, "这里有赌博内容".encode("utf-8"))
            blob = repr(result.to_dict())
            self.assertNotIn("赌博", blob)


class ReplacementTest(unittest.TestCase):
    def test_replacement_discards_upstream_length(self):
        """Keeping the upstream's Content-Length would hang the browser.

        The upstream promised 9000 bytes; the replacement is a short page. A
        merged header set would leave the browser waiting for bytes that will
        never arrive.
        """
        inspection = inbound.Inspection(checked=True, blocked=True,
                                        reason="keyword_rule")
        response = inbound.replacement_response(inspection, b"<html></html>")
        self.assertIn(b"Content-Length: 13", response)
        self.assertNotIn(b"9000", response)

    def test_replacement_is_uncacheable(self):
        inspection = inbound.Inspection(checked=True, blocked=True)
        response = inbound.replacement_response(inspection, b"x")
        self.assertIn(b"Cache-Control: no-store", response)

    def test_replacement_is_a_complete_response(self):
        inspection = inbound.Inspection(checked=True, blocked=True)
        response = inbound.replacement_response(inspection, b"<p>x</p>")
        self.assertTrue(response.startswith(b"HTTP/1.1 403 Forbidden"))


class EndToEndTest(unittest.TestCase):
    """A real upstream serving a page with a forbidden word in it.

    The fake upstream listens on an ephemeral port, but the *request target*
    names port 80 - which is what the port gate tests, because that is what the
    client asked for. Binding port 80 in a test would need privileges and would
    collide with anything already running; naming it in the target does not.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.engine = _engine(Path(self._tmp.name))
        self.inspections: list = []
        self._upstream_body = b""
        self.upstream = socket.socket()
        self.upstream.bind(("127.0.0.1", 0))
        self.upstream.listen(4)
        self.upstream.settimeout(2.0)
        self.port = self.upstream.getsockname()[1]
        self._stop = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()
        self.config = ProxyConfig(filter=self.engine, timeout=5.0,
                                  on_inspection=self.inspections.append,
                                  upstream_override=("127.0.0.1", self.port))
        self.server, _thread = serve_forever_in_thread(self.config)

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _addr = self.upstream.accept()
            except (socket.timeout, TimeoutError, OSError):
                continue
            try:
                conn.recv(65536)
                conn.sendall(PAGE % (len(self._upstream_body),
                                     self._upstream_body))
            except OSError:
                pass
            finally:
                conn.close()

    def tearDown(self):
        self._stop.set()
        self.server.shutdown()
        self.server.server_close()
        try:
            self.upstream.close()
        except OSError:
            pass
        self._tmp.cleanup()

    def _request(self) -> bytes:
        sock = socket.create_connection(("127.0.0.1", self.config.port),
                                        timeout=5.0)
        try:
            # Port 80 in the target, ephemeral port in the connection: the
            # request names a filtered port, which is what the gate reads.
            sock.sendall(
                b"GET http://127.0.0.1/ HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n\r\n")
            chunks = []
            while True:
                try:
                    chunk = sock.recv(65536)
                except (socket.timeout, TimeoutError):
                    break
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            sock.close()

    def test_page_with_forbidden_word_is_replaced(self):
        """The whole point of inbound inspection, end to end."""
        self.engine.lists["C"].add("赌博")
        self._upstream_body = "这里是赌博页面".encode("utf-8")
        response = self._request()
        self.assertIn(b"403 Forbidden", response)
        self.assertIn("访问被阻止".encode("utf-8"), response)
        self.assertNotIn(self._upstream_body, response)

    def test_clean_page_passes_through_unchanged(self):
        self.engine.lists["C"].add("赌博")
        self._upstream_body = b"an ordinary page"
        response = self._request()
        self.assertIn(b"200 OK", response)
        self.assertIn(b"an ordinary page", response)

    def test_inspection_is_reported_for_every_response(self):
        self._upstream_body = b"clean"
        self._request()
        self.assertTrue(self.inspections)
        self.assertTrue(self.inspections[0].checked)

    def test_blocked_response_still_reports_what_was_checked(self):
        """A block must say the body was read, not that it was skipped."""
        self.engine.lists["C"].add("赌博")
        self._upstream_body = "赌博".encode("utf-8")
        self._request()
        self.assertTrue(self.inspections[0].checked)
        self.assertTrue(self.inspections[0].blocked)


if __name__ == "__main__":
    unittest.main()
