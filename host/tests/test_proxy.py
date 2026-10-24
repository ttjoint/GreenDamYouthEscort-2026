"""H2b: the stage-2 plain-HTTP proxy.

Three layers, tested three ways:

``message``  pure parsing, hostile input included. No sockets, no fixtures.
``policy``   pure decisions against a real ``Filter``. No sockets.
``server``   real loopback sockets, a fake upstream, and the property that
             matters most: **a blocked request never reaches the upstream.**

The last one is tested by counting connections at the fake upstream rather than
by inspecting the response. A proxy that connects first and decides afterwards
produces the correct 403 and still leaks the request; only the connection count
catches it.
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

from host.engine import open_filter_from                       # noqa: E402
from host.proxy import message as msg                          # noqa: E402
from host.proxy import policy                                  # noqa: E402
from host.proxy.server import ProxyConfig, serve_forever_in_thread  # noqa: E402

GET = b"GET %s HTTP/1.1\r\nHost: %s\r\n\r\n"


def _engine(tmp: Path):
    built, _wiring = open_filter_from(tmp / "config")
    return built


class _Upstream:
    """A one-shot fake server that records whether it was ever connected to."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.sock.settimeout(2.0)
        self.port = self.sock.getsockname()[1]
        self.connections = 0
        self.received: list[bytes] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _addr = self.sock.accept()
            except (socket.timeout, TimeoutError, OSError):
                continue
            self.connections += 1
            try:
                conn.settimeout(2.0)
                self.received.append(conn.recv(65536))
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
            except OSError:
                pass
            finally:
                conn.close()

    def close(self) -> None:
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass


class MessageTest(unittest.TestCase):
    """Parsing, including the hostile cases a child will find first."""

    def test_absolute_form_request(self):
        parsed = msg.parse_request(
            GET % (b"http://example.com/a?b=1", b"example.com"))
        self.assertIsInstance(parsed, msg.RequestMessage)
        self.assertEqual(parsed.method, "GET")
        self.assertEqual(parsed.host, "example.com")
        self.assertEqual(parsed.path, "/a?b=1")

    def test_origin_form_uses_host_header(self):
        parsed = msg.parse_request(GET % (b"/a", b"example.com:8080"))
        self.assertEqual(parsed.host, "example.com")
        self.assertEqual(parsed.port, 8080)

    def test_port_from_absolute_target(self):
        parsed = msg.parse_request(GET % (b"http://example.com:8080/a", b"x"))
        self.assertEqual(parsed.port, 8080)

    def test_ipv6_authority_is_not_mangled(self):
        """A naive split(':') turns [::1] into '['. This is the classic bug."""
        self.assertEqual(msg.strip_port("[::1]:8080"), "[::1]")
        self.assertEqual(msg.strip_port("[::1]"), "[::1]")
        self.assertEqual(msg.strip_port("example.com:80"), "example.com")
        self.assertEqual(msg.strip_port("example.com"), "example.com")

    def test_body_is_decoded_when_declared(self):
        parsed = msg.parse_request(
            b"POST http://e/ HTTP/1.1\r\nHost: e\r\nContent-Length: 5\r\n\r\nhello")
        self.assertEqual(parsed.body, "hello")

    def test_undeclared_body_is_none_not_empty(self):
        """'no body' and 'body we did not read' must stay distinguishable."""
        parsed = msg.parse_request(GET % (b"/a", b"e"))
        self.assertIsNone(parsed.body)
        self.assertFalse(parsed.body_skipped)

    def test_chunked_body_is_skipped_not_buffered(self):
        parsed = msg.parse_request(
            b"POST http://e/ HTTP/1.1\r\nHost: e\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n")
        self.assertIsNone(parsed.body)
        self.assertTrue(parsed.body_skipped,
                        msg="a chunked body must be declared unread, not "
                            "silently treated as empty")

    def test_oversized_body_is_skipped(self):
        parsed = msg.parse_request(
            b"POST http://e/ HTTP/1.1\r\nHost: e\r\nContent-Length: 999999999\r\n\r\n",
            max_body=1024)
        self.assertIsNone(parsed.body)
        self.assertTrue(parsed.body_skipped)

    def test_truncated_body_is_skipped(self):
        parsed = msg.parse_request(
            b"POST http://e/ HTTP/1.1\r\nHost: e\r\nContent-Length: 50\r\n\r\nshort")
        self.assertIsNone(parsed.body)
        self.assertTrue(parsed.body_skipped)

    def test_incomplete_head_is_a_reason_not_an_exception(self):
        result = msg.parse_request(b"GET http://e/ HTTP/1.1\r\nHost: e\r\n")
        self.assertIsInstance(result, msg.ParseError)
        self.assertIn("incomplete", result)

    def test_tls_client_hello_is_not_http(self):
        result = msg.parse_request(b"\x16\x03\x01\x00\xff\x01\x00\x00\xfb")
        self.assertIsInstance(result, msg.ParseError)
        self.assertIn("not an HTTP/1.x request", result)

    def test_malformed_request_line(self):
        result = msg.parse_request(b"GARBAGE\r\nHost: e\r\n\r\n")
        self.assertIsInstance(result, msg.ParseError)

    def test_malformed_header_line(self):
        result = msg.parse_request(
            b"GET http://e/ HTTP/1.1\r\nno-colon-here\r\n\r\n")
        self.assertIsInstance(result, msg.ParseError)

    def test_header_bomb_is_rejected(self):
        bomb = b"GET http://e/ HTTP/1.1\r\n" + b"X: y\r\n" * 20000 + b"\r\n"
        result = msg.parse_request(bomb, max_header=4096)
        self.assertIsInstance(result, msg.ParseError)

    def test_non_numeric_content_length(self):
        result = msg.parse_request(
            b"POST http://e/ HTTP/1.1\r\nHost: e\r\nContent-Length: abc\r\n\r\n")
        self.assertIsInstance(result, msg.ParseError)

    def test_head_is_preserved_verbatim(self):
        """Re-serialising headers would normalise casing and order."""
        raw = b"GET http://e/ HTTP/1.1\r\nX-CaSeD: Value\r\nHost: e\r\n\r\n"
        parsed = msg.parse_request(raw)
        self.assertEqual(parsed.head, raw)

    def test_parse_error_is_a_string(self):
        """So a reason can be logged or rendered without special-casing."""
        self.assertIsInstance(msg.ParseError("why"), str)


