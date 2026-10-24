"""The stage-2 proxy listener: sockets, and as little else as possible.

Everything interesting happens in :mod:`host.proxy.policy`, which is socket-free
and exhaustively testable. This module owns the parts that cannot be: accepting
connections, reading bytes, and relaying them.

Two properties it has to keep, both of which are easy to lose in a rewrite:

**A blocked request never reaches the upstream.** :func:`_handle` decides before
it connects. Reordering those two lines would still produce correct *output* and
would still pass a naive test, while leaking the request to a blocked server.

**The listener is single-threaded per connection and never blocks the accept
loop indefinitely.** A child can hold a connection open without sending a
complete head; every read has a deadline, and exceeding it closes the socket.
Without that, one stalled client stops the whole proxy - the cheapest possible
denial of service, and the first thing a curious teenager tries.
"""
from __future__ import annotations

import socket
import socketserver
import threading
from dataclasses import dataclass, field

from .message import (MAX_BODY_BYTES, MAX_HEADER_BYTES, ParseError,
                      RequestMessage, parse_request)
from .policy import BLOCK_STATUS, block_page, decide

__all__ = [
    "ProxyConfig",
    "ProxyServer",
    "serve_forever_in_thread",
    "DEFAULT_TIMEOUT",
]

#: Seconds a single read may block. Long enough for a slow mobile connection,
#: short enough that a stalled client cannot hold a slot for long.
DEFAULT_TIMEOUT = 10.0

#: Largest head we will read before giving up. Matches the parser's limit so the
#: parser's own check is the only one that can fire.
_READ_CHUNK = 8192


@dataclass
class ProxyConfig:
    """What the listener needs to know. All fields have safe defaults."""

    #: The engine. Only ``check_request`` is used.
    filter: object
    #: ``("127.0.0.1", 0)`` for tests; the product binds the loopback only.
    bind: tuple[str, int] = ("127.0.0.1", 0)
    timeout: float = DEFAULT_TIMEOUT
    #: Called with each :class:`~host.proxy.policy.Outcome`, for the audit log.
    on_outcome: object = None
    #: Called with each :class:`~host.proxy.inbound.Inspection` of a response
    #: body. Separate from ``on_outcome`` because "this request was blocked" and
    #: "this response was inspected and was clean" are different events, and a
    #: log that merges them cannot answer "was the page actually read".
    on_inspection: object = None
    #: Client executable name when the host could attribute it. None = unknown,
    #: and the process stage simply does not fire.
    process: str | None = None
    #: True when the host has installed a CA and terminates TLS itself. Stage 3
    #: is not implemented yet, so this only affects what ``plan`` reports.
    ca_installed: bool = False
    #: ``(host, port)`` to connect to instead of the address in the request.
    #:
    #: This exists for tests, and it is the only way to exercise the inbound path
    #: honestly: the port gate keys on the *requested* port (80, 8080, ...), so a
    #: test that names port 80 would otherwise try to connect to a real server on
    #: port 80 - which needs privileges and is not what the test is about. With
    #: the override, the request says port 80 and the bytes come from the fake
    #: upstream on its ephemeral port, which is exactly the real-world shape.
    #:
    #: Left as None in the product. It is not a bypass: the request is still
    #: decided and inspected exactly as it would be otherwise.
    upstream_override: tuple[str, int] | None = None
    #: Set by the server once it is listening; read by tests.
    port: int = 0


