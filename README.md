# GreenDamYouthEscort-2026

A ground-up reimplementation of the *Green Dam Youth Escort* / 绿坝·花季护航
parental-control feature set, targeting current Windows.

This is **not** a port and **not** a patch of the 2009 binaries. Nothing from
the original package is redistributed here, and none of the original code,
mechanisms or obfuscation is carried over. What is carried over is the part
that was actually worth keeping: the curated filter data and the notion of a
domain-specific semantic lexicon, both re-expressed as plain, auditable JSON.

The original product is documented for its historical and technical interest
elsewhere; this repository only contains the new implementation.

## Why a rewrite

The 2009 design cannot run on Windows 11 as built, and several of its choices
are actively harmful:

- **Layered Service Provider** network interception was removed from Windows
  Server 2012 onward.
- **Global CBT/keyboard hooks** injected into ~48 processes are blocked by
  UIPI and session isolation, and cannot reach 64-bit targets from a 32-bit
  hook DLL.
- **GDI `TextOut`/`ExtTextOut` inline hooks** miss most modern text now that
  DirectWrite does the rendering.
- A **kernel driver** plus a **PLX 9054 dongle check** are non-starters under
  driver signing enforcement and on hardware that no longer exists.
- Shipped security debt: hardcoded passwords, plaintext HTTP delivery of
  executables, plaintext upload of collected identity numbers, terminating
  security tools by name, and writing into `%WinDir%\System32`.

The rewrite targets the same user-visible outcomes with supported mechanisms,
and deliberately drops all of the above.

## Layout

```
docs/              design and migration notes
  architecture.md      module map and technical decisions
tools/             legacy import tooling (run once, then irrelevant)
data/              normalised filter data consumed by the new implementation
  legacy-imported/   JSON produced from the legacy data blobs
tests/             unit tests for codecs, parsers and data integrity
src/               the new implementation
```

## Imported data

`tools/import_legacy.py` converts the legacy data blobs into JSON once. See
`docs/legacy-data-import.md` for the full derivation, including how the
semantic lexicon container was recovered from its consumer.

| Output | Count |
|---|---|
| filter categories | 5 |
| trusted domains | 1,177 |
| filter rules (expanded terms) | 11,175 |
| monitored applications | 8 |
| semantic lexicon terms | 142,742 (8,469 with a severity class) |

The lexicon container was decoded from its consumer, not by pattern-matching:
see `docs/falunword-format.md`. One caveat matters for design: the file carries
terms and a 0..7 severity class, but **not** the original per-word
normal/blocked flag — that data was never shipped. The new implementation
rebuilds that mapping deliberately (see `docs/architecture.md`).

The legacy install tree is **not** in this repository. To regenerate the data
you supply your own copy:

```bash
python tools/import_legacy.py --legacy /path/to/legacy-tree
```

## Tests

```bash
python -m unittest discover -s tests
```

Structural tests for the lexicon container need the legacy binary and are
skipped unless you point at a tree:

```bash
GREEN_DAM_LEGACY_TREE=/path/to/legacy-tree python -m unittest discover -s tests
```

## Status

Data migration is complete and the architecture is settled in
`docs/architecture.md`. The control layers (network, semantic matching, image
classification, screen text, UI) are being built on top of this data under
`src/`.