class PolicyTest(unittest.TestCase):
    """Decisions, without a socket in sight."""

    def _decide(self, engine, raw: bytes, **kwargs):
        parsed = msg.parse_request(raw)
        self.assertIsInstance(parsed, msg.RequestMessage)
        return policy.decide(engine, parsed, **kwargs)

    def test_listed_host_is_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            engine.lists["A"].add("evil.example")
            outcome = self._decide(
                engine, GET % (b"http://evil.example/x", b"evil.example"))
            self.assertTrue(outcome.blocked)
            self.assertEqual(outcome.reason, "host_listed")

    def test_unlisted_host_is_forwarded(self):
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            outcome = self._decide(
                engine, GET % (b"http://good.example/x", b"good.example"))
            self.assertFalse(outcome.blocked)
            self.assertEqual(outcome.reason, "allowed")

    def test_plain_http_is_stage_two(self):
        """No CA involved, so a plain request must reach the content stage."""
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            outcome = self._decide(
                engine, GET % (b"http://good.example/x", b"good.example"))
            self.assertEqual(int(outcome.plan.stage), 2)
            self.assertTrue(outcome.plan.sees_content)
            self.assertEqual(outcome.plan.limits, ())

    def test_https_without_ca_is_host_only_and_partial(self):
        """The plan must admit it could not see the body."""
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            outcome = self._decide(
                engine, GET % (b"http://good.example/x", b"good.example"),
                tls=True)
            self.assertEqual(int(outcome.plan.stage), 1)
            self.assertFalse(outcome.plan.sees_content)
            self.assertTrue(outcome.partial)

    def test_partial_is_never_reported_as_plain_allowed(self):
        """'not looked at' must not read as 'clean' in the log."""
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            outcome = self._decide(
                engine, GET % (b"http://good.example/x", b"good.example"),
                tls=True)
            self.assertEqual(outcome.reason, "allowed_partial")

    def test_watched_process_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            engine.lists["B"].add("badclient.exe")
            outcome = self._decide(
                engine, GET % (b"http://good.example/x", b"good.example"),
                process="badclient.exe")
            self.assertTrue(outcome.blocked)
            self.assertEqual(outcome.reason, "watched_process")

    def test_unknown_process_does_not_fire_the_process_stage(self):
        """Attribution is best-effort; None must not block anything."""
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            engine.lists["B"].add("badclient.exe")
            outcome = self._decide(
                engine, GET % (b"http://good.example/x", b"good.example"),
                process=None)
            self.assertFalse(outcome.blocked)

    def test_outcome_summary_carries_no_host(self):
        """The log records the reason, not what was visited."""
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            engine.lists["A"].add("evil.example")
            outcome = self._decide(
                engine, GET % (b"http://evil.example/secret-path", b"evil.example"))
            blob = repr(outcome.to_dict())
            self.assertNotIn("evil.example", blob)
            self.assertNotIn("secret-path", blob)


