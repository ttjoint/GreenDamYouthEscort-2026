"""Inbound response inspection: the stage-2 content check that actually matters.

``analysis/probe/p19`` is unambiguous about how the original filtered content:

    inbound (DBFP_Recv)   keyword-match the HTTP *response body*,
                          then memcpy a preset page over the reply
    outbound (DBFP_Send)  extract a URL from the request, fingerprint the protocol

So the content check that blocks a page runs on the **response**, not the
request. A proxy that only inspects what the browser sends checks the wrong half:
a child typing ``example.com`` sends a request with no forbidden word in it, and
the page that arrives is what the filter is supposed to read.

Three consequences, all of which the engine already models and this module
honours:

**Port gate.** Keyword matching only ran on the HTTP ports (``p19``: 80, 81,
8080, 8081, 8888, 3128). :data:`green_dam.intercept.HTTP_PORTS` is the same set,
so the gate is the engine's, not a second copy.

**Binary bodies are skipped.** The original checked the first 10 bytes for high
bits (``p19`` step 4). :func:`green_dam.intercept.looks_binary` is the same test
expressed as "any non-printable byte", and it is called rather than reimplemented.

**Blocking replaces the reply.** Not a connection reset. The original memcpy'd a
preset page over the buffer, which is why a blocked page in this product is a
*page* and not a network error. :func:`replacement_response` builds that page.

The one thing this module refuses to do: inspect a body it could not fully read.
A truncated or oversized body produces ``checked=False`` and the reply is
forwarded unchanged - "we did not look" must never be recorded as "we looked and
it was clean".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "ResponseHead",
    "Inspection",
    "parse_response_head",
    "inspect_body",
    "replacement_response",
    "MAX_INSPECT_BYTES",
]

#: Bodies above this are forwarded without inspection. Same order of magnitude
#: as the request limit in ``message``: enough for any page, small enough that a
#: hostile server cannot make the proxy buffer without bound.
MAX_INSPECT_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class ResponseHead:
    """The status line and headers of an upstream response."""

    version: str
    status: str
    headers: dict[str, str]
    #: Raw head bytes, for forwarding unchanged when nothing is blocked.
    head: bytes = b""
    #: Body bytes that arrived in the same read as the head.
    body_prefix: bytes = b""

    @property
    def content_length(self) -> int | None:
        value = self.headers.get("content-length")
        if value is None or not value.strip().isdigit():
            return None
        return int(value.strip())

    @property
    def is_textual(self) -> bool:
        """Whether the declared type is something worth keyword-matching.

        A missing ``Content-Type`` is treated as textual, because the binary
        sniff is the authoritative test and it runs anyway. Declaring
        ``image/*`` or ``video/*`` is a cheap early exit; it is not a security
        decision, since a lying server would simply be caught by the sniff.
        """
        ctype = self.headers.get("content-type", "").lower()
        if not ctype:
            return True
        for prefix in ("image/", "video/", "audio/", "font/",
                       "application/octet-stream", "application/zip",
                       "application/pdf"):
            if ctype.startswith(prefix):
                return False
        return True


@dataclass(frozen=True)
class Inspection:
    """The result of looking at one response body.

    ``checked`` is the field that matters: False means the body was not
    inspected, and the caller must not treat the reply as clean.
    """

    checked: bool
    blocked: bool = False
    reason: str = ""
    #: Why inspection did not happen, when ``checked`` is False.
    limit: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"checked": self.checked, "blocked": self.blocked,
                "reason": self.reason, "limit": self.limit}


def parse_response_head(data: bytes) -> ResponseHead | None:
    """Split an upstream response into head and any body that came with it.

    Returns None when the head is not yet complete, which is the signal to read
    more - the same convention as ``message.parse_request``.
    """
    marker = data.find(b"\r\n\r\n")
    skip = 4
    if marker < 0:
        marker = data.find(b"\n\n")
        skip = 2
        if marker < 0:
            return None

    head = data[:marker]
    body_prefix = data[marker + skip:]
    lines = head.replace(b"\r\n", b"\n").split(b"\n")
    if not lines or not lines[0].strip():
        return None

    status_line = lines[0].decode("latin-1")
    parts = status_line.split(None, 2)
    if len(parts) < 2 or not parts[0].upper().startswith("HTTP/"):
        return None
    version = parts[0].split("/", 1)[1]
    status = parts[1]

    headers: dict[str, str] = {}
    for raw in lines[1:]:
        name, sep, value = raw.partition(b":")
        if not sep:
            continue
        headers[name.strip().decode("latin-1").lower()] = \
            value.strip().decode("latin-1")

    return ResponseHead(version=version, status=status, headers=headers,
                        head=head + data[marker:marker + skip],
                        body_prefix=body_prefix)


def inspect_body(filter_, head: ResponseHead, body: bytes,
                 *, port: int) -> Inspection:
    """Keyword-match a response body against list C, exactly as ``p19`` did.

    The engine's own chain is reused via ``check_request`` with
    ``direction="inbound"``: it already applies the port gate, the binary sniff,
    and list C's rule semantics. Reimplementing any of those here would be a
    second copy of rules that were verified against the vendor binary.

    ``port`` is the port the *client asked for*, not the port of whatever socket
    the proxy happened to open upstream. The vendor's gate was on the connection's
    destination port; a proxy that opens a short-lived connection to an arbitrary
    ephemeral port would otherwise never pass the gate and would silently inspect
    nothing. Callers get this right by passing ``RequestMessage.port``.
    """
    from green_dam.intercept import HTTP_PORTS, looks_binary   # noqa: PLC0415
    from green_dam.intercept import Request                    # noqa: PLC0415

    if not head.is_textual:
        return Inspection(checked=False, limit="non_textual_type")

    if port not in HTTP_PORTS:
        # The original only ran keyword matching on these ports (p19 step 6).
        # Skipping is correct; claiming a check happened would not be.
        return Inspection(checked=False, limit="port_not_filtered")

    if len(body) > MAX_INSPECT_BYTES:
        return Inspection(checked=False, limit="body_too_large")

    if head.content_length is not None and len(body) < head.content_length:
        return Inspection(checked=False, limit="body_truncated")

    if looks_binary(body[:10]):
        # A binary body cannot be keyword-filtered. The original skipped it too
        # (p19 step 4) and so does the engine's inbound stage.
        #
        # **But the original's test is wrong for Chinese.** It flags any byte in
        # 0x80-0xFE among the first ten, and every UTF-8 or GBK Chinese character
        # starts with such a byte. A response whose body begins with Chinese - a
        # JSON payload, a text/plain reply, an HTML fragment without a leading
        # tag - was therefore classified binary and skipped, which for a Chinese
        # product means skipping most of what it exists to read.
        #
        # The engine keeps the vendor's rule because it is evidence of what the
        # vendor did. The host does not have to reproduce a bug. The distinction
        # drawn here is narrower and defensible: bytes that *decode* as text under
        # the charset the server declared are text, regardless of their high bits.
        # A PNG still fails to decode as UTF-8 and still gets skipped.
        if not _decodes_as_text(body, head.headers.get("content-type", "")):
            return Inspection(checked=False, limit="binary_body")

    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        text = body.decode("latin-1")

    # ``prefix`` is deliberately **not** forwarded. The engine's inbound stage
    # runs its own ``looks_binary(prefix)`` check, and the vendor's version of
    # that check flags any high byte in the first ten - which is every Chinese
    # character. We have already made a better-founded decision above (decode
    # plus control-byte scan), so passing the prefix would re-impose the rule we
    # just argued against and skip the body anyway.
    decision = filter_.check_request(
        Request(direction="inbound", port=port, host=None, body=text))

    if getattr(decision, "blocked", False):
        return Inspection(checked=True, blocked=True,
                          reason=str(getattr(decision, "reason", "")
                                     or "keyword_rule"))
    return Inspection(checked=True, blocked=False)


def _decodes_as_text(body: bytes, content_type: str) -> bool:
    """True when ``body`` is plausibly text, judged by decoding rather than bits.

    This replaces the vendor's "high bit in the first ten bytes" test, which
    classifies every Chinese character as binary. See the call site for why the
    host deviates and the engine does not.

    Decoding alone is not enough: GBK accepts nearly any byte sequence, so a PNG
    "decodes" happily. The test therefore requires **both** that a declared or
    plausible charset decodes the sample *and* that the result contains no
    C0 control bytes other than tab/newline/carriage-return/formfeed. Binary
    formats are full of those; text is not.
    """
    sample = body[:4096]
    if not sample:
        return True
    if not any(b >= 0x80 for b in sample):
        # Pure ASCII. Still check for control bytes - a run of 0x00 is binary.
        return not _has_control_bytes(sample)

    candidates: list[str] = []
    match = _CHARSET.search(content_type or "")
    if match is not None:
        candidates.append(match.group(1).strip("\"' "))
    candidates.extend(["utf-8", "gbk"])

    for encoding in candidates:
        try:
            decoded = sample.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        if not _has_control_bytes(decoded.encode("utf-8")):
            return True
        # Decoded, but full of control characters: keep trying other charsets
        # rather than declaring text on the strength of GBK's permissiveness.
    return False


#: Bytes allowed to appear in text. Everything else below 0x20 is a control byte
#: and marks the payload as binary.
_TEXT_CONTROL_BYTES = frozenset({0x09, 0x0A, 0x0C, 0x0D, 0x1B})


def _has_control_bytes(data: bytes) -> bool:
    """True when ``data`` contains a C0 control byte that text would not have."""
    return any(b < 0x20 and b not in _TEXT_CONTROL_BYTES for b in data)


#: ``charset=`` in a Content-Type header. Quoted values are legal.
_CHARSET = re.compile(r"charset\s*=\s*([^;\s]+)", re.IGNORECASE)


def replacement_response(inspection: Inspection, page: bytes,
                         *, status: str = "403 Forbidden") -> bytes:
    """The response served instead of the blocked page.

    This is the ``memcpy`` of ``p19`` step 9, expressed as a complete response:
    the upstream's own headers are discarded rather than merged, because keeping
    them would preserve the upstream's ``Content-Length`` and the browser would
    hang waiting for bytes that are never coming.
    """
    return (f"HTTP/1.1 {status}\r\n"
            f"Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(page)}\r\n"
            f"Connection: close\r\n"
            f"Cache-Control: no-store\r\n\r\n").encode("ascii") + page