class _Handler(socketserver.BaseRequestHandler):
    """One connection. Reads a request, decides, then forwards or refuses."""

    def handle(self) -> None:                    # pragma: no cover - exercised via tests
        config: ProxyConfig = self.server.config     # type: ignore[attr-defined]
        sock: socket.socket = self.request
        sock.settimeout(config.timeout)

        data = self._read_head(sock)
        if data is None:
            return

        message = parse_request(data, max_header=MAX_HEADER_BYTES,
                                max_body=MAX_BODY_BYTES)
        if isinstance(message, ParseError):
            self._send(sock, "400 Bad Request",
                       f"<p>无法解析请求：{message}</p>".encode("utf-8"))
            return

        outcome = decide(config.filter, message,
                         tls=message.is_tls, process=config.process)
        if config.on_outcome is not None:
            config.on_outcome(outcome)

        if outcome.blocked:
            # Decided *before* connecting: the upstream never learns it was
            # asked for. See the module docstring.
            self._send(sock, BLOCK_STATUS, block_page(outcome, message))
            return

        self._forward(sock, message, config)

    # -- reading ----------------------------------------------------------

    def _read_head(self, sock: socket.socket) -> bytes | None:
        """Read until the head is complete, or give up. Returns None on give-up.

        ``parse_request`` reports "incomplete head" as a ParseError, so this
        loop uses the blank-line test directly rather than calling the parser
        twice per chunk.
        """
        chunks: list[bytes] = []
        total = 0
        while True:
            try:
                chunk = sock.recv(_READ_CHUNK)
            except (socket.timeout, TimeoutError):
                return None
            except OSError:
                return None
            if not chunk:
                return None
            chunks.append(chunk)
            total += len(chunk)
            data = b"".join(chunks)
            if b"\r\n\r\n" in data or b"\n\n" in data:
                # Body bytes may follow; read what Content-Length promises.
                return self._read_body(sock, data)
            if total > MAX_HEADER_BYTES:
                self._send(sock, "431 Request Header Fields Too Large",
                           b"<p>header too large</p>")
                return None

    def _read_body(self, sock: socket.socket, data: bytes) -> bytes:
        """Top up ``data`` so the declared body is complete, or return as-is."""
        head_end = data.find(b"\r\n\r\n")
        skip = 4
        if head_end < 0:
            head_end = data.find(b"\n\n")
            skip = 2
        head = data[:head_end]
        have = len(data) - head_end - skip
        declared = _content_length(head)
        if declared is None or declared <= have:
            return data
        if declared > MAX_BODY_BYTES:
            # Do not buffer it; the parser will mark the body skipped and the
            # plan will carry ``body_unavailable``.
            return data
        while have < declared:
            try:
                chunk = sock.recv(min(_READ_CHUNK, declared - have))
            except (socket.timeout, TimeoutError, OSError):
                break
            if not chunk:
                break
            data += chunk
            have += len(chunk)
        return data

    # -- writing ----------------------------------------------------------

    def _send(self, sock: socket.socket, status: str, body: bytes) -> None:
        head = (f"HTTP/1.1 {status}\r\n"
                f"Content-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n"
                f"Cache-Control: no-store\r\n\r\n").encode("ascii")
        try:
            sock.sendall(head + body)
        except OSError:
            pass                                  # client went away; nothing to do

    def _forward(self, sock: socket.socket, message: RequestMessage,
                 config: ProxyConfig) -> None:
        """Relay the request upstream and stream the response back.

        The head is forwarded byte-for-byte from ``message.head``: re-serialising
        parsed headers would normalise casing, drop duplicates and reorder, and
        some servers care. The body is forwarded as received.
        """
        host = message.host
        if not host:
            self._send(sock, "400 Bad Request", b"<p>no host</p>")
            return
        port = message.port or (443 if message.is_tls else 80)

        upstream_host = host.strip("[]")
        connect_port = port
        if config.upstream_override is not None:
            upstream_host, connect_port = config.upstream_override
        try:
            upstream = socket.create_connection((upstream_host, connect_port),
                                                timeout=config.timeout)
        except OSError:
            self._send(sock, "502 Bad Gateway",
                       b"<p>cannot reach the requested host</p>")
            return

        try:
            upstream.settimeout(config.timeout)
            upstream.sendall(message.head)
            body = _body_bytes(message)
            if body:
                upstream.sendall(body)
            self._relay_response(sock, upstream, config, port,
                                 requested_port=message.port)
        except OSError:
            pass
        finally:
            try:
                upstream.close()
            except OSError:
                pass

    def _relay_response(self, sock: socket.socket, upstream: socket.socket,
                        config: ProxyConfig, port: int,
                        *, requested_port: int | None = None) -> None:
        """Read the upstream reply, inspect it, then forward or replace it.

        This is the half that ``p19`` says actually filters content: the keyword
        check runs on the *response* body, and a hit replaces the reply rather
        than resetting the connection.

        ``requested_port`` is what the client asked for (``80`` for a plain
        ``http://host/``), which is what the port gate must test. ``port`` is the
        port the proxy actually connected to, which for a test upstream is an
        ephemeral one - testing that against ``HTTP_PORTS`` would mean inspecting
        nothing, ever.

        The whole body is buffered before forwarding. That costs memory a
        streaming proxy would not spend, and it is deliberate: you cannot
        keyword-match bytes you have already sent, and sending them first then
        discovering a match would mean the child has the page already.
        """""
        from .inbound import (MAX_INSPECT_BYTES, inspect_body,
                              parse_response_head, replacement_response)

        collected = bytearray()
        head = None
        while True:
            try:
                chunk = upstream.recv(_READ_CHUNK)
            except (socket.timeout, TimeoutError):
                break
            except OSError:
                break
            if not chunk:
                break
            collected += chunk
            if head is None:
                head = parse_response_head(bytes(collected))
            # Stop buffering once the declared body is complete; anything after
            # that is a second response on a keep-alive connection, which this
            # proxy does not reuse anyway.
            if head is not None and head.content_length is not None:
                if len(collected) - len(head.head) >= head.content_length:
                    break
            if len(collected) > MAX_INSPECT_BYTES + 65536:
                break

        if head is None:
            # Could not parse the reply: forward it verbatim rather than
            # guessing. The caller sees a normal response and no claim is made
            # about having inspected it.
            sock.sendall(bytes(collected))
            return

        body = bytes(collected[len(head.head):])
        inspection = inspect_body(config.filter, head, body,
                                  port=requested_port or port)
        if config.on_inspection is not None:
            config.on_inspection(inspection)

        if inspection.blocked:
            from .policy import Outcome, block_page

            # The inbound block has no flow plan of its own - the request was
            # allowed and only the reply was filtered - so the page is built
            # from a plan-less outcome. ``limits`` is empty because the
            # inspection did happen; the limit, if any, is in the inspection.
            fake = Outcome(action="block", reason=inspection.reason, plan=None)
            page = block_page(fake)
            sock.sendall(replacement_response(inspection, page))
            return

        sock.sendall(bytes(collected))


