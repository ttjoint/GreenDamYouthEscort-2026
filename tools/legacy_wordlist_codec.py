"""
Legacy data importer — stage 1: obfuscated wordlist codec.

The 3.17 packages ship word lists in a fixed 2:1 character obfuscation
(no key, no real encryption). This module decodes them into plain UTF-8
text for the new implementation.

Format
------
Ciphertext is plain printable ASCII restricted to 0x30..0x6F (64 symbols).
Each plaintext byte is encoded as two symbols -> 12 bits::

    V = (v0 << 6) | v1          v = byte - 0x30

Only 8 of the 12 bits carry data, and 4 of them are inverted::

    plain bit 0 <- V[0]          plain bit 4 <- V[6]
    plain bit 1 <- V[1]          plain bit 5 <- V[7]
    plain bit 2 <- 1 - V[2]      plain bit 6 <- 1 - V[8]
    plain bit 3 <- 1 - V[3]      plain bit 7 <- 1 - V[9]

    V[4], V[5], V[10], V[11] are padding (ignored)

Lines are separated by CRLF, which is preserved verbatim and not encoded.
Payload text encoding is GBK.
"""

from __future__ import annotations

SYM_BASE = 0x30
_CRLF = b"\r\n"


def _decode_pair(v0: int, v1: int) -> int:
    v = (v0 << 6) | v1
    bit = lambda k: (v >> k) & 1  # noqa: E731
    return (
        bit(0)
        | bit(1) << 1
        | (1 - bit(2)) << 2
        | (1 - bit(3)) << 3
        | bit(6) << 4
        | bit(7) << 5
        | (1 - bit(8)) << 6
        | (1 - bit(9)) << 7
    )


def _encode_pair(ch: int) -> bytes:
    b = lambda k: (ch >> k) & 1  # noqa: E731
    v = (
        b(0)
        | b(1) << 1
        | (1 - b(2)) << 2
        | (1 - b(3)) << 3
        | b(4) << 6
        | b(5) << 7
        | (1 - b(6)) << 8
        | (1 - b(7)) << 9
    )
    return bytes([SYM_BASE + ((v >> 6) & 0x3F), SYM_BASE + (v & 0x3F)])


def is_obfuscated(data: bytes) -> bool:
    """True if *data* uses the 64-symbol obfuscation alphabet."""
    body = data.replace(_CRLF, b"")
    return bool(body) and all(0x30 <= x <= 0x6F for x in body)


def decode_bytes(data: bytes) -> bytes:
    """Decode obfuscated payload to raw plaintext bytes (still GBK)."""
    out = bytearray()
    lines = data.split(_CRLF)
    for n, line in enumerate(lines):
        for i in range(0, len(line) - 1, 2):
            out.append(_decode_pair(line[i] - SYM_BASE, line[i + 1] - SYM_BASE))
        if n != len(lines) - 1:
            out += _CRLF
    return bytes(out)


def decode_text(data: bytes) -> str:
    """Decode obfuscated payload to a str (GBK source)."""
    return decode_bytes(data).decode("gbk", errors="replace")


def encode_bytes(plain: bytes) -> bytes:
    """Inverse of :func:`decode_bytes`; kept for round-trip tests."""
    out = bytearray()
    lines = plain.split(_CRLF)
    for n, line in enumerate(lines):
        for ch in line:
            out += _encode_pair(ch)
        if n != len(lines) - 1:
            out += _CRLF
    return bytes(out)
