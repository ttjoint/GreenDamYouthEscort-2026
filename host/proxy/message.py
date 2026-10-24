"""HTTP/1.x message parsing, without sockets.

The proxy must read a request line, a header block, and (for content stages) a
body, then hand a :class:`~green_dam.intercept.Request` to the engine. It must
also do this on hostile input: a child who knows what the proxy is can send a
malformed request line, a header block that never ends, a body longer than the
declared length, or a ``Content-Length`` that disagrees with the actual body.

This module parses and nothing else. It does not decide, does not forward, and
does not raise on garbage - it returns a :class:`ParseError` value that the
server turns into a 400. Parsing code that raises on hostile input is parsing
code that crashes the proxy on demand.

What is deliberately *not* supported:

``chunked`` request bodies
    A ``Transfer-Encoding: chunked`` body is read as opaque bytes and not
    inspected. Reassembling chunks to look at them would mean buffering an
    unbounded stream in a process a child controls; the honest move is to
    declare the limit (``LIMIT_NO_BODY``) rather than claim an inspection that
    did not happen. This matches the engine's existing stance on QUIC.

``HTTP/2`` and above
    Their framing is binary and does not start with a method token. They are
    reported as ``unknown_protocol``, which is a stage-1-only plan.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "MAX_HEADER_BYTES",
    "MAX_BODY_BYTES",
    "ParseError",
    "RequestMessage",
    "parse_request",
    "strip_port",
]

#: Header blocks larger than this are rejected rather than buffered. A header
#: bomb is the cheapest way to exhaust a proxy, and 64 KiB is far above any real
#: request (nginx's default is 8 KiB).
MAX_HEADER_BYTES = 64 * 1024

#: Bodies larger than this are not inspected. 8 MiB covers every page a content
#: filter needs to read; beyond that the plan declares ``body_unavailable``
#: instead of buffering an unbounded stream.
MAX_BODY_BYTES = 8 * 1024 * 1024

_REQUEST_LINE = re.compile(
    rb"^(?P<method>[A-Z]{3,16})[ \t]+"
    rb"(?P<target>[^ \t\r\n]+)[ \t]+"
    rb"HTTP/(?P<version>\d\.\d)[ \t]*\r?\n?$")


class ParseError(str):
    """Why a request could not be parsed. A string, so it logs verbatim.

    Subclassing ``str`` rather than ``Exception`` is deliberate: the caller's
    only useful response is a 400 with a reason, not a traceback. Returning it
    keeps parsing total - every byte string produces either a message or a
    reason, never an exception.
    """

    __slots__ = ()


@dataclass(frozen=True)
class RequestMessage:
    """One parsed HTTP request head, plus whatever body was read.

    ``target`` is the raw request target (``/path?q=1`` for origin-form,
    ``http://host/path`` for absolute-form). ``host`` is resolved from the
    target when absolute, otherwise from the ``Host`` header - because that is
    what the engine's host stage needs, and doing it here means the server never
    has to know the precedence rules.
    """

    method: str
    target: str
    version: str
    headers: dict[str, str] = field(default_factory=dict)
    #: Bytes of the head, exactly as received, for verbatim forwarding.
    head: bytes = b""
    #: Decoded body when one was read, else None.
    body: str | None = None
    #: True when the body was present but deliberately not decoded.
    body_skipped: bool = False

    @property
    def host(self) -> str | None:
        """Destination host, from the absolute target if present, else Host."""
        if "://" in self.target:
            rest = self.target.split("://", 1)[1]
            authority = rest.split("/", 1)[0].split("?", 1)[0]
            return strip_port(authority).lower() or None
        header = self.headers.get("host")
        if not header:
            return None
        return strip_port(header.strip()).lower() or None

    @property
    def port(self) -> int | None:
        """Destination port, from the authority if it names one."""
        authority = None
        if "://" in self.target:
            rest = self.target.split("://", 1)[1]
            authority = rest.split("/", 1)[0].split("?", 1)[0]
        elif self.headers.get("host"):
            authority = self.headers["host"].strip()
        if not authority:
            return None
        _, _, port_text = authority.rpartition(":")
        if port_text.isdigit():
            return int(port_text)
        return None

    @property
    def is_tls(self) -> bool:
        """True when the request target declares https.

        Only meaningful for absolute-form targets, which is what a proxy sees.
        A CONNECT is handled by the server before this class exists.
        """
        return self.target.lower().startswith("https://")

    @property
    def path(self) -> str:
        """The origin-form path, for the block page's "you asked for" line."""
        if "://" not in self.target:
            return self.target
        rest = self.target.split("://", 1)[1]
        slash = rest.find("/")
        return rest[slash:] if slash >= 0 else "/"


def strip_port(authority: str) -> str:
    """Remove a ``:port`` suffix from an authority, leaving IPv6 intact.

    ``[::1]:8080`` -> ``[::1]``; ``example.com:80`` -> ``example.com``;
    ``example.com`` -> ``example.com``. A naive ``split(":")`` breaks IPv6,
    which is exactly the case that gets forgotten until it is a bug report.
    """
    if authority.startswith("["):
        end = authority.find("]")
        if end < 0:
            return authority
        return authority[:end + 1]
    if authority.count(":") == 1:
        name, _, port = authority.partition(":")
        return name if port.isdigit() else authority
    return authority


def _split_head(data: bytes) -> tuple[bytes, bytes] | None:
    """Split a head from its body at the first blank line.

    Returns ``None`` when the head is not yet complete, which is the signal to
    read more bytes - not an error.
    """
    marker = data.find(b"\r\n\r\n")
    if marker < 0:
        marker = data.find(b"\n\n")
        if marker < 0:
            return None
        return data[:marker + 2], data[marker + 2:]
    return data[:marker + 4], data[marker + 4:]


def parse_request(data: bytes, *,
                  max_header: int = MAX_HEADER_BYTES,
                  max_body: int = MAX_BODY_BYTES) -> RequestMessage | ParseError:
    """Parse one request from ``data``. Never raises.

    The returned message carries the raw head so the server can forward it
    byte-for-byte. Re-serialising parsed headers would silently normalise
    casing, drop duplicates and reorder - all of which some servers care about.
    """
    if len(data) > max_header + max_body:
        return ParseError(f"request exceeds {max_header + max_body} bytes")

    split = _split_head(data)
    if split is None:
        if len(data) > max_header:
            return ParseError(f"header block exceeds {max_header} bytes")
        # A head that never completes *and* whose first line is not printable
        # ASCII is not a slow request, it is not HTTP at all: a TLS ClientHello
        # or HTTP/2 preface. Reporting "incomplete" there would be true but
        # useless - the caller would wait for bytes that will never come.
        first = data.split(b"\n", 1)[0][:16]
        if first and any(b not in range(0x20, 0x7F) and b not in (0x09, 0x0D)
                         for b in first):
            return ParseError("not an HTTP/1.x request")
        return ParseError("incomplete head")
    head, rest = split

    if len(head) > max_header:
        return ParseError(f"header block exceeds {max_header} bytes")

    lines = head.replace(b"\r\n", b"\n").split(b"\n")
    request_line = lines[0]
    match = _REQUEST_LINE.match(request_line + b"\n")
    if match is None:
        # A binary first line means this is not HTTP/1.x at all - HTTP/2
        # preface, TLS ClientHello, or something hostile.
        if any(b not in range(0x20, 0x7F) and b not in (0x09, 0x0D, 0x0A)
               for b in request_line[:16]):
            return ParseError("not an HTTP/1.x request")
        return ParseError("malformed request line")

    headers: dict[str, str] = {}
    for raw in lines[1:]:
        if not raw.strip():
            continue
        name, sep, value = raw.partition(b":")
        if not sep:
            return ParseError("malformed header line")
        key = name.strip().decode("latin-1").lower()
        headers[key] = value.strip().decode("latin-1")

    method = match.group("method").decode("ascii")
    target = match.group("target").decode("latin-1")
    version = match.group("version").decode("ascii")

    read = _read_body(headers, rest, max_body)
    if isinstance(read, ParseError):
        return read
    # ``_read_body`` returns ``(body, skipped)``. Unpacking it directly, without
    # the isinstance guard above, turned a malformed Content-Length into a
    # ValueError that escaped the "never raises" contract.
    body, skipped = read

    return RequestMessage(method=method, target=target, version=version,
                          headers=headers, head=head, body=body,
                          body_skipped=skipped)


def _read_body(headers: dict[str, str], rest: bytes,
               max_body: int) -> tuple[str | None, bool] | ParseError:
    """Extract the body, honouring Content-Length and refusing chunked.

    Returns ``(body, skipped)``. ``skipped`` is True when a body exists but was
    not decoded - the caller turns that into ``body_unavailable`` rather than
    letting the engine believe the body was empty.
    """
    encoding = headers.get("transfer-encoding", "").lower()
    if "chunked" in encoding:
        # Reassembling an unbounded stream to inspect it is not worth the memory
        # a child could make us allocate. Declared as a limit instead.
        return None, True

    declared = headers.get("content-length")
    if declared is None:
        return None, False
    if not declared.strip().isdigit():
        return ParseError("non-numeric Content-Length")

    length = int(declared.strip())
    if length > max_body:
        return None, True
    if len(rest) < length:
        # Truncated body: do not pretend we saw it.
        return None, True

    raw = rest[:length]
    try:
        return raw.decode("utf-8"), False
    except UnicodeDecodeError:
        # Not text. The engine's own binary sniff decides what that means; an
        # undecodable body must not crash the proxy.
        return raw.decode("latin-1"), False