def _content_length(head: bytes) -> int | None:
    """Content-Length from a raw head, or None when absent/unparseable."""
    for line in head.replace(b"\r\n", b"\n").split(b"\n")[1:]:
        name, sep, value = line.partition(b":")
        if not sep:
            continue
        if name.strip().lower() == b"content-length":
            text = value.strip()
            if text.isdigit():
                return int(text)
            return None
    return None


def _body_bytes(message: RequestMessage) -> bytes:
    """The body to forward, re-encoded from what the parser decoded.

    Only reached for requests that were not blocked. A body the parser refused
    to decode (chunked, oversized) is forwarded as the raw bytes it arrived as,
    which is why ``message.body is None`` does not mean "no body".
    """
    if message.body is None:
        return b""
    declared = _content_length(message.head)
    raw = message.body.encode("utf-8", "surrogateescape")
    if declared is not None and len(raw) > declared:
        return raw[:declared]
    return raw


class ProxyServer(socketserver.ThreadingTCPServer):
    """Threaded TCP listener. One thread per connection, daemon threads.

    Threaded because a single-threaded proxy is a single-threaded browser: one
    slow upstream blocks every other tab. Daemon threads because the server must
    be stoppable from a test without waiting for a hung connection to time out.
    """

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, config: ProxyConfig) -> None:
        self.config = config
        super().__init__(config.bind, _Handler)
        config.port = self.server_address[1]

    def handle_error(self, request, client_address) -> None:  # noqa: ANN001
        """Swallow per-connection errors.

        A proxy that dies because one client sent nonsense is a proxy a child
        can switch off. Errors are already logged by the handler where they
        matter; this override exists so ``socketserver`` does not print a
        traceback per malformed request.
        """


def serve_forever_in_thread(config: ProxyConfig) -> tuple[ProxyServer, threading.Thread]:
    """Start the server on a daemon thread and return it with the thread.

    Tests use this so a proxy can be exercised over a real loopback socket
    without a subprocess. The caller is responsible for ``shutdown()``; the
    helper in the tests does that in a ``finally``.
    """
    server = ProxyServer(config)
    thread = threading.Thread(target=server.serve_forever, daemon=True,
                              name="greendam-proxy")
    thread.start()
    return server, thread