class BlockPageTest(unittest.TestCase):
    def _page(self, engine, raw: bytes) -> str:
        parsed = msg.parse_request(raw)
        outcome = policy.decide(engine, parsed)
        return policy.block_page(outcome, parsed).decode("utf-8")

    def test_page_never_echoes_the_matched_term(self):
        """A page that echoes the rule turns the filter into an oracle.

        A child can binary-search the wordlist by requesting URLs and reading
        the reply. This is the reason the page names a reason token and nothing
        else.
        """
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            engine.lists["A"].add("forbidden-term.example")
            page = self._page(
                engine, GET % (b"http://forbidden-term.example/x",
                               b"forbidden-term.example"))
            self.assertNotIn("forbidden-term", page)
            self.assertNotIn("host_listed", page,
                             msg="even the token is internal vocabulary")

    def test_page_states_a_reason_in_words(self):
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            engine.lists["A"].add("evil.example")
            page = self._page(
                engine, GET % (b"http://evil.example/x", b"evil.example"))
            self.assertIn("禁止访问的名单", page)

    def test_page_names_the_limit_when_inspection_was_partial(self):
        """Naming our own limitation is not a leak, and is the most useful
        thing the page can say to a parent."""
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            engine.lists["A"].add("evil.example")
            parsed = msg.parse_request(
                GET % (b"http://evil.example/x", b"evil.example"))
            outcome = policy.decide(engine, parsed, tls=True)
            page = policy.block_page(outcome, parsed).decode("utf-8")
            self.assertIn("未能完整检查", page)

    def test_page_is_valid_utf8_html(self):
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            engine.lists["A"].add("evil.example")
            page = self._page(
                engine, GET % (b"http://evil.example/x", b"evil.example"))
            self.assertIn("<!DOCTYPE html>", page)
            self.assertIn('charset="utf-8"', page)

    def test_unknown_reason_falls_back_without_leaking_the_token(self):
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            parsed = msg.parse_request(GET % (b"http://e/x", b"e"))
            fake = policy.Outcome(action=policy.BLOCK, reason="internal_token_xyz",
                                 plan=policy.decide(engine, parsed).plan)
            page = policy.block_page(fake).decode("utf-8")
            self.assertNotIn("internal_token_xyz", page)


class ServerTest(unittest.TestCase):
    """Real loopback sockets. Slow but the only way to test a proxy."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.engine = _engine(Path(self._tmp.name))
        self.upstream = _Upstream()
        self.outcomes: list = []
        self.config = ProxyConfig(filter=self.engine, timeout=5.0,
                                  on_outcome=self.outcomes.append)
        self.server, self.thread = serve_forever_in_thread(self.config)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.upstream.close()
        self._tmp.cleanup()

    def _request(self, raw: bytes) -> bytes:
        sock = socket.create_connection(("127.0.0.1", self.config.port),
                                        timeout=5.0)
        try:
            sock.sendall(raw)
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

    def _url(self, host: str = "127.0.0.1") -> bytes:
        return f"http://{host}:{self.upstream.port}/".encode()

    def test_allowed_request_reaches_the_upstream(self):
        response = self._request(
            GET % (self._url(), f"127.0.0.1:{self.upstream.port}".encode()))
        self.assertIn(b"200 OK", response)
        self.assertEqual(self.upstream.connections, 1)

    def test_blocked_request_never_reaches_the_upstream(self):
        """The property the whole module exists to keep.

        Checking the 403 alone would pass even if the proxy connected first.
        Counting connections at the fake upstream is what distinguishes
        "refused" from "forwarded and then lied about".
        """
        self.engine.lists["A"].add("blocked.example")
        response = self._request(
            GET % (b"http://blocked.example/x", b"blocked.example"))
        self.assertIn(b"403 Forbidden", response)
        self.assertEqual(self.upstream.connections, 0,
                         msg="the blocked host was contacted; the proxy "
                             "decided after connecting instead of before")

    def test_block_page_is_served_not_a_reset(self):
        self.engine.lists["A"].add("blocked.example")
        response = self._request(
            GET % (b"http://blocked.example/x", b"blocked.example"))
        self.assertIn("访问被阻止".encode("utf-8"), response)

    def test_block_page_is_uncacheable(self):
        """A cached 403 would outlive a policy change."""
        self.engine.lists["A"].add("blocked.example")
        response = self._request(
            GET % (b"http://blocked.example/x", b"blocked.example"))
        self.assertIn(b"Cache-Control: no-store", response)

    def test_malformed_request_gets_400_not_a_crash(self):
        response = self._request(b"GARBAGE\r\n\r\n")
        self.assertIn(b"400 Bad Request", response)

    def test_stalled_client_does_not_block_the_next_request(self):
        """One silent client must not take the proxy down for everyone."""
        stalled = socket.create_connection(("127.0.0.1", self.config.port),
                                           timeout=5.0)
        try:
            stalled.sendall(b"GET http://e/ HTTP/1.1\r\n")   # never completes
            response = self._request(
                GET % (self._url(), f"127.0.0.1:{self.upstream.port}".encode()))
            self.assertIn(b"200 OK", response)
        finally:
            stalled.close()

    def test_unreachable_upstream_gets_502(self):
        self.engine.lists["A"].clear() if hasattr(self.engine.lists["A"], "clear") else None
        response = self._request(
            GET % (b"http://127.0.0.1:1/x", b"127.0.0.1:1"))
        self.assertIn(b"502 Bad Gateway", response)

    def test_every_decision_is_reported(self):
        self._request(
            GET % (self._url(), f"127.0.0.1:{self.upstream.port}".encode()))
        self.assertTrue(self.outcomes)
        self.assertEqual(self.outcomes[0].action, policy.FORWARD)

    def test_no_decision_is_reported_for_unparseable_input(self):
        self._request(b"GARBAGE\r\n\r\n")
        self.assertEqual(self.outcomes, [])


if __name__ == "__main__":
    unittest.main()
