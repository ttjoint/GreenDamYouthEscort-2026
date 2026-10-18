#!/usr/bin/env python3
"""
FalunWord.lib decoder.

Recovered by disassembling the consumer ``CFWordLib::LoadLib`` and
``BuildWordIndexFromOrderedBuf`` in ``SentenceObj.dll``. ``LoadLib`` performs
this exact read sequence (``fread`` is the CRT wrapper at 0x1003719d):

    fread(&n1, 4, 1, fp)          -> +0x148 : chain-node count
    fread(buf1, 16, n1, fp)       -> +0x140 : chain-node array (n1 * 16 B)
    fread(buf2, 16, 0xFFFF, fp)   -> +0x14c : fixed scratch table (1 MiB)
    fread(&n2, 4, 1, fp)          -> +0x130 : sense-record count
    fread(buf3, 16, n2, fp)       -> +0x128 : sense records   (n2 * 16 B)
    fread(&n3, 4, 1, fp)          -> +0x13c : char-pool count
    fread(buf4, 2, n3, fp)        -> +0x134 : UTF-16 char pool (n3 * 2 B)

This accounting consumes the entire file except a 5-byte trailer ``HNCIT``.
Every section size closing exactly against the real file length is what
proves the segmentation - it is not a guess. ``verify_layout()`` re-checks it
on every run.

Chain nodes are 4 little-endian uint32:

    [0] code      Unicode code point
    [1] next      index of the following character IN THE SAME WORD, 0 = end
    [2] children  number of child words branching from here, 0 = leaf
    [3] sentinel  0xFFFFFFFE index node, 0xFFFFFFFF padding leaf,
                  any other value = a sense count for this node

The ``next`` field is a character path, not a sibling list. Following it from
a chain start spells a whole term:

    0 -> 8 -> 9 -> 10 -> 11 -> 12  ==  '6' '1' '0' '"' '歹' '徒'

A chain start is any node that no other node's ``next`` points at. Chains
were verified against the independent sense-record table (n2 entries), which
stores its own character offsets and lengths into the pool; both views agree
on terms such as ``610歹`` and ``610"歹徒``.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

SENT_INDEX = 0xFFFFFFFE
SENT_PAD = 0xFFFFFFFF
SCRATCH_RECORDS = 0xFFFF
TRAILER = b"HNCIT"


class Layout:
    def __init__(self, blob: bytes) -> None:
        self.blob = blob
        self.n1 = struct.unpack_from("<I", blob, 0)[0]
        off = 4
        self.chain = [
            struct.unpack_from("<4I", blob, off + i * 16) for i in range(self.n1)
        ]
        off += self.n1 * 16

        self.scratch_off = off
        off += SCRATCH_RECORDS * 16

        self.n2 = struct.unpack_from("<I", blob, off)[0]
        off += 4
        self.senses = [
            struct.unpack_from("<4I", blob, off + i * 16) for i in range(self.n2)
        ]
        off += self.n2 * 16

        self.n3 = struct.unpack_from("<I", blob, off)[0]
        off += 4
        self.pool = [
            struct.unpack_from("<H", blob, off + i * 2)[0] for i in range(self.n3)
        ]
        off += self.n3 * 2

        self.consumed = off
        self.trailer = blob[off:]

    def verify(self) -> bool:
        return self.trailer == TRAILER and self.consumed + len(TRAILER) == len(self.blob)

    def pool_text(self, start: int, length: int) -> str:
        return "".join(
            chr(self.pool[start + k]) for k in range(length) if start + k < len(self.pool)
        )


def iter_chains(lay: Layout) -> list[dict]:
    """Spell out every term by following the ``next`` character path."""
    chain = lay.chain
    n = len(chain)

    targeted = bytearray(n)
    for _, nxt, _, _ in chain:
        if 0 < nxt < n:
            targeted[nxt] = 1

    seen = bytearray(n)
    terms: list[dict] = []
    for start in range(n):
        if targeted[start] or seen[start]:
            continue
        chars: list[str] = []
        senses = 0
        k = start
        while 0 <= k < n and not seen[k]:
            seen[k] = 1
            code, nxt, _children, sentinel = chain[k]
            if 0 < code < 0x110000:
                chars.append(chr(code))
            if sentinel not in (SENT_INDEX, SENT_PAD):
                senses += 1
            if nxt == 0:
                break
            k = nxt
        word = "".join(chars)
        if word:
            terms.append({"word": word, "start": start, "senses": senses})
    return terms


def decode_sense_table(lay: Layout) -> list[dict]:
    """
    Second, independent view of the lexicon.

    Sense records are ``[char_offset][unused][unused][char_count]``; the first
    field indexes the UTF-16 pool. Cross-checking the two views is what
    validated the chain interpretation.
    """
    out = []
    for i, (start, _a, _b, length) in enumerate(lay.senses):
        if length == 0 or length > 64:
            continue
        out.append({"index": i, "text": lay.pool_text(start, length), "length": length})
    return out


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("lib", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--txt", type=Path)
    args = ap.parse_args()

    blob = args.lib.read_bytes()
    lay = Layout(blob)
    ok = lay.verify()

    print(f"file          : {len(blob)} B")
    print(f"chain nodes   : {lay.n1}")
    print(f"sense records : {lay.n2}")
    print(f"pool chars    : {lay.n3}")
    print(f"consumed      : {lay.consumed} + trailer {lay.trailer!r}")
    print(f"layout OK     : {ok}")
    if not ok:
        print("WARNING: layout did not close exactly; results are suspect")

    terms = iter_chains(lay)
    senses = decode_sense_table(lay)
    print(f"terms (chain) : {len(terms)}")
    print(f"senses (table): {len(senses)}")

    if args.txt:
        args.txt.write_text("\n".join(t["word"] for t in terms) + "\n", encoding="utf-8")
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "source": args.lib.name,
                    "layout": {
                        "chain_nodes": lay.n1,
                        "sense_records": lay.n2,
                        "pool_chars": lay.n3,
                        "trailer": lay.trailer.decode("ascii", "replace"),
                        "layout_verified": ok,
                    },
                    "term_count": len(terms),
                    "terms": terms,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
