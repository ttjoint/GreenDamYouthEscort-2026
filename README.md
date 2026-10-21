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
  status-checklist.md  what is implemented / todo / untested, per module
analysis/          reverse-engineering write-ups of the original product
                     (master ledger, facts, corrections, spec; see analysis/README.md)
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

The lexicon's character pool turned out to be a **single continuous corpus
stream** (36,943 characters, pinyin-ordered) rather than an index. The 4,692
sense records are `(offset, length, level)` triples over that stream, and their
offsets are 100% monotonically increasing. The same string can carry several
levels because its level comes from context, not from the word itself.

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

Data migration is complete, the architecture is settled in
`docs/architecture.md`, and the semantic matcher is running under `src/`:
10,182 matchable terms, measured against a purpose-built corpus
(`docs/semantic-corpus.md`).

The HNC sentence-classification layer is reversed and verified
(`docs/hnc-engine.md`). The classifier at `0x10030650` is reproduced in
`src/green_dam/hnc.py` and checked against the original binary by running it
under Unicorn: **974 inputs, zero mismatches**. That harness caught four real
mistakes that static reading had missed, including a jump table read in
reverse and a nested condition dismissed as redundant.

The ceiling is documented rather than hidden. `SENTEN.LIB`, `TYPE.LIB`,
`XU.LIB` and `rule.lib` were never shipped, so sentence-class *codes* are
recoverable but their *names* are not. The engine in the package ran
half-loaded, and the HNC layer in `src/` is a reference baseline, not a runtime
dependency.

Two expectations were refuted by the data during that work and are documented
rather than papered over: `hong_hei` is not a self-consistent severity grading
(core terms such as 九评 are all level 0), and the original per-word
normal/blocked flag was never shipped. Both are pinned by tests so the docs
cannot drift from the code.

Next: M8 policy engine, then M2 network interception. Reversing is finished;
building starts now.

### M8 progress

The legacy 67-slot policy array is now decoded and wired in
(`src/green_dam/sysdat.py`, `tests/test_sysdat.py`). Slot names come from the
GBK table in `XNet2.exe`; the `index = slot_no - 1` mapping is proven by the
`rep movsd` at `0x40bc16`, not inferred. Only slots 1..25 are named - the rest
are reserved, always zero, and deliberately left unnamed.

The array gates behaviour only: slots 2 and 10 are attested off-switches for
filtering and keyword filtering. It supplies no action mapping, because the
vendor's score-to-action semantics are still unknown. A gate silences evidence
and never manufactures an `allow`; a test pins that.

One prior claim was corrected rather than carried forward: slot 17 is
`allow_auto_screenshot`, not `allow_auto_shutdown` (the latter came from
mixing in the log-event numbering). The import tool now takes slot names from
the module so the data cannot drift from the evidence again.